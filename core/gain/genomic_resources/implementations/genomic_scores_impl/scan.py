from __future__ import annotations

import functools
from collections.abc import Callable, Generator, Iterable
from typing import Any, NamedTuple, TypeVar, cast

import numpy as np

from gain import logging
from gain.genomic_resources.cli_errors import report_resource_failure
from gain.genomic_resources.genomic_scores import (
    AlleleScore,
    GenomicScore,
    RecordArrays,
    build_score_from_resource,
    clip_span,
    owned_records_mask,
    owns_record,
)
from gain.genomic_resources.histogram import (
    CategoricalHistogram,
    CategoricalHistogramConfig,
    Histogram,
    HistogramConfig,
    HistogramError,
    NullHistogram,
    NullHistogramConfig,
    NumberHistogram,
    NumberHistogramConfig,
    build_default_histogram_conf,
    build_empty_histogram,
)
from gain.genomic_resources.repository import (
    GenomicResource,
)
from gain.genomic_resources.resource_errors import (
    MalformedResourceError,
)
from gain.genomic_resources.resource_types import (
    equivalent_resource_types,
)
from gain.genomic_resources.score_def import ScoreValue
from gain.genomic_resources.score_implementation import (
    ScoreImplementationBase,
)
from gain.genomic_resources.statistics.alleles import (
    RegionAlleles,
    allele_arrays_folded_into,
    merge_region_alleles,
    records_folded_into,
    region_alleles_for,
    save_allele_statistics,
    serves_allele_arrays,
)
from gain.genomic_resources.statistics.coverage import (
    RegionCoverage,
    accumulate_coverage,
    merge_region_coverage,
    normalize_values,
    save_and_plot_coverage,
)
from gain.genomic_resources.statistics.min_max import MinMaxValue

logger = logging.getLogger(__name__)

#: What this module publishes.  Stated rather than left to be inferred
#: from which names happen to lack a leading underscore.
#:
#: Four kinds of thing, sorted here but worth naming: the task bodies
#: ``impl``'s task graph schedules (``do_*_task``,
#: ``do_noregion_histograms``, ``merge_min_max``,
#: ``merge_and_save_histograms``); the two passes and their vectorized
#: twins (``scan_region``, ``do_histogram``/``do_min_max`` and the
#: ``*_bulk`` pair, over the shared ``bulk_region_scan`` driver); the
#: gates that choose between them (``bulk_scan_eligible``,
#: ``can_bulk_*``); and the config and merge steps.
#:
#: Everything absent is a per-batch inner step (``_accumulate_arrays``,
#: ``_select_and_weigh``, ...) that no caller outside this module has any
#: business reaching for.
__all__ = [
    "RegionScanResult",
    "bulk_region_scan",
    "bulk_scan_eligible",
    "can_bulk_histogram",
    "can_bulk_min_max",
    "do_histogram",
    "do_histogram_bulk",
    "do_histogram_task",
    "do_min_max",
    "do_min_max_bulk",
    "do_min_max_task",
    "do_noregion_histograms",
    "merge_and_save_histograms",
    "merge_histograms",
    "merge_min_max",
    "scan_region",
    "unpack_score_defs",
    "update_hist_confs",
]

# The per-batch accumulator target of a bulk region scan -- a Histogram for the
# histogram pass, a MinMaxValue for the min/max pass.  A TypeVar keeps
# ``bulk_region_scan`` generic without losing either caller's dict type.
_AccT = TypeVar("_AccT")

# The resource kinds whose statistics the vectorized scan may serve, in every
# spelling of each.  Expanded through ``equivalent_resource_types`` rather than
# written out: a fragment score has TWO accepted type strings
# (``fragment_score`` and the deprecated ``cnv_collection``, gain#471), and
# a literal set
# naming only one of them would send the other silently back to the per-record
# path -- no error, no failing test, just the slow path forever.
_BULK_SCAN_RESOURCE_TYPES = frozenset(
    spelling
    for resource_type in ("position_score", "allele_score", "fragment_score")
    for spelling in equivalent_resource_types(resource_type)
)

# The kinds whose rows have a span coverage can union: position scores and
# fragment scores (in both spellings).  Allele scores are deliberately out:
# their rows collapse to points and their coverage is a DISTINCT-position
# count with its own slice (gain#777), not this union.
_COVERAGE_SCAN_RESOURCE_TYPES = frozenset(
    spelling
    for resource_type in ("position_score", "fragment_score")
    for spelling in equivalent_resource_types(resource_type)
)

# The kinds whose rows are PAIRWISE DISJOINT -- no two share a position.
# Position scores, whose ``validate_records`` refuses a row beginning at
# or before its predecessor's end, on raw spans.  (Adjacent rows are
# legal and common, and the segment algebra depends on it, so this is
# deliberately not phrased as "cannot touch": ADR 0020 and
# ``add_interval`` use touching for exactly that adjacency.)
#
# Named for the FACT rather than for one of its consequences, because it
# has two: disjoint rows have an exact run algebra, so their segment
# statistics are published; and they cannot double-count a position, so
# the scan hands their coverage full unclipped spans.  Fragment rows
# overlap, so they get neither (their counts are their own statistic --
# gain#794).
_NON_OVERLAPPING_ROW_RESOURCE_TYPES = frozenset(
    equivalent_resource_types("position_score"))

