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
    BinFragmentLengthAggregator,
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


def length_fold(
    fragments: list[tuple[int, int, float]], *, start: int, end: int,
    aggregator: str, uncovered_value: float | None = None,
    bin_size: int = 10,
) -> list[BinValue]:
    """Every bin a length fold answers: those ``feed`` emits, then ``flush``."""
    fold_ = BinFragmentLengthAggregator(
        start=start, end=end, bin_size=bin_size, aggregator=aggregator,
        uncovered_value=uncovered_value)
    bins: list[BinValue] = []
    for fragment in fragments:
        bins.extend(fold_.feed(FragmentValue(*fragment)))
    bins.extend(fold_.flush())
    return bins


def test_a_fragment_inside_one_bin_adds_its_value_weighted_by_its_length(
) -> None:
    # (3, 6) covers 4 bp of 1-10: a sum of 4 * 2.5; 11-20 is empty.
    bins = length_fold([(3, 6, 2.5)], start=1, end=20, aggregator="sum")

    assert bins == [(1, 10, 10.0), (11, 20, 0.0)]


def test_a_fragment_across_two_bins_adds_its_overlap_to_each() -> None:
    # (8, 13): 3 bp in 1-10, 3 bp in 11-20.
    bins = length_fold([(8, 13, 2)], start=1, end=20, aggregator="sum")

    assert bins == [(1, 10, 6.0), (11, 20, 6.0)]


def test_a_fragment_across_three_bins_adds_its_overlap_to_each() -> None:
    # (8, 23): 3 bp in 1-10, all 10 of 11-20, 3 bp in 21-30.
    bins = length_fold([(8, 23, 1)], start=1, end=40, aggregator="sum")

    assert bins == [(1, 10, 3.0), (11, 20, 10.0), (21, 30, 3.0),
                    (31, 40, 0.0)]


def test_overlapping_fragments_each_add_their_own_overlap() -> None:
    # In 11-20: (5, 15) 5 bp of 1, (12, 25) 9 bp of 3 -> (5 + 27) / 14.
    bins = length_fold([(5, 15, 1), (12, 25, 3)],
                       start=1, end=30, aggregator="mean")

    assert bins == [(1, 10, 1.0), (11, 20, 32 / 14), (21, 30, 3.0)]


def test_a_long_fragment_still_reaches_the_bins_after_a_short_one() -> None:
    # (5, 35) is fed first; (12, 14) starts and ends inside it, and its
    # start answers 1-10 only: 21-30 and 31-40 still hold the long one.
    bins = length_fold([(5, 35, 2), (12, 14, 10)],
                       start=1, end=40, aggregator="sum")

    assert bins == [(1, 10, 12.0), (11, 20, 50.0), (21, 30, 20.0),
                    (31, 40, 10.0)]


def test_two_fragments_with_one_start_and_different_ends() -> None:
    # Both start at 8; (8, 12) reaches 11-20 for 2 bp, (8, 27) for 10.
    bins = length_fold([(8, 12, 1), (8, 27, 1)],
                       start=1, end=30, aggregator="sum")

    assert bins == [(1, 10, 6.0), (11, 20, 12.0), (21, 30, 7.0)]


def test_an_uncovered_value_fills_the_gaps_between_fragments() -> None:
    # 1-10: (3, 4) 2 bp of 6, 8 bp uncovered of 1; 11-20: (15, 16) 2 bp
    # of 6, 8 bp of 1; 21-30 all uncovered.
    bins = length_fold([(3, 4, 6), (15, 16, 6)],
                       start=1, end=30, aggregator="sum", uncovered_value=1)

    assert bins == [(1, 10, 20.0), (11, 20, 20.0), (21, 30, 10.0)]


def test_flush_fills_the_tail_to_the_region_s_end_only() -> None:
    # The region ends at 25: 21-30 holds 21-25 uncovered, not 26-30.
    length = BinFragmentLengthAggregator(
        start=1, end=25, bin_size=10, aggregator="sum", uncovered_value=2)

    fed = length.feed(FragmentValue(12, 13, 5))
    flushed = length.flush()

    assert fed == [(1, 10, 20.0)]
    assert flushed == [(11, 20, 26.0), (21, 30, 10.0)]


