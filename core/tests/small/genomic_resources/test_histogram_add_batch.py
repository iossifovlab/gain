# pylint: disable=C0114,C0116,W0212
import numpy as np
from gain.genomic_resources.histogram import (
    NumberHistogram,
    NumberHistogramConfig,
)


def _lin_config() -> NumberHistogramConfig:
    return NumberHistogramConfig.from_dict({
        "type": "number",
        "view_range": {"min": 0, "max": 10},
        "number_of_bins": 10,
        "x_log_scale": False,
        "y_log_scale": False,
    })


def _reference(
    config: NumberHistogramConfig,
    values: np.ndarray, weights: np.ndarray,
) -> NumberHistogram:
    hist = NumberHistogram(config)
    for value, weight in zip(values, weights, strict=True):
        hist.add_value(float(value), int(weight))
    return hist


def _assert_same(batched: NumberHistogram, ref: NumberHistogram) -> None:
    assert np.array_equal(batched.bars, ref.bars), \
        (batched.bars, ref.bars)
    assert batched.out_of_range_bins == ref.out_of_range_bins
    assert np.array_equal(
        [batched.min_value], [ref.min_value], equal_nan=True)
    assert np.array_equal(
        [batched.max_value], [ref.max_value], equal_nan=True)


def test_add_batch_matches_add_value_loop_linear() -> None:
    config = _lin_config()
    # in-range, right-edge (bin clamp), below-range, above-range, nan-skip.
    values = np.array([0.0, 1.5, 9.9, 10.0, -1.0, 12.0, 5.0, np.nan])
    weights = np.array([1, 2, 3, 1, 4, 2, 5, 7])

    ref = _reference(config, values, weights)
    batched = NumberHistogram(config)
    batched.add_batch(values, weights)

    _assert_same(batched, ref)


def _log_config() -> NumberHistogramConfig:
    return NumberHistogramConfig.from_dict({
        "type": "number",
        "view_range": {"min": 0, "max": 1000},
        "number_of_bins": 5,
        "x_log_scale": True,
        "y_log_scale": False,
        "x_min_log": 0.1,
    })


def test_add_batch_matches_add_value_loop_log() -> None:
    config = _log_config()
    values = np.array(
        [0.0, 0.05, 0.1, 0.5, 1.0, 10.0, 100.0, 1000.0, 1001.0, -1.0, np.nan])
    weights = np.array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11])

    ref = _reference(config, values, weights)
    batched = NumberHistogram(config)
    batched.add_batch(values, weights)

    _assert_same(batched, ref)


def test_add_batch_matches_add_value_loop_fuzz() -> None:
    rng = np.random.default_rng(20260723)
    for _ in range(300):
        lo = int(rng.integers(-50, 50))
        hi = lo + int(rng.integers(1, 100))
        nbins = int(rng.integers(1, 40))
        config = NumberHistogramConfig.from_dict({
            "type": "number",
            "view_range": {"min": lo, "max": hi},
            "number_of_bins": nbins,
            "x_log_scale": False,
            "y_log_scale": False,
        })
        n = int(rng.integers(0, 300))
        values = rng.uniform(lo - 5, hi + 5, size=n)
        if n:
            values[rng.random(n) < 0.1] = np.nan
        weights = rng.integers(1, 10_000, size=n)

        ref = _reference(config, values, weights)
        batched = NumberHistogram(config)
        batched.add_batch(values, weights)
        _assert_same(batched, ref)


def test_add_batch_all_nan_and_empty_are_noops() -> None:
    config = _lin_config()
    for values, weights in [
        (np.array([]), np.array([])),
        (np.array([np.nan, np.nan]), np.array([3, 4])),
    ]:
        hist = NumberHistogram(config)
        hist.add_batch(values, weights)
        assert (hist.bars == 0).all()
        assert hist.out_of_range_bins == [0, 0]
        assert np.isnan(hist.min_value)
        assert np.isnan(hist.max_value)


def test_add_batch_weighted_counts_use_int64() -> None:
    config = _lin_config()
    # One bin accumulates 5e9 -- past int32, so int32 bars would wrap.
    values = np.full(1000, 5.0)
    weights = np.full(1000, 5_000_000, dtype=np.int64)

    ref = _reference(config, values, weights)
    batched = NumberHistogram(config)
    batched.add_batch(values, weights)

    _assert_same(batched, ref)
    assert batched.bars[5] == 5_000_000_000