# The kinds whose rows ARE fragments, in both spellings, and so publish
# a fragment count and fragment-length histogram (gain#794).  A separate
# statement from the one above rather than its complement: that these
# are exactly the coverage-scanned kinds that overlap is true today and
# incidental -- a future overlapping kind that is not a fragment score
# would inherit fragment counts it has no business publishing.
_FRAGMENT_STATISTICS_RESOURCE_TYPES = frozenset(
    equivalent_resource_types("fragment_score"))


def _score_for(
    resource: GenomicResource,
    score: GenomicScore | None,
) -> GenomicScore:
    """The score this call reads: the caller's, or one built for it.

    The one place the ``score`` parameter threaded through this module is
    honoured, so that "a caller may hand its own score down" is stated
    once instead of at each of the six sites that would otherwise write
    the same conditional.

    **Why the parameter exists.** ``GenomicScore.__init__`` is ~98% the
    cerberus normalize-and-validate pass over the resource's config, and
    that pass scales with the score count -- milliseconds for a wide
    resource.  A scan task used to pay it three or four times per region
    (gain#1038): once for the allele probe, once in each gate it asks,
    and once more in the scan that serves the region.  The config does
    not change between those calls, so they all built the same thing.

    **Why not memoize the factory instead.** ``build_score_from_resource``
    documents the opposite contract -- every call yields a fresh instance
    the caller owns -- and it is called from the annotation pipeline and
    the web tier, not just here.  Sharing is therefore made explicit at
    the call site, and bounded by it.

    **Why the sharing stops at the task frame.** Not because the score
    could not survive the trip: ``do_noregion_histograms`` calls the two
    task functions in-process, with no task graph between them, and a
    score would reach them intact.  It stops because a ``do_*_task``
    signature is a SCHEDULING CONTRACT -- ``impl`` hands those arguments
    to ``TaskGraph.make_task``, and under a distributed executor they are
    serialized.  A parameter that cannot appear in that list does not
    belong in that signature, whoever the caller happens to be.

    **The pair must match.** ``resource`` is ignored when a ``score`` is
    given, so the two must describe the same resource; handing a score
    built from another one would scan that other resource silently.  Not
    asserted, because identity is too strong a test -- a caller may
    legitimately hold an equivalent resource re-fetched from the
    repository -- and every call site is in this module.

    **Who closes it.** A score handed to one of the scans is OPENED by
    that scan and CLOSED on return, which is what the freshly built one
    always got: a scan is the last thing a task does with its score, and
    leaving a pysam handle open for the caller to remember would be the
    surprise.  It holds for a score handed in already open too -- the
    scan does not adopt the caller's lifetime, it ends it.  The gates
    open nothing and so close nothing.
    """
    if score is not None:
        return score
    return build_score_from_resource(resource)


def _allele_batches(
    score: GenomicScore,
    chrom: str,
    start: int,
    end: int,
    score_ids: list[str],
    *,
    alleles: RegionAlleles,
    batch_size: int,
) -> Iterable[RecordArrays]:
    """The bulk producer for a region carrying an allele statistic.

    Only the score kind differs from the shared producer, and only the
    statistics module knows that; this adapter is what lets
    ``bulk_region_scan`` stay a driver that reads whatever producer it
    was handed.
    """
    return allele_arrays_folded_into(
        cast(AlleleScore, score), chrom, start, end, score_ids,
        batch_size=batch_size, alleles=alleles)


class RegionScanResult(NamedTuple):
    """What one region's statistics task hands to the merge step."""

    histograms: dict[str, Histogram]
    coverage: RegionCoverage | None
    alleles: RegionAlleles | None


def do_noregion_histograms(
    resource: GenomicResource,
) -> None:
    """Compute a resource's statistics in one task, contig by contig.

    The ``--region-size 0`` path: one task, no splitting *within* a
    contig.  The region-read family requires a contig, so the iteration
    over contigs is stated here rather than smuggled down as a null.

    Each contig is scanned through the very task functions a split run
    calls and folded together by the very functions that fold those
    tasks' results, so this produces what a split run produces -- not
    something merely believed to match, and a resource either of them
    refuses is attributed there rather than reaching the reader as a
    reason-less "statistics were not built".  An empty contig (an
    unscored alt, of which hg38 has hundreds) contributes an empty
    histogram, which merges cleanly; ``merge_histograms`` only
    nullifies on a genuine error.
    """
    all_min_max_scores, all_hist_confs = unpack_score_defs(resource)

    with build_score_from_resource(resource).open() as score:
        chroms = list(score.get_all_chromosomes())

    if all_min_max_scores:
        all_hist_confs = merge_min_max(
            all_min_max_scores,
            all_hist_confs,
            *(do_min_max_task(
                resource, all_min_max_scores, chrom, None, None)
              for chrom in chroms),
        )
    merge_and_save_histograms(
        resource,
        *(do_histogram_task(
            resource, all_hist_confs, chrom, None, None)
          for chrom in chroms),
    )


def unpack_score_defs(
    resource: GenomicResource,
) -> tuple[list[str], dict[str, HistogramConfig]]:
    """Extracts scores with min/max and histogram configs for a score."""
    score = build_score_from_resource(resource)
    all_min_max_scores = []
    all_hist_confs: dict[str, HistogramConfig] = {}
    with score.open():
        for score_id, score_def in score.score_definitions.items():
            if score_def.hist_conf is not None:
                hist_conf = score_def.hist_conf
            else:
                hist_conf = build_default_histogram_conf(
                    score_def.value_type)
            if isinstance(hist_conf, NullHistogramConfig):
                all_hist_confs[score_id] = hist_conf
                continue

            if isinstance(hist_conf, CategoricalHistogramConfig):
                all_hist_confs[score_id] = hist_conf
                continue

            assert isinstance(hist_conf, NumberHistogramConfig)
            if not hist_conf.has_view_range():
                all_min_max_scores.append(score_id)
            all_hist_confs[score_id] = hist_conf
    return all_min_max_scores, all_hist_confs


