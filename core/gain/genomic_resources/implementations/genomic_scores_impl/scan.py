from __future__ import annotations

import functools
from collections.abc import Callable, Generator, Iterable
from typing import Any, NamedTuple, cast

import numpy as np

from gain import logging
from gain.genomic_resources.cli_errors import report_resource_failure
from gain.genomic_resources.genomic_scores import (
    AlleleScore,
    GenomicScore,
    RecordArrays,
    build_score_from_resource,
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
    region_coverage_for,
    save_and_plot_coverage,
)
from gain.genomic_resources.statistics.fragments import (
    RegionFragments,
    accumulate_fragments,
    merge_region_fragments,
    region_fragments_for,
    save_and_plot_fragments,
)
from gain.genomic_resources.statistics.min_max import MinMaxValue
from gain.genomic_resources.statistics.record_validation import (
    validate_record_arrays,
    validate_records,
)

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

#: The score value types a NUMBER histogram can fold ONE VALUE of.
#:
#: ``float`` and ``int`` are the obvious two.  ``bool`` is here because a
#: bool IS a number to numpy -- ``np.isnan(True)`` answers ``False`` rather
#: than raising -- so a ``type: bool`` score under a number histogram folds
#: to a 0/1 histogram, and has always done so.  ``str`` is the one type no
#: number histogram can fold at all: ``np.isnan`` raises ``TypeError`` on
#: text, which is what a resource pairing the two aborted its entire
#: statistics build with (gain#1285).
#:
#: Deliberately WIDER than :data:`_BULK_HISTOGRAM_VALUE_TYPES`, and the two
#: are different questions rather than one rule stated twice: this asks what
#: a histogram can fold value by value, that asks what it can fold a whole
#: column of.
_NUMBER_HISTOGRAM_VALUE_TYPES = ("float", "int", "bool")

#: Which score value types each histogram kind can accumulate a whole BATCH
#: of -- the pairing :func:`can_bulk_histogram` gates the vectorized
#: histogram scan on.  Narrower than :data:`_NUMBER_HISTOGRAM_VALUE_TYPES`:
#: the bulk read yields a number histogram's column as ``float64``, which a
#: ``bool`` score's column is not, and a categorical's as ``str`` objects.
#: A NULL histogram accumulates nothing and so appears in neither.
#:
#: :func:`can_bulk_min_max` deliberately does NOT read this, though its own
#: tuple matches the number row: a min/max pass accumulates no histogram,
#: and sharing the literal would let a future widening of one widen the
#: other by accident.  Its docstring says so at the point of the copy.
_BULK_HISTOGRAM_VALUE_TYPES: dict[type[HistogramConfig], tuple[str, ...]] = {
    NumberHistogramConfig: ("float", "int"),
    CategoricalHistogramConfig: ("str",),
}


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
    fragments: RegionFragments | None
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
    """Extracts scores with min/max and histogram configs for a score.

    Also where a configured NUMBER histogram over a value type it cannot
    fold is refused: it becomes a null histogram, and is therefore
    scheduled for no min/max pass either.  See
    :func:`_refuse_number_histogram` for why that refusal lives here rather
    than where a value meets its histogram.  It is the only mismatch
    refused here -- a CATEGORICAL histogram over a number is still left to
    the per-value catch in :func:`do_histogram`, because it nullifies one
    score rather than aborting the build.
    """
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
            if score_def.value_type not in _NUMBER_HISTOGRAM_VALUE_TYPES:
                all_hist_confs[score_id] = _refuse_number_histogram(
                    resource, score_id, score_def.value_type)
                continue

            if not hist_conf.has_view_range():
                all_min_max_scores.append(score_id)
            all_hist_confs[score_id] = hist_conf
    return all_min_max_scores, all_hist_confs