def test_an_uncovered_value_does_not_fill_an_edge_bin_outside_the_region(
) -> None:
    # 15-24: 11-20 holds 15-20 (6 bp), 21-30 holds 21-24 (4 bp).
    bins = length_fold([], start=15, end=24, aggregator="sum",
                       uncovered_value=1)

    assert bins == [(11, 20, 6.0), (21, 30, 4.0)]


@pytest.mark.parametrize("aggregator, empty", [
    ("sum", 0.0), ("mean", math.nan), ("max", math.nan), ("min", math.nan),
    ("median", math.nan),
])
def test_without_an_uncovered_value_an_empty_bin_holds_the_empty_value(
    aggregator: str, empty: float,
) -> None:
    bins = length_fold([(3, 4, 2)], start=1, end=20, aggregator=aggregator)

    np.testing.assert_equal(bins[1], (11, 20, empty))


@pytest.mark.parametrize("aggregator, expected", [
    # 1-10: (2, 4) 3 bp of 1, (6, 10) 5 bp of 5, 2 bp uncovered of 0;
    # the median of 0 0 1 1 1 5 5 5 5 5 is (1 + 5) / 2.
    ("sum", 28.0), ("mean", 2.8), ("max", 5.0), ("min", 0.0),
    ("median", 3.0),
])
def test_each_aggregator_weighs_by_overlap(
    aggregator: str, expected: float,
) -> None:
    bins = length_fold([(2, 4, 1), (6, 10, 5)], start=1, end=10,
                       aggregator=aggregator, uncovered_value=0)

    assert bins == [(1, 10, expected)]


@pytest.mark.parametrize("fragment", [(14, 20, 1), (15, 35, 1)])
def test_a_length_fold_refuses_a_fragment_outside_the_region(
    fragment: tuple[int, int, int],
) -> None:
    length = BinFragmentLengthAggregator(
        start=15, end=34, bin_size=10, aggregator="sum")

    with pytest.raises(ValueError, match=(
            rf"a fragment at \[{fragment[0]}, {fragment[1]}\] is not "
            r"inside the binned region \[15, 34\]")):
        length.feed(FragmentValue(*fragment))


def test_a_length_fold_refuses_a_fragment_out_of_start_order() -> None:
    length = BinFragmentLengthAggregator(
        start=1, end=40, bin_size=10, aggregator="sum")
    length.feed(FragmentValue(12, 13, 1))

    with pytest.raises(ValueError, match="sorted by start"):
        length.feed(FragmentValue(11, 30, 1))


# The prototype's five length cases, on the global grid: a region ending
# at 25 answers the whole bin 21-30 and fills it to 25 only.
PROTOTYPE_FRAGMENTS = [(9, 14, 1), (14, 18, 2), (18, 26, 3)]


def test_prototype_length() -> None:
    bins = length_fold(PROTOTYPE_FRAGMENTS, start=1, end=30,
                       aggregator="mean")

    assert bins == [
        (1, 10, 1.0), (11, 20, (4 * 1 + 5 * 2 + 3 * 3) / (4 + 5 + 3)),
        (21, 30, 3.0)]


def test_prototype_length_with_default_value() -> None:
    bins = length_fold(PROTOTYPE_FRAGMENTS, start=1, end=30,
                       aggregator="mean", uncovered_value=0)

    assert bins == [
        (1, 10, 2 * 1.0 / 10),
        (11, 20, (4 * 1 + 5 * 2 + 3 * 3) / (4 + 5 + 3)),
        (21, 30, 6 * 3.0 / 10)]


def test_prototype_length_fragment_spanning_all_bins() -> None:
    # The prototype's (5, 40) as its caller clips it to 1-25.
    bins = length_fold([(5, 25, 7)], start=1, end=25, aggregator="mean")

    assert bins == [(1, 10, 7.0), (11, 20, 7.0), (21, 30, 7.0)]


def test_prototype_length_no_fragments_with_default_value() -> None:
    bins = length_fold([], start=1, end=25, aggregator="mean",
                       uncovered_value=0)

    assert bins == [(1, 10, 0.0), (11, 20, 0.0), (21, 30, 0.0)]


def test_prototype_length_identical_fragments_with_default_value() -> None:
    bins = length_fold([(3, 4, 1), (3, 4, 5)], start=1, end=25,
                       aggregator="mean", uncovered_value=0)

    assert bins == [
        (1, 10, (2 * 1 + 2 * 5 + 8 * 0) / (2 + 2 + 8)),
        (11, 20, 0.0), (21, 30, 0.0)]