def scan_region(
    score: GenomicScore,
    chrom: str,
    start: int | None,
    end: int | None,
    score_ids: list[str],
    *,
    alleles: RegionAlleles | None = None,
) -> Generator[
        tuple[int, int, list[ScoreValue]], None, None]:
    """Read a region the way the statistics scan reads it.

    Every per-record pass reads here, so validation is composed in the
    open -- one visible extra link over the record stream -- rather than
    each pass being trusted to remember it (ADR 0008).  What the pass
    then sees is exactly what a reader sees: the same transform, over the
    same records, in the same order.

    One read.  ``validate_records`` is a transducer over the very stream
    the transform consumes, not a second pass over the region -- and so
    is the optional allele fold, which reads the nucleotides off the RAW
    records the transform is about to collapse to points (gain#777).
    """
    records = score.validate_records(
        score.fetch_records(chrom, start, end))
    if alleles is not None:
        records = records_folded_into(records, alleles)
    yield from score.region_values_from_records(
        records, chrom, start, end, score_ids)


def do_min_max(
    resource: GenomicResource,
    score_ids: list[str],
    chrom: str,
    start: int | None,
    end: int | None,
    *,
    score: GenomicScore | None = None,
) -> dict[str, MinMaxValue]:
    """Reduce a region to a min and a max per score, record by record.

    The per-record min/max pass, and the floor the vectorized
    :func:`do_min_max_bulk` is required to match.  Reads through
    :func:`scan_region`, so the validation every pass composes is
    composed here too (ADR 0008), and reduces only the records the
    region OWNS -- min/max would survive double-counting, but a
    region measuring differently by which path served it is what the
    parity tests refuse.

    A ``score`` handed in is opened here and closed on return; see
    :func:`_score_for`.
    """
    result = {
        scr_id: MinMaxValue(scr_id)
        for scr_id in score_ids
    }
    with _score_for(resource, score).open() as opened:
        for left, _right, rec in scan_region(
                opened, chrom, start, end, score_ids):
            # The same record partition every statistic reads: a
            # region reduces the records it OWNS.  min/max is
            # idempotent under duplication, so the merged result
            # would survive without this -- but the bulk twin
            # selects, and a region measuring differently by which
            # path served it is what the parity tests refuse.
            if not owns_record(left, start, end):
                continue
            for score_index, score_id in enumerate(score_ids):
                result[score_id].add_value(
                    rec[score_index],  # type: ignore
                )
    return result


def merge_min_max(
    score_ids: list[str],
    all_hist_confs: dict[str, HistogramConfig],
    *calculate_tasks: dict[str, MinMaxValue],
) -> dict[str, HistogramConfig]:
    """Fold every region's min/max together and view-range the confs.

    The merge stage between the min/max tasks and the histogram
    tasks: each region hands up its own ``MinMaxValue`` per score,
    they combine into one, and :func:`update_hist_confs` writes the
    result into the histogram configs the histogram pass will build
    from.  A histogram whose config already carries a view range
    never scheduled a min/max task and is not touched.
    """
    res: dict[str, MinMaxValue] = {}
    for score_id in score_ids:
        for min_max_region in calculate_tasks:
            if res.get(score_id) is None:
                res[score_id] = min_max_region[score_id]
            else:
                assert res[score_id] is not None
                res[score_id].merge(
                    min_max_region[score_id])
    return update_hist_confs(
        all_hist_confs, res)


def update_hist_confs(
    all_hist_confs: dict[str, HistogramConfig],
    minmax_task: dict[str, MinMaxValue] | None,
) -> dict[str, HistogramConfig]:
    """Give each number histogram the view range its min/max found.

    ``None`` means no min/max pass ran, so the configs stand as they
    are.  A score whose min or max came back nan has no values to
    bin -- its histogram is nullified with that as the reason, which
    is a resource fact worth reporting, not a failure to raise on.
    """
    if minmax_task is None:
        return all_hist_confs

    for score_id, min_max in minmax_task.items():
        hist_conf = all_hist_confs[score_id]
        assert isinstance(hist_conf, NumberHistogramConfig)
        assert not hist_conf.has_view_range()
        if np.isnan(min_max.min) or np.isnan(min_max.max):
            logger.warning(
                "min/max value for %s not found; "
                "nullify the histogram", score_id)
            all_hist_confs[score_id] = NullHistogramConfig(
                f"min/max for {score_id} not found")
        else:
            hist_conf.view_range = (min_max.min, min_max.max)
    logger.info("histogram configs updated: %s", all_hist_confs)
    return all_hist_confs


