"""The scan-side fold that turns a kind's regions into one statistic.

Not part of ``base_statistic`` because this fold reports through
``cli_errors``, and ``cli_errors`` reaches ``histogram``, which imports
``base_statistic`` -- importing it from there closes a cycle.  The line
it draws is a real one either way: ``base_statistic`` says what a
statistic IS, and stays free of anything CLI-facing.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from gain.genomic_resources.cli_errors import report_resource_failure
from gain.genomic_resources.statistics.base_statistic import (
    MergeableRegion,
    RegionFoldedStatistic,
    regions_in_genomic_order,
)


# `S` is not tied to the regions' type: the bound that would say so,
# `S: RegionFoldedStatistic[R]`, refers to an earlier type parameter and
# mypy 1.15 rejects it (`Name "R" is not defined`).  `Any` states that
# looseness instead of hiding it behind a bare `RegionFoldedStatistic`.
# Handing coverage regions to an `AlleleStatistics` therefore type
# checks; `RegionFoldedStatistic.merge`'s concrete-type gate is what
# refuses it at runtime.
def merge_regions[S: RegionFoldedStatistic[Any]](
    resource_id: str,
    regions: Iterable[MergeableRegion | None],
    # `Callable[[], S]` rather than `type[S]`: the concrete statistics
    # take no constructor arguments, but the BOUND's `__init__` takes an
    # id and a description, and `type[S]` is checked against the bound.
    build: Callable[[], S],
    what: str,
) -> S | None:
    """Fold one kind's scanned regions into a single statistic.

    ``None`` for a kind that contributed no regions at all -- the caller
    writes no file for it, which is how "this resource has no such
    statistic" is said.  A kind that produced nothing on SOME task is a
    ``None`` among the regions and is simply dropped.

    ``what`` names the statistic in the failure line, and is the same
    string :func:`~.base_statistic.refuse_unmergeable` names it by --
    the two halves of what an operator reads on one failure.  The
    failure is reported AND re-raised: reporting alone would leave the
    build writing a half-merged statistic.
    """
    ordered = regions_in_genomic_order(regions)
    if not ordered:
        return None
    statistics = build()
    try:
        for region in ordered:
            statistics.fold_region(region)
    except ValueError as err:
        report_resource_failure(
            err, f"could not merge the {what} of", resource_id)
        raise
    return statistics
