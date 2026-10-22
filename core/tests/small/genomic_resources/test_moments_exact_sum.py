# pylint: disable=C0114,C0116
"""``Moments`` stores the correctly rounded sums, whatever the order of
the additions (gain#2047): one batch, many batches, shuffled batches, the
per-value path and merges all store the same ``sum`` and
``sum_of_squares``, to the bit."""
import json
import math

import numpy as np
import pytest
from gain.genomic_resources.histogram import (
    NumberHistogram,
    NumberHistogramConfig,
)
from gain.genomic_resources.statistics.moments import Moments

_RNG_SEED = 2047


def _values_and_weights() -> tuple[np.ndarray, np.ndarray]:
    """Values with all 53 bits significant, over a wide range of
    magnitudes, so a plain double sum depends on the order."""
    rng = np.random.default_rng(_RNG_SEED)
    values = rng.standard_normal(3000) * 10.0 ** rng.integers(-3, 9, 3000)
    weights = rng.integers(1, 50, 3000)
    return values, weights


def _sums(moments: Moments) -> tuple[int, float, float]:
    return moments.count, moments.sum, moments.sum_of_squares


def _one_batch(values: np.ndarray, weights: np.ndarray) -> Moments:
    moments = Moments()
    moments.add_batch(values, weights)
    return moments


def _batches(
    values: np.ndarray, weights: np.ndarray, size: int,
) -> list[Moments]:
    return [
        _one_batch(values[start:start + size], weights[start:start + size])
        for start in range(0, len(values), size)
    ]


def test_the_plain_double_sums_of_the_fixture_depend_on_the_order() -> None:
    # Guards the tests below: on values whose plain sums agree whatever
    # the order, an order-independent result would prove nothing.
    values, weights = _values_and_weights()
    products = values * weights
    squares = values * values * weights
    order = np.random.default_rng(_RNG_SEED).permutation(len(values))

    assert float(products.sum()) != float(products[order].sum())
    assert float(squares.sum()) != float(squares[order].sum())


def test_several_batches_store_the_sums_of_one_batch() -> None:
    values, weights = _values_and_weights()
    whole = _one_batch(values, weights)

    split = Moments()
    for size in (7, 100, 1000):
        split = Moments()
        for start in range(0, len(values), size):
            split.add_batch(
                values[start:start + size], weights[start:start + size])
        assert _sums(split) == _sums(whole), size


def test_shuffled_batches_store_the_sums_of_one_batch() -> None:
    values, weights = _values_and_weights()
    order = np.random.default_rng(_RNG_SEED + 1).permutation(len(values))
    whole = _one_batch(values, weights)

    shuffled = Moments()
    for part in _batches(values[order], weights[order], 250):
        shuffled.merge(part)

    assert _sums(shuffled) == _sums(whole)


def test_the_add_path_stores_the_sums_of_one_batch() -> None:
    values, weights = _values_and_weights()
    whole = _one_batch(values, weights)

    one_by_one = Moments()
    for value, weight in zip(values.tolist(), weights.tolist(), strict=True):
        one_by_one.add(value, weight)

    assert _sums(one_by_one) == _sums(whole)


def test_merges_in_any_order_store_the_sums_of_one_batch() -> None:
    values, weights = _values_and_weights()
    whole = _one_batch(values, weights)
    parts = _batches(values, weights, 300)

    forward = Moments()
    for part in parts:
        forward.merge(part)
    backward = Moments()
    for part in reversed(parts):
        backward.merge(part)
    # A tree of merges, as the region split of a statistics build gives.
    left, right = Moments(), Moments()
    for part in parts[: len(parts) // 2]:
        left.merge(part)
    for part in parts[len(parts) // 2:]:
        right.merge(part)
    right.merge(left)

    assert _sums(forward) == _sums(whole)
    assert _sums(backward) == _sums(whole)
    assert _sums(right) == _sums(whole)


def test_the_sums_are_the_correctly_rounded_exact_sums() -> None:
    values, weights = _values_and_weights()

    moments = _one_batch(values, weights)

    assert moments.sum == math.fsum((values * weights).tolist())
    assert moments.sum_of_squares == math.fsum(
        (values * values * weights).tolist())


@pytest.mark.parametrize("values", [
    pytest.param([5e-324, 3 * 5e-324, -1e-310, 2.5e-308], id="subnormal"),
    pytest.param([1e200, 1.0, -1e200, 1e-200], id="cancellation"),
    pytest.param(
        [1e300, 1e300], id="overflowing-square",
        marks=pytest.mark.filterwarnings("ignore:overflow encountered")),
])
def test_extreme_values_fold_the_same_through_both_arms(
    values: list[float],
) -> None:
    weights = [1] * len(values)
    batch = _one_batch(np.array(values), np.array(weights))

    one_by_one = Moments()
    for value in reversed(values):
        one_by_one.add(value, 1)

    assert _sums(one_by_one) == _sums(batch)
    assert batch.sum == math.fsum(values)


def test_infinities_propagate_as_in_a_plain_sum() -> None:
    moments = _one_batch(np.array([1.0, math.inf]), np.array([1, 1]))
    assert moments.sum == math.inf
    assert moments.sum_of_squares == math.inf

    moments.add(-math.inf, 1)
    assert math.isnan(moments.sum)


def test_number_histogram_stores_the_same_sums_through_both_arms() -> None:
    values, weights = _values_and_weights()
    config = NumberHistogramConfig((-1e9, 1e9), number_of_bins=10)
    batch = NumberHistogram(config)
    for start in range(0, len(values), 128):
        batch.add_batch(values[start:start + 128], weights[start:start + 128])
    one_by_one = NumberHistogram(config)
    for value, weight in zip(values.tolist(), weights.tolist(), strict=True):
        one_by_one.add_value(value, count=weight)

    stored_batch = json.loads(batch.serialize())
    stored_one_by_one = json.loads(one_by_one.serialize())

    for key in ("count", "sum", "sum_of_squares"):
        assert stored_batch[key] == stored_one_by_one[key], key
    assert isinstance(stored_batch["sum"], float)
    assert isinstance(stored_batch["sum_of_squares"], float)


def test_a_loaded_histogram_merges_from_the_stored_sums() -> None:
    values, weights = _values_and_weights()
    config = NumberHistogramConfig((-1e9, 1e9), number_of_bins=10)
    first = NumberHistogram(config)
    first.add_batch(values[:1500], weights[:1500])
    second = NumberHistogram(config)
    second.add_batch(values[1500:], weights[1500:])
    loaded = NumberHistogram.deserialize(first.serialize())

    loaded.merge(second)

    assert loaded.moments is not None
    assert first.moments is not None
    assert second.moments is not None
    expected = Moments(
        count=first.moments.count,
        sum=first.moments.sum,
        sum_of_squares=first.moments.sum_of_squares,
    )
    expected.merge(second.moments)
    assert _sums(loaded.moments) == _sums(expected)