def do_histogram(
    resource: GenomicResource,
    all_hist_confs: dict[str, HistogramConfig],
    chrom: str,
    start: int | None,
    end: int | None,
    *,
    coverage: RegionCoverage | None = None,
    alleles: RegionAlleles | None = None,
    score: GenomicScore | None = None,
) -> dict[str, Histogram]:
    """Histogram a region record by record, per score.

    The per-record histogram pass, and the floor
    :func:`do_histogram_bulk` is required to match to the bit.  Reads
    through :func:`scan_region` and weighs each owned record the way
    the score's kind weighs it (``RECORD_WEIGHT_IS_SPAN``).

    ``coverage`` and ``alleles`` ride the same read rather than
    costing the region a second one, and are accumulated IN PLACE --
    the caller owns them, because it is the task's return value that
    has to travel under a distributed executor.

    A histogram that refuses a value is nullified on its own and the
    resource's other scores carry on.

    A ``score`` handed in is opened here and closed on return; see
    :func:`_score_for`.
    """
    result: dict[str, Histogram] = {}

    logger.info("updated hist confs: %s", all_hist_confs)

    for score_id, hist_conf in all_hist_confs.items():
        if isinstance(hist_conf, NullHistogramConfig):
            continue
        result[score_id] = build_empty_histogram(hist_conf)

    score_ids = list(result.keys())
    with _score_for(resource, score).open() as opened:
        # One statement of the rule, read by this path and by the bulk
        # one: only a position score weighs a record by its span.
        weight_is_span = opened.RECORD_WEIGHT_IS_SPAN
        # Coverage unions POSITIONS, and a union is only additive
        # across parallel regions when the spans are clipped to
        # disjoint extents -- so a kind whose rows can overlap keeps
        # clipping.  Rows that cannot are already pairwise disjoint,
        # so their coverage is exact unclipped and rides the same
        # record partition as every other statistic.
        clip_coverage = coverage is not None \
            and not coverage.rows_are_disjoint
        track_fragments = coverage is not None \
            and coverage.tracks_fragments
        for left, right, rec in scan_region(
                opened, chrom, start, end, score_ids,
                alleles=alleles):
            owned = owns_record(left, start, end)
            if coverage is not None:
                if clip_coverage:
                    span = clip_span(left, right, start, end)
                    if span is not None:
                        coverage.add_interval(
                            span[0], span[1], normalize_values(rec))
                elif owned:
                    coverage.add_interval(
                        left, right, normalize_values(rec))
            if not owned:
                continue
            if track_fragments:
                # A fragment is the row as stored, at its own span.
                assert coverage is not None
                coverage.add_fragment(right - left + 1)
            weight = right - left + 1 if weight_is_span else 1
            for scr_index, scr_id in enumerate(score_ids):

                try:
                    result[scr_id].add_value(
                        rec[scr_index],  # type: ignore
                        weight,
                    )
                except TypeError as err:
                    logger.exception(
                        "Failed adding value %s to histogram of %s; "
                        "%s:%s-%s", rec[scr_index] if rec else None,
                        resource.resource_id,
                        chrom, start, end)
                    result[scr_id] = NullHistogram(
                        NullHistogramConfig(str(err)),
                    )
                except HistogramError as err:
                    logger.warning(
                        "Histogram for %s nullified",
                        scr_id,
                    )
                    result[scr_id] = NullHistogram(
                        NullHistogramConfig(str(err)),
                    )
    return result


#: The row count one bulk read pulls per batch.
_SCAN_BATCH_SIZE = 100_000


def do_histogram_bulk(
    resource: GenomicResource,
    all_hist_confs: dict[str, HistogramConfig],
    chrom: str,
    start: int,
    end: int,
    *,
    coverage: RegionCoverage | None = None,
    alleles: RegionAlleles | None = None,
    score: GenomicScore | None = None,
) -> dict[str, Histogram]:
    """Vectorized equivalent of :func:`do_histogram`.

    Reads a region as batches of column arrays -- tabix pulls raw pysam
    rows directly and bigWig converts each fetched interval chunk in one
    shot, neither building a ``Record`` per row -- and accumulates each
    score's histogram with the histogram's own ``add_batch`` rather than a
    per-record ``add_value``.  The selection, the weight, the overlap rule
    and the value coercion are identical to the per-record path (pinned
    by the bulk-vs-per-record tests, and by both paths reading one
    statement of the per-kind rules); the dispatch restricts this to the
    score and histogram combinations :func:`can_bulk_histogram`
    admits, over tabix/bigWig tables -- everything else keeps
    :func:`do_histogram`.
    """
    result: dict[str, Histogram] = {}
    for score_id, hist_conf in all_hist_confs.items():
        if isinstance(hist_conf, NullHistogramConfig):
            continue
        result[score_id] = build_empty_histogram(hist_conf)

    accumulate = _accumulate_arrays
    if coverage is not None:

        def accumulate_with_coverage(
            arrays: RecordArrays,
            result: dict[str, Histogram],
            region: tuple[str, int | None, int | None],
            # Shadows this function's own ``score`` parameter, and must:
            # the name is part of the accumulator type ``accumulate`` is
            # assigned from, which ``_accumulate_arrays`` spells
            # ``score``.  Harmless -- nothing in here reads the outer
            # one; the ``bulk_region_scan`` call that does is below, at
            # this function's own scope.
            score: GenomicScore,
        ) -> None:
            _accumulate_arrays(
                arrays, result, region, score)
            accumulate_coverage(arrays, coverage, region)

        accumulate = accumulate_with_coverage

    batches = None
    if alleles is not None:
        # The widened read, folding the nucleotides off each batch on
        # its way to the shared door; what the door and this scan see
        # is the same three-column batch as ever.
        batches = functools.partial(
            _allele_batches, alleles=alleles,
            batch_size=_SCAN_BATCH_SIZE)
    return bulk_region_scan(
        resource, result, chrom, start, end, accumulate,
        batches=batches, score=score)