def _refuse_number_histogram(
    resource: GenomicResource, score_id: str, value_type: str | None,
) -> NullHistogramConfig:
    """Nullify a number histogram configured over a type it cannot fold.

    The refusal belongs HERE, where the configs are unpacked, rather than
    where a value meets its histogram, for three reasons.  It is the one
    place that also decides the min/max pass, and it is that pass -- not
    the histogram -- that a number histogram over text used to abort the
    whole build in (gain#1285); nullifying at the value would leave the
    min/max already scheduled.  It runs ONCE per statistics build, the
    region tasks being handed the result as data, so the report cannot
    repeat per record the way gain#1283's does.  And a config that can
    never work is a fact about the resource, knowable before a single
    record is read, where a value a histogram refuses one at a time is
    genuinely a fact about that value -- which is why ``do_histogram``
    keeps its own per-value catch as the backstop for the latter.

    The reason names the resource and the score because the abort it
    replaces named neither: it surfaced as a bare ``TypeError`` out of
    ``numpy``, leaving the reader of a failed ``repo-repair`` run nothing
    to grep for.

    **It prescribes the HISTOGRAM edit and not a ``type:`` one**, though
    both would end the mismatch for a table score.  A multi-valued VCF INFO
    field -- the shape this was found through -- discards whatever ``type:``
    states, because its value is the ``|``-join and no stated type can
    describe that (gain#1259); telling its author to correct the type names
    an edit that cannot work.  And a ``type:`` edit is not inert even where
    it does work: it moves NA normalization and the statistics hash, which
    is why ``_report_overridden_type`` declines to prescribe one too
    (gain#1284).  The histogram edit is correct for every route here.

    **The reason is carried by the log, not by the info page.**  A null
    histogram writes no ``histogram_<score>.json`` -- that suppression is
    what ``histogram: {type: "null"}`` means to the info-page templates --
    so the page says only that there is no histogram, and this line in the
    build log is where the why lives.  Surfacing a REFUSED null differently
    from an author-disabled one is gain#1307.
    """
    reason = (
        f"resource {resource.resource_id}: score {score_id!r} has value "
        f"type {value_type!r}, which a number histogram cannot "
        f"accumulate; give the score a categorical histogram "
        f"('histogram: {{type: categorical}}') or no histogram at all"
    )
    logger.warning("%s", reason)
    return NullHistogramConfig(reason)


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
    records = validate_records(
        score, score.fetch_records(chrom, start, end))
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
    fragments: RegionFragments | None = None,
    alleles: RegionAlleles | None = None,
    score: GenomicScore | None = None,
) -> dict[str, Histogram]:
    """Histogram a region record by record, per score.

    The per-record histogram pass, and the floor
    :func:`do_histogram_bulk` is required to match to the bit.  Reads
    through :func:`scan_region` and weighs each owned record the way
    the score's kind weighs it (``record_weight``).

    ``coverage``, ``fragments`` and ``alleles`` ride the same read
    rather than costing the region a second one, and are accumulated IN
    PLACE -- the caller owns them, because it is the task's return value
    that has to travel under a distributed executor.

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
        for left, right, rec in scan_region(
                opened, chrom, start, end, score_ids,
                alleles=alleles):
            owned = owns_record(left, start, end)
            if not owned:
                continue
            if coverage is not None:
                # The row at its full span, on the same record
                # partition as every other statistic: coverage-scanned
                # rows are pairwise disjoint, so the union is exact
                # unclipped (see ``RegionCoverage``).
                coverage.add_interval(
                    left, right, normalize_values(rec))
            if fragments is not None:
                # A fragment is the row as stored, at its own span.
                fragments.add_fragment(right - left + 1)
            # The kind's ONE statement of the weight rule, called per
            # record here and broadcast over a whole batch by the bulk
            # path -- the two cannot drift because there is nothing to
            # drift from.  Measured at the record's FULL span, never
            # clipped to the region: see `_select_and_weigh`, which says
            # why selecting beats clipping here (gain#816).
            weight = opened.record_weight(left, right)
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
    fragments: RegionFragments | None = None,
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
    if coverage is not None or fragments is not None:

        def accumulate_with_statistics(
            arrays: RecordArrays,
            result: dict[str, Histogram],
            region: tuple[str, int | None, int | None],
            # Shadows this function's own ``score`` parameter, and must.
            # ``accumulate``'s type is INFERRED from the
            # ``_accumulate_arrays`` assigned to it just above -- a
            # ``def``, whose parameter names are part of that type -- so
            # renaming this one is an assignment error, not a style
            # choice.  (``bulk_region_scan``'s own ``Callable[...]``
            # annotation carries no names; this is not that type.)
            # Harmless -- nothing in here reads the outer one; the
            # ``bulk_region_scan`` call that does is below, at this
            # function's own scope.
            score: GenomicScore,
        ) -> None:
            _accumulate_arrays(
                arrays, result, region, score)
            # Both statistics ride the record partition -- the rows the
            # region owns, at their full span -- but neither implies
            # the other: a fragment score takes this branch with no
            # coverage at all (gain#1127), a position score with no
            # fragments.
            if coverage is not None:
                accumulate_coverage(arrays, coverage, region)
            if fragments is not None:
                accumulate_fragments(arrays, fragments, region)

        accumulate = accumulate_with_statistics

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


# ``AccT`` is the per-batch accumulator target of a bulk region scan -- a
# Histogram for the histogram pass, a MinMaxValue for the min/max pass.  It
# keeps ``bulk_region_scan`` generic without losing either caller's dict type.
def bulk_region_scan[AccT](
    resource: GenomicResource,
    result: dict[str, AccT],
    chrom: str,
    start: int,
    end: int,
    accumulate: Callable[
        [RecordArrays, dict[str, AccT],
         tuple[str, int | None, int | None], GenomicScore],
        None],
    *,
    batches: Callable[
        [GenomicScore, str, int, int, list[str]],
        Iterable[RecordArrays]] | None = None,
    score: GenomicScore | None = None,
) -> dict[str, AccT]:
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
    quietly reads unvalidated (ADR 0008).  That function dispatches on the
    score's class to the rule registered for its kind (ADR 0027); nothing
    here knows what the rule is.

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
    return validate_record_arrays(
        score,
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
    path and for the per-record one: ``record_weight`` -- a position-score
    record counts once per base pair it spans; an allele record and a
    fragment count 1, however wide they are.  This path never calls it
    directly; ``record_weights`` broadcasts it over the owned records'
    position columns, which is the only weight read this scan makes.
    """
    _chrom, start, end = region
    keep = owned_records_mask(pos_begin, start, end)
    return keep, score.record_weights(pos_begin[keep], pos_end[keep])


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
    So a categorical histogram over an ``int`` score keeps
    :func:`do_histogram`, which handles it as it always has.

    A number histogram over a ``str`` score no longer reaches either path:
    :func:`unpack_score_defs` refuses that pairing outright and hands this
    one a null histogram instead (gain#1285).  The condition stays stated
    here because it is this gate's own -- the batch shapes it admits are
    not a consequence of what the unpack refuses, and a ``bool`` score
    separates them: the per-value rule keeps it, this one does not.
    """
    bulk_score_ids = []
    score = _score_for(resource, score)
    score_defs = score.score_definitions
    for score_id, hist_conf in all_hist_confs.items():
        if isinstance(hist_conf, NullHistogramConfig):
            continue
        value_types = _BULK_HISTOGRAM_VALUE_TYPES.get(type(hist_conf))
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

    A str score is no longer SCHEDULED for a min/max pass at all: the only
    thing that schedules one is a number histogram without a view range,
    and :func:`unpack_score_defs` refuses that over text before any pass is
    planned (gain#1285).  The condition stays stated for this consumer
    rather than assumed from that one, because it is this pass's own: left
    ungated, a column of nothing but NA sentinels would raise here, out of
    a generator and past every nullify handler, where the per-record path
    yields an empty min/max and nullifies that one histogram.

    Written out rather than read off :data:`_BULK_HISTOGRAM_VALUE_TYPES`,
    which the tuple happens to match: that constant says what a HISTOGRAM
    can accumulate a batch of, and this pass accumulates no histogram.
    Sharing the literal would make a future widening of the number
    histogram's batch types widen this gate as a side effect.
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

    The shared gate for the histogram and min/max bulk paths.  Both
    conditions belong to the score, so both are asked of it:

    * every score of a value type the column parse defines
      (``float``, ``int``, ``str``) -- asked of the score, which owns
      that parse;
    * and the backend serves the bulk read at all -- asked of the score,
      not tested on the table's class.  This is what keeps a VCF-backed
      allele score on the per-record path: its record payload is not a raw
      row, so its table declares no column-array support.

    **No resource kind is tested.**  Eligibility is a claim about a score,
    so it is asked of one: handed a resource that cannot become a score,
    and no ``score`` to use instead, this raises ``ValueError`` out of the
    factory rather than answering ``False``, which would read as a fact
    about a score and send a caller holding the wrong resource quietly
    down the per-record path.  With a ``score`` given the factory is not
    called, and the pair is the caller's to match (see :func:`_score_for`).

    A new kind needs no guard here either: ``record_weight`` is abstract on
    ``GenomicScore``, a kind absent from ``record_validation``'s registry is
    refused by the array door below, and a backend serving no column arrays
    is refused above.  ADR 0001 records why the kind condition was retired,
    and ADR 0027 why the second of those three refusals is now a run-time one
    rather than something mypy and pylint catch first.

    Answered WITHOUT opening the score: the table and the score definitions
    are both built in ``GenomicScore.__init__``, so nothing here needs a
    file handle.  That is also why ``score`` may be handed in already
    closed -- a caller that has finished reading through it can still ask
    this (see :func:`_score_for` for why it would want to).
    """
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

    The per-region statistics ride the same read, each accumulated by
    whichever path serves the histograms and carried out in the task's
    RETURN value — a mutated argument would not travel under a
    distributed executor, whose task results arrive serialized.  Which
    kinds get which accumulator is not decided here: each statistics
    module answers for its own, gated on the built score's class --
    :func:`~gain.genomic_resources.statistics.coverage.region_coverage_for`,
    :func:`~gain.genomic_resources.statistics.fragments.region_fragments_for`
    and
    :func:`~gain.genomic_resources.statistics.alleles.region_alleles_for`
    -- and the three are asked independently, so a kind publishes any
    of them without the others.  An allele score's :class:`RegionAlleles`
    has one extra condition on the bulk path: it needs the nucleotides,
    so a backend that will not serve them sends the region back to the
    per-record read rather than to a statistic with no class data.

    ONE score serves the whole invocation -- the three gates, the allele
    probe below, the bulk gate, and whichever scan takes the region; see
    :func:`_score_for`.  An allele score's probe OPENS it and hands it
    on closed; the scan reopens it, as it would have opened a fresh one.

    Built BEFORE the ``try``, unlike :func:`do_min_max_task`'s, because
    the gates and the allele probe need it and both precede the ``try``.
    So a construction that refuses this resource is not attributed here.
    """
    score = build_score_from_resource(resource)
    coverage = region_coverage_for(score, chrom, start, end)
    fragments = region_fragments_for(score, chrom, start, end)
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
                coverage=coverage, fragments=fragments,
                alleles=alleles, score=score)
        else:
            histograms = do_histogram(
                resource, all_hist_confs, chrom, start, end,
                coverage=coverage, fragments=fragments,
                alleles=alleles, score=score)
        return RegionScanResult(histograms, coverage, fragments, alleles)
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
    resource: GenomicResource,  # ruff: ignore[unused-function-argument]
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
    ScoreImplementationBase._save_and_plot_histograms(  # ruff: ignore[private-member-access]
        resource, build_score_from_resource(resource),
        merged_histograms)
    return merged_histograms


def merge_and_save_histograms(
    resource: GenomicResource,
    *results: RegionScanResult,
) -> dict[str, Histogram]:
    """Fold every region's scan results together and save all four.

    The scan's last task.  Histograms, coverage, fragments and alleles
    each merge across the regions and are written into the resource --
    the one place any of the four is produced, so no SECOND code path
    can refresh one of them and leave the others stale.  Within this
    one the writes are sequential and there is no rollback, so a raise
    partway does leave a mixture.
    """
    merged_histograms = merge_histograms(
        resource, *(result.histograms for result in results))
    save_and_plot_coverage(resource, merge_region_coverage(
        resource.resource_id,
        (result.coverage for result in results)))
    save_and_plot_fragments(resource, merge_region_fragments(
        resource.resource_id,
        (result.fragments for result in results)))
    save_allele_statistics(resource, merge_region_alleles(
        resource.resource_id,
        (result.alleles for result in results)))
    return _save_histograms(
        resource, merged_histograms)
