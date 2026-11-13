"""The min/max pass of the statistics scan.

The per-record and vectorized min/max scans of a region, the task body
that chooses between them, and the merge that gives each number histogram
the view range its min/max found.  The shared read and the gates of the
vectorized path stay in :mod:`.scan`.
"""
from __future__ import annotations

import numpy as np

from gain import logging
from gain.genomic_resources.cli_errors import report_resource_failure
from gain.genomic_resources.genomic_scores import (
    GenomicScore,
    RecordArrays,
    build_score_from_resource,
    owned_records_mask,
    owns_record,
)
from gain.genomic_resources.histogram import (
    HistogramConfig,
)
from gain.genomic_resources.repository import (
    GenomicResource,
)
from gain.genomic_resources.resource_errors import (
    MalformedResourceError,
)
from gain.genomic_resources.statistics.min_max import (
    MinMaxValue,
    NullMinMaxValue,
)

from .scan import (
    _score_for,
    bulk_region_scan,
    can_bulk_min_max,
    scan_region,
)
from .scan_histograms import update_hist_confs

logger = logging.getLogger(__name__)

__all__ = [
    "do_min_max",
    "do_min_max_bulk",
    "do_min_max_task",
    "merge_min_max",
]


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

    A value the reducer refuses nullifies THAT score and the pass carries
    on, exactly as :func:`do_histogram` has always done -- the asymmetry
    between the two was what made a single unfoldable value cost a whole
    statistics build (gain#1285, gain#1313).  The refused score is replaced
    with a :class:`~gain.genomic_resources.statistics.min_max.NullMinMaxValue`,
    which is the latch as much as the outcome: its ``add_value`` is a no-op,
    so the refusal costs one exception per region rather than one per record.

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
                try:
                    result[score_id].add_value(
                        rec[score_index],  # type: ignore
                    )
                except TypeError as err:
                    # The containment ``do_histogram`` has had all along: one
                    # score the reducer refuses costs THAT score, not the
                    # resource's whole statistics build (gain#1285).  The
                    # replacement is the latch as much as the outcome --
                    # ``NullMinMaxValue.add_value`` is a no-op, so the refusal
                    # is paid once per region per score rather than once per
                    # record, which is the only reason a per-value catch is
                    # affordable in this loop.
                    logger.exception(
                        "Failed adding value %s to the min/max of %s in "
                        "<%s> at %s:%s-%s; nullify the score's min/max",
                        rec[score_index] if rec else None, score_id,
                        resource.resource_id, chrom, start, end)
                    result[score_id] = NullMinMaxValue(score_id, str(err))
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

    A region that REFUSED a score nullifies it for the whole fold, the way
    ``merge_histograms`` lets one region's ``NullHistogram`` nullify a score:
    the regions that folded values cannot answer for the one that could not.
    """
    res: dict[str, MinMaxValue] = {}
    for score_id in score_ids:
        for min_max_region in calculate_tasks:
            region_min_max = min_max_region[score_id]
            # A refusal travels, exactly as a region's ``NullHistogram`` does
            # in ``merge_histograms``: the regions that DID fold values
            # cannot answer for the one that could not, and a view range
            # built from the survivors alone would bin the score as if
            # nothing had been refused.  Only this direction needs saying --
            # ``NullMinMaxValue.merge`` is a no-op, so a score already
            # refused stays refused whatever is folded into it.
            if res.get(score_id) is None \
                    or isinstance(region_min_max, NullMinMaxValue):
                res[score_id] = region_min_max
            else:
                res[score_id].merge(region_min_max)
    return update_hist_confs(
        all_hist_confs, res)


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