def bulk_region_scan(
    resource: GenomicResource,
    result: dict[str, _AccT],
    chrom: str,
    start: int,
    end: int,
    accumulate: Callable[
        [RecordArrays, dict[str, _AccT],
         tuple[str, int | None, int | None], GenomicScore],
        None],
    *,
    batches: Callable[
        [GenomicScore, str, int, int, list[str]],
        Iterable[RecordArrays]] | None = None,
    score: GenomicScore | None = None,
) -> dict[str, _AccT]:
    """Drive a bulk region scan, folding each batch into ``result``.

    Public though only this module calls it: ADR 0001 names it as
    the shared driver behind both bulk passes, so it is part of what
    ``scan`` states about itself rather than a helper.

    The shared skeleton of :func:`do_histogram_bulk` and
    :func:`do_min_max_bulk`: open the score and stream the region's
    column-array batches through ``accumulate`` (which mutates
    ``result``).  The caller supplies the pre-built ``result`` -- empty
    histograms or seeded ``MinMaxValue`` -- and the matching accumulator.
    Batches are keyed by SCORE ID: the score resolves each id to its
    payload column itself (gain#398), so nothing here handles column
    indices.

    This is the scan's vectorized door, and the counterpart of
    :func:`scan_region`: the batches are read through
    ``validate_record_arrays``, one visible extra link over the stream
    the scan is already pulling, so that a pass cannot be added that
    quietly reads unvalidated (ADR 0008).  The kind states its own rule
    in that method's body; nothing here knows what the rule is.

    The validator is per REGION, and a region lies within one contig, so
    the ordering carry never spans a contig boundary -- the same reason
    the per-record validators reset on a change of chromosome.

    A ``score`` handed in is opened here and closed on return; see
    :func:`_score_for`.
    """
    with _score_for(resource, score).open() as opened:
        if batches is None:
            batches = _validated_batches
        for arrays in batches(opened, chrom, start, end, list(result)):
            accumulate(arrays, result, (chrom, start, end), opened)
    return result


def _validated_batches(
    score: GenomicScore,
    chrom: str,
    start: int,
    end: int,
    score_ids: list[str],
) -> Iterable[RecordArrays]:
    """The default producer: the shared read through the shared door."""
    return score.validate_record_arrays(
        score.fetch_region_value_arrays(
            chrom, start, end, score_ids,
            batch_size=_SCAN_BATCH_SIZE),
        chrom)


def _accumulate_arrays(
    arrays: RecordArrays,
    result: dict[str, Histogram],
    region: tuple[str, int | None, int | None],
    score: GenomicScore,
) -> None:
    """Fold one batch of column arrays into the per-score histograms.

    ``arrays`` is one ``(pos_begin, pos_end, {score_id: cells})`` batch as
    produced by :meth:`_region_value_arrays`.  Selects the records this
    region OWNS exactly as the per-record read does, weighs each at
    its full span as ``score``'s kind weighs it, and adds each
    score's values vectorized.  Whether the
    batch is one this kind's records may form was settled before it got
    here, by the door's ``validate_record_arrays``.

    A histogram that refuses its batch is nullified and the rest of the
    resource's scores carry on, exactly as :func:`do_histogram` nullifies
    one that refuses a value: a categorical histogram raises once its
    values outgrow ``UNIQUE_VALUES_LIMIT``, and the score it belongs to
    must not cost the others their statistics.  A nullified score is
    skipped by every later batch, which is what the per-record path gets
    from ``NullHistogram.add_value`` being a no-op.
    """
    pos_begin, pos_end, value_cells = arrays
    keep, weights = _select_and_weigh(
        pos_begin, pos_end, region, score)

    for score_id, hist in result.items():
        if not isinstance(hist, (NumberHistogram, CategoricalHistogram)):
            continue
        values = value_cells[score_id][keep]
        try:
            hist.add_batch(values, weights)
        except TypeError as err:
            logger.exception(
                "Failed adding a batch of %s values to the histogram of "
                "%s; %s:%s-%s", values.size, score.resource_id, *region)
            result[score_id] = NullHistogram(NullHistogramConfig(str(err)))
        except HistogramError as err:
            logger.warning(
                "Histogram for %s nullified: %s", score_id, err)
            result[score_id] = NullHistogram(NullHistogramConfig(str(err)))


def _select_and_weigh(
    pos_begin: np.ndarray,
    pos_end: np.ndarray,
    region: tuple[str, int | None, int | None],
    score: GenomicScore,
) -> tuple[np.ndarray, np.ndarray]:
    """Select a batch's owned records and weigh them, per ``score``'s kind.

    Returns ``(keep, weights)``:
    :func:`~gain.genomic_resources.genomic_scores.records.owned_records_mask`,
    and the owned records' weights measured at their FULL span --
    never clipped to the region.  Selecting instead of clipping is
    what makes a statistic independent of ``--region-size``
    (gain#816): a record straddling a boundary used to be measured by
    both regions, which summed to the right answer only for a
    span-weighted kind and double-counted every count-weighted one.

    Because the span is the record's own, an inverted span can no
    longer be MADE here by clipping -- the gain#636 edge is
    unrepresentable on this path rather than merely guarded.

    Measuring only.  Whether the batch is one this kind's records may
    form is settled upstream, by the door's ``validate_record_arrays``,
    against the RAW columns -- which is why no rule is stated here.

    The weight is read off the score class, which states it once for this
    path and for the per-record one: ``RECORD_WEIGHT_IS_SPAN`` -- a
    position-score record counts once per base pair it spans; an allele
    record and a fragment count 1, however wide they are.
    """
    _chrom, start, end = region
    keep = owned_records_mask(pos_begin, start, end)
    if not score.RECORD_WEIGHT_IS_SPAN:
        # A count kind needs only HOW MANY records it owns; gathering
        # their spans to measure one array's length would allocate
        # two full columns per batch and read neither.
        return keep, np.ones(
            int(np.count_nonzero(keep)), dtype=np.int64)
    if keep.all():
        # The common case by a wide margin: rows arrive begin-sorted
        # and only a leading run can fall outside the region, so
        # every batch after a region's first is wholly owned.
        return keep, (pos_end - pos_begin + 1).astype(np.int64)
    left = pos_begin[keep]
    right = pos_end[keep]
    return keep, (right - left + 1).astype(np.int64)


