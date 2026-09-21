# pylint: disable=C0114,C0116,W0212
"""The count, sum and sum-of-squares a number histogram folds beside its
bars, and the ``count`` / ``mean`` / ``std`` read off them (gain#1589)."""
import json
import logging

import numpy as np
import pytest
from gain.genomic_resources.histogram import (
    NumberHistogram,
    NumberHistogramConfig,
)


def _a_config(
    lo: float = 0, hi: float = 10, nbins: int = 10,
) -> NumberHistogramConfig:
    return NumberHistogramConfig.from_dict({
        "type": "number",
        "view_range": {"min": lo, "max": hi},
        "number_of_bins": nbins,
        "x_log_scale": False,
        "y_log_scale": False,
    })


def test_add_value_accumulates_the_weighted_count_sum_and_sum_of_squares(
) -> None:
    hist = NumberHistogram(_a_config())

    hist.add_value(2.0, count=3)
    hist.add_value(5.0)

    assert hist.count == 4
    assert hist.sum == 11.0
    assert hist.sum_of_squares == 37.0


def test_out_of_range_values_count_and_enter_the_sums_unclamped() -> None:
    hist = NumberHistogram(_a_config(lo=0, hi=10))

    hist.add_value(-4.0, count=2)   # below the view range
    hist.add_value(3.0, count=3)
    hist.add_value(20.0)            # above it

    assert hist.bars.sum() == 3
    assert hist.out_of_range_bins == [2, 1]
    assert hist.count == hist.bars.sum() + sum(hist.out_of_range_bins)
    assert hist.sum == -8.0 + 9.0 + 20.0
    assert hist.sum_of_squares == 32.0 + 27.0 + 400.0


def test_nan_and_none_are_skipped_by_the_accumulators_too() -> None:
    hist = NumberHistogram(_a_config())

    hist.add_value(np.nan, count=5)
    hist.add_value(None, count=5)
    hist.add_value(1.0)

    assert hist.count == 1
    assert hist.sum == 1.0


def _folded(*values: float) -> NumberHistogram:
    hist = NumberHistogram(_a_config())
    for value in values:
        hist.add_value(value)
    return hist


def test_merge_adds_the_accumulators() -> None:
    left = _folded(1.0, 2.0)
    right = _folded(3.0)

    left.merge(right)

    assert left.count == 3
    assert left.sum == 6.0
    assert left.sum_of_squares == 14.0


def test_merge_with_a_histogram_of_unknown_moments_is_unknown() -> None:
    # An old file's histogram has no accumulators, and merging one in
    # cannot invent them: absent means unknown, never empty.
    fresh = _folded(1.0, 2.0)
    old = NumberHistogram.from_dict({
        key: value for key, value in _folded(3.0).to_dict().items()
        if key not in ("count", "sum", "sum_of_squares")})
    assert old.count is None

    fresh.merge(old)

    assert fresh.count is None
    assert fresh.sum is None
    assert fresh.sum_of_squares is None
    # The bars still merged; only the moments are unknown.
    assert fresh.bars.sum() == 3


def test_the_accumulators_are_stored_beside_min_and_max_and_round_trip(
) -> None:
    hist = _folded(1.0, 2.0, 4.0)

    stored = json.loads(hist.serialize())
    loaded = NumberHistogram.deserialize(hist.serialize())

    assert stored["count"] == 3
    assert stored["sum"] == 7.0
    assert stored["sum_of_squares"] == 21.0
    assert (loaded.count, loaded.sum, loaded.sum_of_squares) == (
        3, 7.0, 21.0)


def test_a_file_without_the_accumulators_loads_with_unknown_moments(
) -> None:
    stored = _folded(1.0, 2.0).to_dict()
    for key in ("count", "sum", "sum_of_squares"):
        del stored[key]

    loaded = NumberHistogram.deserialize(json.dumps(stored))

    assert loaded.count is None
    assert loaded.sum is None
    assert loaded.sum_of_squares is None
    assert loaded.mean is None
    assert loaded.std is None


def test_mean_and_std_match_numpy_over_the_weighted_values() -> None:
    hist = NumberHistogram(_a_config(lo=0, hi=100))
    values = np.array([2.5, 7.0, 40.0, 41.5, 3.0])
    weights = np.array([3, 1, 2, 5, 1])
    hist.add_batch(values, weights)
    expanded = np.repeat(values, weights)

    assert hist.mean == pytest.approx(expanded.mean())
    # Population sd (``ddof=0``): the histogram describes the whole
    # resource, not a sample of it.
    assert hist.std == pytest.approx(expanded.std(ddof=0))


def test_mean_and_std_are_unknown_before_anything_is_folded() -> None:
    hist = NumberHistogram(_a_config())

    assert hist.count == 0
    assert hist.mean is None
    assert hist.std is None


def test_a_negative_variance_from_cancellation_is_clamped_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A constant value 1e8 folded 1e6 times: ``sum_of_squares / count``
    # and ``mean ** 2`` are the same 1e16 up to rounding, and rounding can
    # land the difference a hair below zero.  Forced rather than found, by
    # loading accumulators that disagree in the last place.
    hist = NumberHistogram.deserialize(json.dumps({
        **_folded(1.0).to_dict(),
        "count": 4,
        "sum": 8.0,
        "sum_of_squares": 16.0 - 1e-12,
    }))

    with caplog.at_level(logging.WARNING):
        std = hist.std

    assert std == 0.0
    assert "negative variance" in caplog.text
