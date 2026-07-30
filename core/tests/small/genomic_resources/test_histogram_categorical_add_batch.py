# pylint: disable=C0114,C0116,W0212
import numpy as np
import pytest
from gain.genomic_resources.histogram import (
    CategoricalHistogram,
    CategoricalHistogramConfig,
    HistogramError,
)


def _config(*, enforce_type: bool = False) -> CategoricalHistogramConfig:
    return CategoricalHistogramConfig(enforce_type=enforce_type)


def _reference(
    config: CategoricalHistogramConfig,
    values: np.ndarray, weights: np.ndarray,
) -> CategoricalHistogram:
    hist = CategoricalHistogram(config)
    for value, weight in zip(values, weights, strict=True):
        hist.add_value(value, int(weight))
    return hist


def _assert_same(
    batched: CategoricalHistogram, ref: CategoricalHistogram,
) -> None:
    assert batched.raw_values == ref.raw_values
    # Insertion order decides which values a display truncation keeps, so
    # equal counts are not enough -- the order has to match too.
    assert list(batched.raw_values) == list(ref.raw_values)


def test_add_batch_matches_add_value_loop_unweighted() -> None:
    config = _config()
    values = np.array(["a", "b", "a", None, "c", "b", "a"], dtype=object)
    weights = np.ones(values.size, dtype=np.int64)

    ref = _reference(config, values, weights)
    batched = CategoricalHistogram(config)
    batched.add_batch(values, weights)

    _assert_same(batched, ref)
    assert batched.raw_values == {"a": 3, "b": 2, "c": 1}


def test_add_batch_matches_add_value_loop_weighted() -> None:
    """A position score weighs each record by its span, so weights vary."""
    config = _config()
    values = np.array(["a", "b", "a", None, "c"], dtype=object)
    weights = np.array([3, 1, 4, 9, 2], dtype=np.int64)

    ref = _reference(config, values, weights)
    batched = CategoricalHistogram(config)
    batched.add_batch(values, weights)

    _assert_same(batched, ref)
    # The None's weight is dropped whole, not folded into anything.
    assert batched.raw_values == {"a": 7, "b": 1, "c": 2}


def test_add_batch_accumulates_across_batches() -> None:
    config = _config()
    hist = CategoricalHistogram(config)
    hist.add_batch(
        np.array(["a", "b"], dtype=object), np.array([1, 2], dtype=np.int64))
    hist.add_batch(
        np.array(["b", "c"], dtype=object), np.array([3, 4], dtype=np.int64))

    assert hist.raw_values == {"a": 1, "b": 5, "c": 4}


def test_add_batch_empty_and_all_none_are_noops() -> None:
    config = _config()
    for values, weights in [
        (np.array([], dtype=object), np.array([], dtype=np.int64)),
        (np.array([None, None], dtype=object),
         np.array([3, 4], dtype=np.int64)),
    ]:
        hist = CategoricalHistogram(config)
        hist.add_batch(values, weights)
        assert hist.raw_values == {}


def test_add_batch_matches_add_value_loop_fuzz() -> None:
    """Randomised equivalence over batch widths, weights and value mixes.

    Width is varied because the unweighted case takes a different route --
    a whole-list count -- from the weighted one, and a batch of every width
    has to come out of both the same way it comes out of the scalar loop.
    """
    rng = np.random.default_rng(20260730)
    alphabet = [*[f"v{i}" for i in range(12)], 1, 2, 3, None]
    for _ in range(300):
        size = int(rng.integers(0, 120))
        values = np.array(
            [alphabet[int(i)] for i in rng.integers(0, len(alphabet), size)],
            dtype=object)
        weights = np.ones(size, dtype=np.int64) if rng.random() < 0.5 \
            else rng.integers(1, 10_000, size=size)

        config = _config()
        ref = _reference(config, values, weights)
        batched = CategoricalHistogram(config)
        batched.add_batch(values, weights)

        _assert_same(batched, ref)


def test_add_batch_refuses_a_value_add_value_refuses(
) -> None:
    """The same ``TypeError``, with the same message, on the same value."""
    config = _config()
    values = np.array(["a", 0.5, "b"], dtype=object)
    weights = np.ones(3, dtype=np.int64)

    with pytest.raises(TypeError) as scalar:
        _reference(config, values, weights)
    with pytest.raises(TypeError) as batch:
        CategoricalHistogram(config).add_batch(values, weights)

    assert str(batch.value) == str(scalar.value)
    assert "bad <0.5>" in str(batch.value)