def can_bulk_histogram(
    resource: GenomicResource,
    all_hist_confs: dict[str, HistogramConfig],
    *,
    score: GenomicScore | None = None,
) -> bool:
    """Whether the vectorized scan may serve this histogram build.

    :func:`bulk_scan_eligible` plus the conditions that are this caller's
    alone -- every score must feed a histogram that can accumulate a whole
    batch, and be handed the batch shape that histogram accepts:

    * a NUMBER histogram takes a ``float`` or an ``int`` score, whose
      columns the bulk read yields as the ``float64``
      ``NumberHistogram.add_batch`` accumulates;
    * a CATEGORICAL histogram takes a ``str`` score, whose column the bulk
      read yields as the ``str`` objects
      ``CategoricalHistogram.add_batch`` counts;
    * a NULL histogram has nothing to accumulate and is skipped by both
      paths, so it constrains neither.

    The two pairings are the whole rule, and the mismatches are what it
    exists to keep out: the per-record path meets a value its histogram
    refuses one at a time, catches the ``TypeError`` and nullifies that
    one score, whereas a batch of the wrong shape is not a value the
    histogram can refuse -- it is a coercion failure inside ``add_batch``.
    So a categorical histogram over an ``int`` score, or a number
    histogram over a ``str`` one, keeps :func:`do_histogram`, which
    handles both as it always has.
    """
    pairing = {
        NumberHistogramConfig: ("float", "int"),
        CategoricalHistogramConfig: ("str",),
    }
    bulk_score_ids = []
    score = _score_for(resource, score)
    score_defs = score.score_definitions
    for score_id, hist_conf in all_hist_confs.items():
        if isinstance(hist_conf, NullHistogramConfig):
            continue
        value_types = pairing.get(type(hist_conf))
        score_def = score_defs.get(score_id)
        if value_types is None or score_def is None \
                or score_def.value_type not in value_types:
            return False
        bulk_score_ids.append(score_id)
    return bulk_scan_eligible(
        resource, bulk_score_ids, score=score)


def can_bulk_min_max(
    resource: GenomicResource,
    score_ids: list[str],
    *,
    score: GenomicScore | None = None,
) -> bool:
    """Whether the vectorized scan may serve this min/max pass.

    :func:`bulk_scan_eligible` plus the one condition that is this
    caller's alone: every score must be a NUMBER.  The reduction is
    ``min()``/``max()`` over the non-nan values of a float64 column, and a
    ``str`` score's column is an object array, which ``np.isnan`` refuses
    outright.

    A str score reaches here only through a misconfiguration: a min/max is
    scheduled for a score whose histogram is a number histogram without a
    view range, and a number histogram over a str score is exactly the
    mismatch :func:`can_bulk_histogram` keeps off the bulk path.  Left
    ungated, a column of nothing but NA sentinels would raise here, out of
    a generator and past every nullify handler, where the per-record path
    yields an empty min/max and nullifies that one histogram -- so the
    condition is stated for this consumer too, not assumed from the other.
    """
    score = _score_for(resource, score)
    score_defs = score.score_definitions
    for score_id in score_ids:
        score_def = score_defs.get(score_id)
        if score_def is None \
                or score_def.value_type not in ("float", "int"):
            return False
    return bulk_scan_eligible(
        resource, score_ids, score=score)


def bulk_scan_eligible(
    resource: GenomicResource,
    score_ids: list[str],
    *,
    score: GenomicScore | None = None,
) -> bool:
    """Whether a vectorized region scan may serve these scores.

    The shared gate for the histogram and min/max bulk paths, and the place
    the conditions that are THIS caller's live -- as opposed to the one
    condition that is the backend's, which the score answers itself:

    * a resource kind the bulk path is exercised against
      (:data:`_BULK_SCAN_RESOURCE_TYPES`): a position, allele or fragment
      score.  Their record semantics are not assumed here -- the score
      class states them, in ``RECORD_WEIGHT_IS_SPAN`` and in its own
      ``validate_record_arrays`` body.  The set names every score kind
      GAIn accepts, so this test cannot fail today; it is kept so that
      a newly registered kind lands on the per-record path by default;
    * every score of a value type the column parse defines
      (``float``, ``int``, ``str``) -- asked of the score, which owns
      that parse;
    * and the backend serves the bulk read at all -- asked of the score,
      not tested on the table's class.  This is what keeps a VCF-backed
      allele score on the per-record path: its record payload is not a raw
      row, so its table declares no column-array support.

    Answered WITHOUT opening the score: the table and the score definitions
    are both built in ``GenomicScore.__init__``, so nothing here needs a
    file handle.  That is also why ``score`` may be handed in already
    closed -- a caller that has finished reading through it can still ask
    this (see :func:`_score_for` for why it would want to).
    """
    if resource.get_type() not in _BULK_SCAN_RESOURCE_TYPES:
        return False
    return _score_for(resource, score).supports_region_value_arrays(score_ids)


