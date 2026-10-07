# pylint: disable=C0114,C0116
# ruff: file-ignore[suspicious-non-cryptographic-random-usage]
# Seeded `random` builds test data here, not secrets.
"""The coverage profile and the ``coverage_profile`` fold, at their seam.

:func:`coverage_profile` turns fragments in start order into the runs of
their coverage profile -- the sum of the values of the fragments over
each position, as maximal runs of one non-zero value.  The run cases are
the prototype's (``iossifovlab/binning_tool_prototype``,
``tests/test_b.py``, ``fragment_coverage``).  The fold feeds those runs to
the ``fragment_length`` fold, so each bin reduces the profile weighted by
base pairs, and a position the profile leaves at 0 is uncovered.
"""
import math
import random
from collections.abc import Sequence
from itertools import starmap

import numpy as np
import pytest
from gain.binning.fragment_folds import (
    BinFragmentCoverageAggregator,
    BinValue,
    CoverageProfile,
    FragmentValue,
    coverage_profile,
)


def runs(*fragments: tuple[int, int, float]) -> list[FragmentValue]:
    return list(coverage_profile(starmap(FragmentValue, fragments)))


def test_no_fragment_has_no_profile() -> None:
    assert not runs()


def test_a_single_fragment_is_its_own_profile() -> None:
    assert runs((5, 9, 3)) == [FragmentValue(5, 9, 3)]


def test_overlapping_fragments_sum_where_they_overlap() -> None:
    assert runs((1, 5, 1), (3, 8, 1), (4, 4, 1)) == [
        FragmentValue(1, 2, 1), FragmentValue(3, 3, 2),
        FragmentValue(4, 4, 3), FragmentValue(5, 5, 2),
        FragmentValue(6, 8, 1)]


def test_a_gap_between_fragments_is_in_no_run() -> None:
    assert runs((1, 2, 1), (20, 22, 1)) == [
        FragmentValue(1, 2, 1), FragmentValue(20, 22, 1)]


def test_touching_equal_fragments_merge_into_one_run() -> None:
    assert runs((1, 3, 2), (4, 6, 2), (7, 9, 2)) == [FragmentValue(1, 9, 2)]


def test_runs_merge_after_a_negative_value_restores_the_level() -> None:
    # 4-6 get 2 + 1 - 1 = 2, so 1-9 is one run.
    assert runs((1, 9, 2), (4, 6, 1), (4, 6, -1)) == [FragmentValue(1, 9, 2)]


def test_values_cancelling_to_zero_leave_a_position_in_no_run() -> None:
    assert runs((1, 9, 2), (4, 6, -2)) == [
        FragmentValue(1, 3, 2), FragmentValue(7, 9, 2)]


def test_a_long_fragment_runs_on_past_a_later_short_one() -> None:
    assert runs((1, 2, 1), (2, 30, 1)) == [
        FragmentValue(1, 1, 1), FragmentValue(2, 2, 2),
        FragmentValue(3, 30, 1)]


def test_float_values_are_summed_as_floats() -> None:
    profile = runs((1, 4, 0.5), (3, 6, 0.25))

    assert profile == [
        FragmentValue(1, 2, 0.5), FragmentValue(3, 4, 0.75),
        FragmentValue(5, 6, 0.25)]
    assert all(isinstance(run.value, float) for run in profile)


def naive_profile(fragments: list[FragmentValue]) -> list[FragmentValue]:
    """The profile summed base by base, its non-zero runs merged."""
    coverage: dict[int, float] = {}
    for fragment in fragments:
        assert isinstance(fragment.value, int)
        for position in range(fragment.start, fragment.end + 1):
            coverage[position] = coverage.get(position, 0) + fragment.value
    out: list[FragmentValue] = []
    for position in sorted(coverage):
        value = coverage[position]
        if value == 0:
            continue
        if out and out[-1].end + 1 == position and out[-1].value == value:
            out[-1] = FragmentValue(out[-1].start, position, value)
        else:
            out.append(FragmentValue(position, position, value))
    return out