def test_a_type_refusal_leaves_nothing_behind() -> None:
    """A batch refused for its type does not half-count.

    The types are checked before anything is accumulated, so a batch whose
    third value is unusable does not leave the first two counted.
    """
    hist = CategoricalHistogram(_config())
    with pytest.raises(TypeError):
        hist.add_batch(
            np.array(["a", "b", 0.5], dtype=object),
            np.ones(3, dtype=np.int64))

    assert hist.raw_values == {}


def test_a_limit_refusal_accumulates_first_and_says_so() -> None:
    """The limit is tested after the batch lands, unlike the type check.

    The scalar loop stops at the value that crosses the limit, so it holds
    exactly one too many; a batch is counted whole and then tested, so it
    holds the whole batch.  Not observable through the scan -- both paths
    replace the histogram with a ``NullHistogram`` -- but ``add_batch`` is
    public and the docstring states this rather than claiming the symmetry.
    """
    values = _too_many_values(40)
    weights = np.ones(values.size, dtype=np.int64)

    batched = CategoricalHistogram(_config())
    with pytest.raises(HistogramError):
        batched.add_batch(values, weights)

    scalar = CategoricalHistogram(_config())
    with pytest.raises(HistogramError):
        for value in values:
            scalar.add_value(value)

    assert len(batched.raw_values) == values.size
    assert len(scalar.raw_values) == \
        CategoricalHistogram.UNIQUE_VALUES_LIMIT + 1


def _too_many_values(extra: int) -> np.ndarray:
    limit = CategoricalHistogram.UNIQUE_VALUES_LIMIT
    return np.array(
        [f"v{i}" for i in range(limit + extra)], dtype=object)


@pytest.mark.parametrize("extra", [1, 2, 40])
def test_add_batch_reports_the_limit_as_add_value_reports_it(
    extra: int,
) -> None:
    """Past ``UNIQUE_VALUES_LIMIT``, the same message whatever the overshoot.

    ``add_value`` tests after every single add, so it always raises holding
    exactly one value too many; a batch that overshoots by forty has to say
    the same thing, because that message is what a ``NullHistogram`` carries
    into the saved statistics.
    """
    config = _config()
    values = _too_many_values(extra)
    weights = np.ones(values.size, dtype=np.int64)

    with pytest.raises(HistogramError) as scalar:
        _reference(config, values, weights)
    with pytest.raises(HistogramError) as batch:
        CategoricalHistogram(config).add_batch(values, weights)

    assert str(batch.value) == str(scalar.value)
    assert str(CategoricalHistogram.UNIQUE_VALUES_LIMIT + 1) \
        in str(batch.value)


def test_add_batch_crossing_the_limit_over_several_batches() -> None:
    """The limit is on the histogram, not on one batch."""
    hist = CategoricalHistogram(_config())
    limit = CategoricalHistogram.UNIQUE_VALUES_LIMIT
    half = np.array([f"v{i}" for i in range(limit)], dtype=object)
    hist.add_batch(half, np.ones(half.size, dtype=np.int64))

    with pytest.raises(HistogramError):
        hist.add_batch(
            np.array(["one-too-many"], dtype=object),
            np.ones(1, dtype=np.int64))


def test_add_batch_does_not_enforce_the_limit_when_the_type_is_enforced(
) -> None:
    """An explicitly configured categorical histogram has no value ceiling.

    ``enforce_type`` is what ``from_dict`` sets and ``default_config`` clears,
    and the scalar path reads it the same way -- a configured histogram is
    taken at its word.
    """
    config = _config(enforce_type=True)
    values = _too_many_values(5)
    weights = np.ones(values.size, dtype=np.int64)

    hist = CategoricalHistogram(config)
    hist.add_batch(values, weights)

    assert len(hist.raw_values) == values.size
    assert hist.raw_values == _reference(config, values, weights).raw_values


def test_add_batch_is_not_a_per_value_add_value_loop() -> None:
    """The batch must not fall back to looping ``add_value``.

    Equivalence alone cannot catch that: a fallback is by construction
    bit-identical to the scalar path and would pass every other test here,
    while costing the statistics scan exactly what batching buys it.
    """
    hist = CategoricalHistogram(_config())
    calls = 0
    original = CategoricalHistogram.add_value

    def counting_add_value(
        self: CategoricalHistogram, value: str | int | None, count: int = 1,
    ) -> None:
        nonlocal calls
        calls += 1
        original(self, value, count)

    CategoricalHistogram.add_value = (  # type: ignore[method-assign]
        counting_add_value)
    try:
        hist.add_batch(
            np.array(["a", "b", "a"], dtype=object),
            np.array([1, 2, 3], dtype=np.int64))
    finally:
        CategoricalHistogram.add_value = original  # type: ignore[method-assign]

    assert calls == 0, f"add_batch fell back to {calls} add_value calls"