def do_min_max_task(
    resource: GenomicResource,
    score_ids: list[str],
    chrom: str,
    start: int | None,
    end: int | None,
) -> dict[str, MinMaxValue]:
    """Compute a region's min/max, bulk-vectorized where eligible.

    Mirrors :func:`do_histogram_task`: the bulk path needs a bounded
    region -- a concrete contig for its overlap guard, and concrete bounds
    because that is what the score's bulk read takes -- so any unbounded
    scan keeps the per-record :func:`do_min_max`.

    A resource the scan refuses is reported here and the refusal
    re-raised: this is the only frame that knows both which resource the
    scan was reading and that the reading is what failed, and the task
    graph's own report names no resource.

    ONE score, built here and threaded to the gate and to whichever scan
    serves the region -- see :func:`_score_for`.  Built inside the
    ``try``, because a construction that refuses the resource is exactly
    the failure this frame exists to attribute.  (Its histogram sibling
    builds outside, for a reason its own docstring gives.)
    """
    try:
        score = build_score_from_resource(resource)
        if chrom is not None and start is not None and end is not None \
                and can_bulk_min_max(
                    resource, score_ids, score=score):
            return do_min_max_bulk(
                resource, score_ids, chrom, start, end, score=score)
        return do_min_max(
            resource, score_ids, chrom, start, end, score=score)
    except MalformedResourceError as err:
        report_resource_failure(
            err, "could not scan the values of", resource.resource_id)
        raise


def do_histogram_task(
    resource: GenomicResource,
    all_hist_confs: dict[str, HistogramConfig],
    chrom: str,
    start: int | None,
    end: int | None,
) -> RegionScanResult:
    """Compute a region's histograms, bulk-vectorized where eligible.

    The bulk path needs a bounded region: a concrete contig, because its
    overlap guard runs along a single chromosome's records, and concrete
    bounds, because that is what the score's bulk read takes.  Any
    unbounded scan keeps the per-record path.

    A resource the scan refuses is reported here and the refusal
    re-raised, for the reason :func:`do_min_max_task` gives.

    Coverage rides the same read: a kind whose rows have a span to
    union gets a :class:`RegionCoverage` accumulated by whichever
    path serves the histograms, carried out in the task's RETURN
    value — a mutated argument would not travel under a distributed
    executor, whose task results arrive serialized.  An allele
    score's :class:`RegionAlleles` rides it on the same terms, with
    one extra condition on the bulk path: it needs the nucleotides,
    so a backend that will not serve them sends the region back to
    the per-record read rather than to a statistic with no class
    data.

    ONE score serves the whole invocation -- the allele probe below, the
    bulk gate, and whichever scan takes the region -- where each of those
    used to build its own (gain#1038); see :func:`_score_for`.  An allele
    score's probe OPENS it and hands it on closed; the scan reopens it,
    as it would have opened a fresh one.

    Built BEFORE the ``try``, unlike :func:`do_min_max_task`'s, because
    the allele probe needs it and the probe precedes the ``try``.  So a
    construction that refuses this resource is not attributed here -- as
    was already the case before the score was shared.
    """
    coverage = None
    resource_type = resource.get_type()
    if resource_type in _COVERAGE_SCAN_RESOURCE_TYPES:
        coverage = RegionCoverage(
            chrom, start, end,
            rows_are_disjoint=resource_type
            in _NON_OVERLAPPING_ROW_RESOURCE_TYPES,
            track_fragments=resource_type
            in _FRAGMENT_STATISTICS_RESOURCE_TYPES)
    score = build_score_from_resource(resource)
    alleles = region_alleles_for(score, chrom, start, end)
    nucleotides = True
    if alleles is not None:
        # Asked of an OPEN score, and of the score ids a bulk read
        # would ask for -- the filter ``do_histogram_bulk`` builds
        # its result from, since a null histogram has nothing to
        # accumulate.  Unopened, a table naming its key columns
        # nowhere but in its own header answers False and costs the
        # whole region the bulk scan for no gain in correctness.
        with score.open():
            nucleotides = serves_allele_arrays(score, [
                score_id for score_id, conf in all_hist_confs.items()
                if not isinstance(conf, NullHistogramConfig)])
    try:
        if chrom is not None and start is not None and end is not None \
                and nucleotides \
                and can_bulk_histogram(
                    resource, all_hist_confs, score=score):
            histograms = do_histogram_bulk(
                resource, all_hist_confs, chrom, start, end,
                coverage=coverage, alleles=alleles, score=score)
        else:
            histograms = do_histogram(
                resource, all_hist_confs, chrom, start, end,
                coverage=coverage, alleles=alleles, score=score)
        return RegionScanResult(histograms, coverage, alleles)
    except MalformedResourceError as err:
        report_resource_failure(
            err, "could not build the histograms of",
            resource.resource_id)
        raise


def do_min_max_bulk(
    resource: GenomicResource,
    score_ids: list[str],
    chrom: str,
    start: int,
    end: int,
    *,
    score: GenomicScore | None = None,
) -> dict[str, MinMaxValue]:
    """Vectorized equivalent of :func:`do_min_max`.

    Reads the region as column-array batches of already-parsed values
    (the same producer the histogram bulk path uses) and reduces each score
    with ``min()``/``max()`` over the batch's non-nan subset, rather than a
    per-record ``MinMaxValue.add_value``.  The parse, the region selection,
    the overlap rule and the record count are identical to the per-record
    path -- both read the same per-kind facts off the score class.
    """
    result: dict[str, MinMaxValue] = {
        score_id: MinMaxValue(score_id) for score_id in score_ids}
    return bulk_region_scan(
        resource, result, chrom, start, end,
        _accumulate_min_max, score=score)