@pytest.mark.parametrize("seed", range(20))
def test_the_profile_matches_a_naive_one_over_random_fragments(
    seed: int,
) -> None:
    rng = random.Random(seed)
    starts = sorted(rng.randint(1, 100) for _ in range(30))
    fragments = [
        FragmentValue(s, s + rng.randint(0, 15), rng.choice([-1, 1, 1, 2]))
        for s in starts]

    assert list(coverage_profile(fragments)) == naive_profile(fragments)


def test_a_fragment_out_of_start_order_is_refused() -> None:
    profile = CoverageProfile()
    profile.feed(FragmentValue(5, 9, 1))

    with pytest.raises(ValueError, match="sorted by start"):
        profile.feed(FragmentValue(4, 9, 1))


def fold(
    fragments: Sequence[tuple[int, int, float]], *, start: int, end: int,
    aggregator: str, uncovered_value: float | None = 0,
    bin_size: int = 10,
) -> list[BinValue]:
    """Every bin a coverage fold answers: ``feed``'s, then ``flush``'s."""
    coverage_fold = BinFragmentCoverageAggregator(
        start=start, end=end, bin_size=bin_size, aggregator=aggregator,
        uncovered_value=uncovered_value)
    bins: list[BinValue] = []
    for fragment in fragments:
        bins.extend(coverage_fold.feed(FragmentValue(*fragment)))
    bins.extend(coverage_fold.flush())
    return bins


#: The brief's worked example: the profile is 1 on 6-10, 2 on 11-14,
#: 1 on 15-18 and 0 elsewhere in 1-30.
WORKED = [(6, 14, 1), (11, 18, 1)]


@pytest.mark.parametrize("aggregator, uncovered_value, expected", [
    # 1-10: 5 bp of 1 and 5 uncovered; 11-20: 4 bp of 2, 4 of 1, 2
    # uncovered; 21-30 uncovered.
    ("mean", 0, [0.5, 1.2, 0.0]),
    ("mean", None, [1.0, 1.5, math.nan]),
    ("sum", 0, [5, 12, 0]),
    ("sum", None, [5, 12, 0]),
    ("max", 0, [1, 2, 0]),
    ("min", 0, [0, 0, 0]),
    ("min", None, [1, 1, math.nan]),
])
def test_overlapping_fragments_fold_their_profile(
    aggregator: str, uncovered_value: float | None, expected: list[float],
) -> None:
    bins = fold(WORKED, start=1, end=30, aggregator=aggregator,
                uncovered_value=uncovered_value)

    assert [(b.start, b.end) for b in bins] == [(1, 10), (11, 20), (21, 30)]
    np.testing.assert_array_equal([b.value for b in bins], expected)


def test_adjacent_equal_runs_fold_as_one_run() -> None:
    # 3-12 and 13-17 make one run of 2 over 3-17: its median in 11-20 is
    # 2 over 7 bp against 3 uncovered.
    bins = fold([(3, 12, 2), (13, 17, 2)], start=1, end=20,
                aggregator="median")

    assert [b.value for b in bins] == [2.0, 2.0]


def test_a_fragment_ending_after_a_later_one_still_reaches_its_bins() -> None:
    # (2, 35) covers 2-35 with 1; (5, 7) lifts 5-7 to 3.
    bins = fold([(2, 35, 1), (5, 7, 2)], start=1, end=40, aggregator="sum")

    np.testing.assert_array_equal(
        [b.value for b in bins], [9 + 6, 10, 10, 5])


def test_float_values_are_not_truncated() -> None:
    # 1-2 is 0.5, 3-4 0.75, 5-6 0.25: the sum is 1 + 1.5 + 0.5.
    bins = fold([(1, 4, 0.5), (3, 6, 0.25)], start=1, end=10,
                aggregator="sum")

    assert [b.value for b in bins] == [3.0]


def test_a_fragment_and_a_gap_longer_than_10_000_bp() -> None:
    # 1-12000 is 1, 12001-24000 uncovered, 24001-24010 is 4.
    bins = fold([(1, 12_000, 1), (24_001, 24_010, 4)],
                start=1, end=30_000, aggregator="sum", bin_size=6_000)

    np.testing.assert_array_equal(
        [b.value for b in bins], [6_000, 6_000, 0, 0, 40])
