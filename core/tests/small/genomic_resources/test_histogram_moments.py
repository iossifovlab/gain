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
from gain.genomic_resources.statistics.moments import MOMENT_KEYS


def _a_config(lo: float = 0, hi: float = 10) -> NumberHistogramConfig:
    return NumberHistogramConfig((lo, hi), number_of_bins=10)


def _folded(*values: float) -> NumberHistogram:
    hist = NumberHistogram(_a_config())
    for value in values:
        hist.add_value(value)
    return hist


def _predating(*values: float) -> NumberHistogram:
    """``_folded`` as a file written before the accumulators would load."""
    stored = _folded(*values).to_dict()
    for key in MOMENT_KEYS:
        del stored[key]
    return NumberHistogram.deserialize(json.dumps(stored))


def _moments(hist: NumberHistogram) -> tuple[int, float, float]:
    assert hist.moments is not None
    return hist.moments.count, hist.moments.sum, hist.moments.sum_of_squares


def test_add_value_accumulates_the_weighted_count_sum_and_sum_of_squares(
) -> None:
    hist = NumberHistogram(_a_config())

    hist.add_value(2.0, count=3)
    hist.add_value(5.0)

    assert _moments(hist) == (4, 11.0, 37.0)


def test_out_of_range_values_count_and_enter_the_sums_unclamped() -> None:
    hist = NumberHistogram(_a_config(lo=0, hi=10))

    hist.add_value(-4.0, count=2)   # below the view range
    hist.add_value(3.0, count=3)
    hist.add_value(20.0)            # above it

    assert hist.bars.sum() == 3
    assert hist.out_of_range_bins == [2, 1]
    assert hist.count == hist.bars.sum() + sum(hist.out_of_range_bins)
    assert _moments(hist) == (6, -8.0 + 9.0 + 20.0, 32.0 + 27.0 + 400.0)


def test_nan_and_none_are_skipped_by_the_accumulators_too() -> None:
    hist = NumberHistogram(_a_config())

    hist.add_value(np.nan, count=5)
    hist.add_value(None, count=5)
    hist.add_value(1.0)

    assert _moments(hist) == (1, 1.0, 1.0)


def test_merge_adds_the_accumulators() -> None:
    left = _folded(1.0, 2.0)

    left.merge(_folded(3.0))

    assert _moments(left) == (3, 6.0, 14.0)


def test_merge_with_a_histogram_of_unknown_moments_is_unknown() -> None:
    # An old file's histogram has no accumulators, and merging one in
    # cannot invent them: absent means unknown, never empty -- on either
    # side, so an unknown left does not adopt the right's sums either.
    fresh = _folded(1.0, 2.0)
    fresh.merge(_predating(3.0))
    assert fresh.moments is None
    # The bars still merged; only the moments are unknown.
    assert fresh.bars.sum() == 3

    old = _predating(3.0)
    old.merge(_folded(1.0, 2.0))
    assert old.moments is None


def test_the_accumulators_are_stored_beside_min_and_max_and_round_trip(
) -> None:
    hist = _folded(1.0, 2.0, 4.0)

    stored = json.loads(hist.serialize())
    loaded = NumberHistogram.deserialize(hist.serialize())

    assert [stored[key] for key in MOMENT_KEYS] == [3, 7.0, 21.0]
    assert _moments(loaded) == (3, 7.0, 21.0)


def test_a_numpy_weight_still_serializes() -> None:
    # ``add_value`` takes whatever count a backend hands it; the bars
    # absorb a numpy int, and the stored accumulators must too.
    hist = NumberHistogram(_a_config())
    hist.add_value(2.0, np.int64(3))

    stored = json.loads(hist.serialize())

    assert [stored[key] for key in MOMENT_KEYS] == [3, 6.0, 12.0]


def test_a_file_without_the_accumulators_loads_with_unknown_moments(
) -> None:
    loaded = _predating(1.0, 2.0)

    assert loaded.moments is None
    assert (loaded.count, loaded.mean, loaded.std) == (None, None, None)


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
    assert (hist.mean, hist.std) == (None, None)


def test_moments_summary_renders_labelled_n_mean_sd_for_the_page() -> None:
    hist = NumberHistogram(_a_config(lo=0, hi=100))
    hist.add_batch(np.array([1.0, 4.0]), np.array([1_000_000, 234_567]))

    # One (label, value) pair per line of the page's Summary cell: n with
    # thousands separators, mean and sd at the three significant digits
    # ``values_domain`` uses.
    assert hist.moments_summary() == (
        ("n", "1,234,567"), ("mean", "1.57"), ("sd", "1.18"))


def test_moments_summary_is_none_when_the_moments_are_unknown() -> None:
    assert NumberHistogram(_a_config()).moments_summary() is None
    assert _predating(3.0).moments_summary() is None


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