def _accumulate_min_max(
    arrays: RecordArrays,
    result: dict[str, MinMaxValue],
    region: tuple[str, int | None, int | None],
    score: GenomicScore,
) -> None:
    """Fold one batch of column arrays into the per-score min/max.

    Shares the record selection with the histogram path, and the door it
    is read through with every pass; the reduction takes ``min()``/``max()``
    over the owned values
    with the nans dropped first -- an empty remainder contributes nothing --
    folded into the running ``MinMaxValue`` exactly as ``add_value`` seeds
    and combines them.

    An extremum of an ``int`` score is converted back to ``int``, because
    that is the type the per-record ``add_value`` folds in and therefore
    what ``MinMaxValue.serialize`` writes (``min: 3``, not ``min: 3.0``).
    The column arrives as ``float64`` -- the array's non-value has to be a
    nan -- so the round trip is exact up to 2**53 and the correctly
    rounded integer above it.
    """
    pos_begin, _pos_end, value_cells = arrays
    # The selection alone: min/max reduces the records this region
    # owns and weighs nothing, so it reads the mask directly rather
    # than asking for weights it would discard.
    _chrom, start, end = region
    keep = owned_records_mask(pos_begin, start, end)

    for score_id, min_max in result.items():
        values = value_cells[score_id][keep]
        finite = values[~np.isnan(values)]
        if finite.size:
            is_int = \
                score.score_definitions[score_id].value_type == "int"
            low: float = int(finite.min()) if is_int \
                else float(finite.min())
            high: float = int(finite.max()) if is_int \
                else float(finite.max())
            min_max.min = low if np.isnan(min_max.min) \
                else min(min_max.min, low)
            min_max.max = high if np.isnan(min_max.max) \
                else max(min_max.max, high)


def merge_histograms(
    resource: GenomicResource,  # noqa: ARG001
    *calculated_histograms: dict[str, Any],
) -> dict[str, Histogram]:
    """Fold each region's histograms into one histogram per score.

    A score that cannot be histogrammed is nullified on its own, exactly
    as the per-region accumulation path nullifies its own overflow: the
    merge of a resource's scores is per score, so one un-histogrammable
    score must not cost the rest of the resource its histograms
    (gain#465).  A categorical score can stay within
    ``UNIQUE_VALUES_LIMIT`` in every single region and exceed it only in
    their union, so the merge is the first place its failure appears.
    """
    result: dict[str, Histogram] = {}

    for histogram_region in calculated_histograms:
        for score_id, hist in histogram_region.items():
            if result.get(score_id) is None:
                # The accumulator aliases the first region's histogram
                # and later regions are merged into it in place, so that
                # region's dict ends up holding the merged -- or, when
                # the merge raises, the partially merged -- object.
                # Safe as written: the per-region dicts are dependency
                # task results, handed to this merge once and never read
                # again, and under a distributed executor they arrive
                # deserialized, so nothing observes the mutation.
                result[score_id] = hist
                continue
            if isinstance(result[score_id], NullHistogram):
                continue
            if isinstance(hist, NullHistogram):
                result[score_id] = NullHistogram(NullHistogramConfig(
                    f"Empty histogram for {score_id} in a region: "
                    f"{hist.reason}"))
            else:
                try:
                    result[score_id].merge(hist)
                except HistogramError as err:
                    logger.warning(
                        "Histogram for %s nullified while merging "
                        "regions: %s", score_id, err)
                    result[score_id] = NullHistogram(
                        NullHistogramConfig(str(err)),
                    )

    return result


def _save_histograms(
    resource: GenomicResource, merged_histograms: dict[str, Histogram],
) -> dict[str, Histogram]:
    # The one reach past a private in this module, and the one
    # thread still tying ``scan`` to the implementation class
    # hierarchy -- its two siblings just above are plain module
    # functions imported from the statistic's own module.
    # ``_save_and_plot_histograms`` is a staticmethod touching no
    # ``cls`` and is never overridden, so promoting it to a
    # module-level function in ``score_implementation`` retires
    # this suppression; that also edits ``gene_scores_impl``, its
    # other caller, so it is gain#1036 rather than gain#1007.
    ScoreImplementationBase._save_and_plot_histograms(  # noqa: SLF001
        resource, build_score_from_resource(resource),
        merged_histograms)
    return merged_histograms


def merge_and_save_histograms(
    resource: GenomicResource,
    *results: RegionScanResult,
) -> dict[str, Histogram]:
    """Fold every region's scan results together and save all three.

    The scan's last task.  Histograms, coverage and alleles each
    merge across the regions and are written into the resource --
    the one place any of the three is produced, so no SECOND
    code path can refresh one of them and leave the others stale.
    Within this one the three writes are sequential and there is
    no rollback, so a raise partway does leave a mixture.
    """
    merged_histograms = merge_histograms(
        resource, *(result.histograms for result in results))
    save_and_plot_coverage(resource, merge_region_coverage(
        resource.resource_id,
        (result.coverage for result in results)))
    save_allele_statistics(resource, merge_region_alleles(
        resource.resource_id,
        (result.alleles for result in results)))
    return _save_histograms(
        resource, merged_histograms)
