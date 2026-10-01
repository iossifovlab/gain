"""The binned fold: start-sorted ``(position, column, value)`` to a block.

:func:`~gain.genomic_resources.genomic_scores.aggregation.fold_into_bins`
is pure, so it is pinned here on literal records, with no resource behind
it.  Its read over a fragment score is pinned in
``test_fragment_binned_read.py``.
"""
# pylint: disable=C0116
import math
import re
from collections.abc import Iterator

import numpy as np
import pytest
from gain.genomic_resources.genomic_scores.aggregation import (
    fold_into_bins,
)


def test_the_block_has_one_row_per_global_grid_bin_and_one_column_each(
) -> None:
    """``[5, 25]`` with bins of 10 touches bins 0, 1 and 2 of the grid."""
    block = fold_into_bins(
        [(5, 0, 1.0), (12, 0, 1.0), (13, 1, 7.0), (25, 0, 1.0)],
        start=5, end=25, bin_size=10, aggregators=["count", "sum"])

    assert block.dtype == np.float64
    np.testing.assert_array_equal(block, [
        [1.0, 0.0],
        [1.0, 7.0],
        [1.0, 0.0],
    ])


@pytest.mark.parametrize("aggregator,empty", [
    ("count", 0.0),
    ("sum", 0.0),
    ("mean", math.nan),
    ("max", math.nan),
    ("min", math.nan),
    ("median", math.nan),
    ("product", math.nan),
])
def test_an_empty_bin_is_zero_for_count_and_sum_and_nan_otherwise(
    aggregator: str, empty: float,
) -> None:
    """Bin 1 of ``[1, 30]`` receives nothing; bins 0 and 2 receive a 2."""
    block = fold_into_bins(
        [(3, 0, 2.0), (25, 0, 2.0)],
        start=1, end=30, bin_size=10, aggregators=[aggregator])

    np.testing.assert_array_equal(block[1], [empty])
    assert not math.isnan(block[0, 0])
    assert not math.isnan(block[2, 0])


def test_a_bin_whose_every_value_is_null_counts_as_empty() -> None:
    """Null values are skipped, so a bin of nulls holds the empty value."""
    block = fold_into_bins(
        [(3, 0, None), (3, 1, None), (4, 0, None), (4, 1, None)],
        start=1, end=10, bin_size=10, aggregators=["count", "max"])

    np.testing.assert_array_equal(block, [[0.0, math.nan]])


@pytest.mark.parametrize("aggregator", [
    "join(,)", "list", "concatenate", "mode", "bool", "value_count",
    "no_such_aggregator",
])
def test_an_aggregator_that_cannot_answer_a_float_is_refused_unread(
    aggregator: str,
) -> None:
    """Refused by name, before the first record is asked for."""
    def records() -> Iterator[tuple[int, int, float]]:
        raise AssertionError("the fold read a record before refusing")
        yield  # pylint: disable=unreachable

    with pytest.raises(
            ValueError, match=rf"{re.escape(aggregator)}.*float"):
        fold_into_bins(
            records(), start=1, end=10, bin_size=10,
            aggregators=["count", aggregator])


@pytest.mark.parametrize("start,end,n_bins", [
    (1, 10, 1),     # exactly one grid bin
    (10, 11, 2),    # the last base of bin 0 and the first of bin 1
    (15, 15, 1),    # one base, mid-bin
    (15, 34, 3),    # starts and ends mid-bin
    (11, 40, 3),    # starts and ends on bin edges
])
def test_the_row_count_is_the_grid_bins_the_region_touches(
    start: int, end: int, n_bins: int,
) -> None:
    block = fold_into_bins(
        [], start=start, end=end, bin_size=10, aggregators=["count"])

    assert block.shape == (n_bins, 1)


def test_a_record_on_a_bins_last_base_lands_in_that_bin() -> None:
    block = fold_into_bins(
        [(10, 0, 1.0), (11, 0, 1.0), (11, 0, 1.0)],
        start=1, end=20, bin_size=10, aggregators=["count"])

    np.testing.assert_array_equal(block, [[1.0], [2.0]])


def test_adjacent_regions_split_on_a_bin_edge_tile_the_whole() -> None:
    records = [
        (3, 0, 1.0), (9, 1, 4.0), (10, 0, 2.0), (11, 1, 5.0),
        (20, 0, 3.0), (21, 1, 6.0), (33, 0, 7.0),
    ]
    aggregators = ["sum", "max"]

    left = fold_into_bins(
        [r for r in records if r[0] <= 20],
        start=3, end=20, bin_size=10, aggregators=aggregators)
    right = fold_into_bins(
        [r for r in records if r[0] > 20],
        start=21, end=35, bin_size=10, aggregators=aggregators)
    whole = fold_into_bins(
        records, start=3, end=35, bin_size=10, aggregators=aggregators)

    np.testing.assert_array_equal(np.vstack([left, right]), whole)


@pytest.mark.parametrize("position", [4, 26])
def test_a_record_outside_the_region_is_refused(position: int) -> None:
    """Even one inside an edge bin: the edge rows hold the region only."""
    with pytest.raises(ValueError, match=rf"{position}.*\[5, 25\]"):
        fold_into_bins(
            [(position, 0, 1.0)],
            start=5, end=25, bin_size=10, aggregators=["count"])


def test_records_out_of_position_order_are_refused() -> None:
    """A record behind its predecessor would reopen a bin already folded."""
    with pytest.raises(ValueError, match=r"12.*after.*13"):
        fold_into_bins(
            [(5, 0, 1.0), (13, 0, 1.0), (12, 0, 1.0)],
            start=1, end=20, bin_size=10, aggregators=["count"])


def test_a_bin_size_below_one_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one position"):
        fold_into_bins([], start=1, end=20, bin_size=0, aggregators=["count"])


@pytest.mark.parametrize("sign,expected", [(1, math.inf), (-1, -math.inf)])
def test_an_int_result_past_the_float_range_is_a_signed_infinity(
    sign: int, expected: float,
) -> None:
    """An exact int ``product`` past ~1.8e308 saturates, as a float would.

    A Python int that large does not convert to a float at all, so without
    saturation the whole block would be refused partway through.
    """
    block = fold_into_bins(
        [(1, 0, sign * 10 ** 200), (2, 0, 10 ** 200), (15, 0, 3)],
        start=1, end=20, bin_size=10, aggregators=["product"])

    np.testing.assert_array_equal(block, [[expected], [3.0]])
