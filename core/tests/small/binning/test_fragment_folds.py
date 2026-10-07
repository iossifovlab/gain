# pylint: disable=C0114,C0116
"""The streaming bin folds of ``fragment_score_binner``, at their seam.

A fold is fed ``(start, end, value)`` fragments in start order and
answers ``(bin start, bin end, value)`` bins of the global grid anchored
at position 1 -- whole grid bins, so a region starting mid-bin answers
the whole bin holding its start.  The start cases are the prototype's
(``iossifovlab/binning_tool_prototype``, ``tests/test_b.py``), their
expected bins re-derived for that grid.
"""
import math

import numpy as np
import pytest
from gain.binning.fragment_folds import (
    BinFragmentStartAggregator,
    BinValue,
    FragmentValue,
)


def fold(
    fragments: list[tuple[int, int, float]], *, start: int, end: int,
    aggregator: str, bin_size: int = 10,
) -> list[BinValue]:
    """Every bin a start fold answers: those ``feed`` emits, then ``flush``."""
    start_fold = BinFragmentStartAggregator(
        start=start, end=end, bin_size=bin_size, aggregator=aggregator)
    bins: list[BinValue] = []
    for fragment in fragments:
        bins.extend(start_fold.feed(FragmentValue(*fragment)))
    bins.extend(start_fold.flush())
    return bins


def test_the_prototype_s_start_case_on_the_global_grid() -> None:
    # (18, 26) crosses into 21-30 yet counts in 11-20 only, by its start.
    bins = fold([(9, 14, 1), (14, 18, 2), (18, 26, 3)],
                start=1, end=30, aggregator="mean")

    np.testing.assert_equal(bins, [
        (1, 10, 1.0), (11, 20, 2.5), (21, 30, math.nan)])


def test_a_fragment_on_the_last_base_of_a_bin_belongs_to_that_bin() -> None:
    bins = fold([(10, 10, 5), (11, 12, 7)],
                start=1, end=20, aggregator="sum")

    assert bins == [(1, 10, 5.0), (11, 20, 7.0)]


def test_a_fragment_crossing_bin_edges_counts_in_its_start_bin_only() -> None:
    bins = fold([(8, 35, 1)], start=1, end=40, aggregator="count")

    assert bins == [
        (1, 10, 1.0), (11, 20, 0.0), (21, 30, 0.0), (31, 40, 0.0)]


@pytest.mark.parametrize("aggregator, empty", [
    ("count", 0.0), ("sum", 0.0), ("mean", math.nan), ("max", math.nan),
    ("min", math.nan), ("median", math.nan), ("product", math.nan),
])
def test_an_empty_bin_holds_the_aggregator_s_empty_value(
    aggregator: str, empty: float,
) -> None:
    # 11-20 has no fragment; 1-10 and 21-30 have one each.
    bins = fold([(3, 4, 2), (25, 26, 3)],
                start=1, end=30, aggregator=aggregator)

    np.testing.assert_equal(bins[1], (11, 20, empty))


def test_the_bins_after_the_last_fragment_come_from_flush() -> None:
    start_fold = BinFragmentStartAggregator(
        start=1, end=40, bin_size=10, aggregator="sum")

    fed = start_fold.feed(FragmentValue(12, 30, 4))
    flushed = start_fold.flush()

    assert fed == [(1, 10, 0.0)]
    assert flushed == [(11, 20, 4.0), (21, 30, 0.0), (31, 40, 0.0)]


def test_a_fold_only_flushed_answers_every_bin_empty() -> None:
    bins = fold([], start=1, end=30, aggregator="mean")

    np.testing.assert_equal(bins, [
        (1, 10, math.nan), (11, 20, math.nan), (21, 30, math.nan)])


def test_a_region_starting_mid_bin_answers_whole_global_grid_bins() -> None:
    # 15-34 spans the grid bins 11-20, 21-30 and 31-40, unclipped; the
    # prototype's region-anchored grid would answer 15-24, 25-34.
    bins = fold([(15, 16, 1), (24, 40, 2), (34, 34, 3)],
                start=15, end=34, aggregator="sum")

    assert bins == [(11, 20, 1.0), (21, 30, 2.0), (31, 40, 3.0)]


@pytest.mark.parametrize("fragment_start", [14, 35])
def test_a_fragment_starting_outside_the_region_is_refused(
    fragment_start: int,
) -> None:
    # 14 and 35 lie in the region's edge bins, yet outside [15, 34].
    start_fold = BinFragmentStartAggregator(
        start=15, end=34, bin_size=10, aggregator="sum")

    with pytest.raises(ValueError, match=(
            rf"a fragment at {fragment_start} is outside the binned "
            r"region \[15, 34\]")):
        start_fold.feed(FragmentValue(fragment_start, 40, 1))


def test_a_fragment_out_of_start_order_is_refused() -> None:
    start_fold = BinFragmentStartAggregator(
        start=1, end=40, bin_size=10, aggregator="sum")
    start_fold.feed(FragmentValue(12, 13, 1))

    with pytest.raises(ValueError, match=(
            "a fragment at 11 arrived after one at 12; a bin fold needs "
            "its fragments sorted by start")):
        start_fold.feed(FragmentValue(11, 30, 1))


@pytest.mark.parametrize("aggregator, values, expected", [
    ("sum", [10**308, 10**308], math.inf),
    ("sum", [-(10**308), -(10**308)], -math.inf),
    ("product", [10**200, 10**200], math.inf),
    ("product", [10**200, -(10**200)], -math.inf),
])
def test_an_int_result_too_large_for_a_float_saturates(
    aggregator: str, values: list[int], expected: float,
) -> None:
    # 2 * 10**308 and 10**400 have no float: the bin saturates, as
    # fold_into_bins does, rather than raising OverflowError.
    bins = fold([(3, 4, value) for value in values],
                start=1, end=10, aggregator=aggregator)

    assert bins == [(1, 10, expected)]
