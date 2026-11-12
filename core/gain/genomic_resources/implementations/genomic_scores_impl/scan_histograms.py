"""The histogram pass of the statistics scan, and its merge and save.

The per-record and vectorized histogram scans of a region, the task body
that chooses between them, and the last task of a statistics build, which
folds every region together and saves the result.  The shared read and
the gates of the vectorized path stay in :mod:`.scan`.
"""
from __future__ import annotations

import functools
from typing import Any

import numpy as np

from gain import logging
from gain.genomic_resources.cli_errors import report_resource_failure
from gain.genomic_resources.genomic_scores import (
    GenomicScore,
    RecordArrays,
    build_score_from_resource,
    owns_record,
)
from gain.genomic_resources.histogram import (
    Histogram,
    HistogramConfig,
    NullHistogram,
    NullHistogramConfig,
    NumberHistogramConfig,
    build_empty_histogram,
)
from gain.genomic_resources.repository import (
    GenomicResource,
)
from gain.genomic_resources.resource_errors import (
    HistogramError,
    MalformedResourceError,
)
from gain.genomic_resources.score_def import ScoreValue
from gain.genomic_resources.score_implementation import (
    save_and_plot_histograms,
)
from gain.genomic_resources.statistics.alleles import (
    RegionAlleles,
    merge_region_alleles,
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
from gain.genomic_resources.statistics.min_max import (
    MinMaxValue,
    NullMinMaxValue,
)

from .scan import (
    _SCAN_BATCH_SIZE,
    RegionScanResult,
    _accumulate_arrays,
    _allele_batches,
    _score_for,
    bulk_region_scan,
    can_bulk_histogram,
    scan_region,
)

logger = logging.getLogger(__name__)

__all__ = [
    "do_histogram",
    "do_histogram_bulk",
    "do_histogram_task",
    "merge_and_save_histograms",
    "merge_histograms",
    "update_hist_confs",
]


def update_hist_confs(
    all_hist_confs: dict[str, HistogramConfig],
    minmax_task: dict[str, MinMaxValue] | None,
) -> dict[str, HistogramConfig]:
    """Give each number histogram the view range its min/max found.

    ``None`` means no min/max pass ran, so the configs stand as they
    are.  A score whose min or max came back nan has no values to
    bin -- its histogram is nullified with that as the reason, which
    is a resource fact worth reporting, not a failure to raise on.

    A score the min/max pass REFUSED arrives here the same way, since a
    refusal leaves the nan seed untouched, but it is a different fact about
    the resource -- values that could not be folded, rather than no values --
    so it is nullified with the refusal as the reason instead.
    """
    if minmax_task is None:
        return all_hist_confs

    for score_id, min_max in minmax_task.items():
        hist_conf = all_hist_confs[score_id]
        assert isinstance(hist_conf, NumberHistogramConfig)
        assert not hist_conf.has_view_range()
        if isinstance(min_max, NullMinMaxValue):
            # Same nullify, different resource fact: a score with no values in
            # the region and a score whose values could not be folded both
            # arrive here with nothing to bin, and a curator reading the
            # reason has to be able to tell them apart.
            logger.warning(
                "min/max for %s was refused; "
                "nullify the histogram: %s", score_id, min_max.reason)
            all_hist_confs[score_id] = NullHistogramConfig(
                f"min/max for {score_id} refused: {min_max.reason}")
        elif np.isnan(min_max.min) or np.isnan(min_max.max):
            logger.warning(
                "min/max value for %s not found; "
                "nullify the histogram", score_id)
            all_hist_confs[score_id] = NullHistogramConfig(
                f"min/max for {score_id} not found")
        else:
            hist_conf.view_range = (min_max.min, min_max.max)
    logger.info("histogram configs updated: %s", all_hist_confs)
    return all_hist_confs


def _add_record_values(
    result: dict[str, Histogram],
    score_ids: list[str],
    rec: list[ScoreValue],
    weight: int,
    *,
    resource: GenomicResource,
    chrom: str,
    start: int | None,
    end: int | None,
) -> None:
    """Add the score values of one record to their histograms.

    Each score of the record goes to its own histogram in ``result``,
    at ``weight``.  A histogram that refuses its value is replaced IN
    PLACE in ``result`` by a null histogram, and the other scores of
    the record carry on.
    """
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
            _add_record_values(
                result, score_ids, rec, weight,
                resource=resource, chrom=chrom, start=start, end=end)
    return result


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

    A score the min/max pass nullified rides along as its
    ``NullHistogram`` -- unscanned, but in the result, so the merge
    carries the reason to the save (:func:`_build_nullified_histograms`).

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
        histograms.update(_build_nullified_histograms(all_hist_confs))
        return RegionScanResult(histograms, coverage, fragments, alleles)
    except MalformedResourceError as err:
        report_resource_failure(
            err, "could not build the histograms of",
            resource.resource_id)
        raise


def _build_nullified_histograms(
    all_hist_confs: dict[str, HistogramConfig],
) -> dict[str, NullHistogram]:
    """The null histograms of the scores the build nullified.

    A ``NullHistogramConfig`` here is the min/max pass's own
    (:func:`update_hist_confs`; :func:`unpack_score_defs` admits no
    other), so it is recorded the way a histogram nullified accumulating
    is: a ``NullHistogram`` carrying the reason, serialised with the
    resource's statistics.
    """
    return {
        score_id: NullHistogram(hist_conf)
        for score_id, hist_conf in all_hist_confs.items()
        if isinstance(hist_conf, NullHistogramConfig)
    }


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
    save_and_plot_histograms(
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
