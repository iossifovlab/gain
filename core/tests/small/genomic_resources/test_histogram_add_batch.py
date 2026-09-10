# pylint: disable=C0114,C0116,W0212
import numpy as np
from gain.genomic_resources.histogram import (
    NumberHistogram,
    NumberHistogramConfig,
)


def _a_config(lo: float, hi: float, nbins: int) -> NumberHistogramConfig:
    return NumberHistogramConfig.from_dict({
        "type": "number",
        "view_range": {"min": lo, "max": hi},
        "number_of_bins": nbins,
        "x_log_scale": False,
        "y_log_scale": False,
    })


def _lin_config() -> NumberHistogramConfig:
    return _a_config(0, 10, 10)


def _reference(
    config: NumberHistogramConfig,
    values: np.ndarray, weights: np.ndarray,
) -> NumberHistogram:
    """Fold each value AS STORED, without laundering its dtype.

    This deliberately does not coerce with ``float(value)``: that would hide
    the question the narrow-dtype tests exist to ask, since a real caller
    hands ``add_value`` the numpy scalar its column holds and that is only a
    Python ``float`` when the column is float64.
    """
    hist = NumberHistogram(config)
    for value, weight in zip(values, weights, strict=True):
        hist.add_value(value, int(weight))
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


#: How far either side of a bin edge the float32 grid is walked.
_ULPS_EACH_SIDE = 3


def _straddling_bin_edges(config: NumberHistogramConfig) -> np.ndarray:
    """The float32 grid immediately around every bin edge of a config.

    Derived rather than hand-picked, because this is the ONLY place the two
    arms can disagree at all.  A bin index is a truncation, so the arms
    differ exactly where float32 rounds a value to the far side of an edge
    that float64 keeps on the near side -- a few ULPs wide, and nowhere else.
    Uniform draws never land there: 200 random configs of them agree even
    when ``add_value`` bins the raw numpy scalar.

    The edges come from the histogram's own ``bins`` rather than a second
    spelling of ``linspace``, so this cannot drift from what it is probing.
    """
    edges = NumberHistogram(config).bins.astype(np.float32)
    walked = edges.copy()
    for _ in range(_ULPS_EACH_SIDE):
        walked = np.nextafter(walked, -np.inf, dtype=np.float32)

    grid = [walked]
    for _ in range(2 * _ULPS_EACH_SIDE):
        walked = np.nextafter(walked, np.inf, dtype=np.float32)
        grid.append(walked)

    values = np.concatenate(grid)
    lo, hi = config.view_range
    in_view = (values >= lo) & (values <= hi)
    return values[in_view]


def test_add_batch_matches_add_value_loop_float32_at_bin_edges() -> None:
    """A float32 column folds identically through both arms (gain#1338).

    ``(44, 106)`` over 27 bins is a config where the disagreement is real:
    numpy 2 keeps ``np.float32 - <python float>`` in float32, and
    60.074073791503906 -- one of the edge neighbours this generates -- bins
    to 7 that way against 6 in the float64 ``add_batch`` uses.  So this
    fails against the allow-list that refused float32 outright AND against a
    widening that admits the scalar but bins it without normalizing.
    """
    config = _a_config(44.0, 106.0, 27)
    values = _straddling_bin_edges(config)
    weights = np.arange(1, len(values) + 1)

    ref = _reference(config, values, weights)
    batched = NumberHistogram(config)
    batched.add_batch(values, weights)

    assert ref.bars.sum() > 0, "the fixture must fold something"
    _assert_same(batched, ref)


def test_add_batch_matches_add_value_loop_bool() -> None:
    """A boolean column folds identically through both arms (gain#1338)."""
    config = _lin_config()
    values = np.array([True, False, True, True])
    weights = np.array([1, 2, 3, 4])

    ref = _reference(config, values, weights)
    batched = NumberHistogram(config)
    batched.add_batch(values, weights)

    _assert_same(batched, ref)


def test_add_batch_matches_add_value_loop_float32_edge_fuzz() -> None:
    """The float32 agreement is not one lucky range.

    Every config's own bin edges, not uniform draws -- a fuzz that samples
    uniformly passes even when the two arms disagree, so it would pin
    nothing.
    """
    rng = np.random.default_rng(1338)
    for _ in range(50):
        lo = float(rng.integers(-50, 50))
        config = _a_config(
            lo, lo + float(rng.integers(1, 100)), int(rng.integers(1, 40)))
        values = _straddling_bin_edges(config)
        weights = rng.integers(1, 10_000, size=len(values))

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


def test_add_batch_log_scale_is_vectorized_not_a_per_value_loop() -> None:
    """The log path must not fall back to looping ``add_value``.

    Equivalence alone cannot catch a regression here: a fallback that calls
    ``add_value`` per element is by construction bit-identical to the scalar
    path, so it would pass every other test in this file while quietly costing
    the statistics scan the ~32% that vectorizing the accumulation buys.  This
    is the only test that would notice.
    """
    config = _log_config()
    hist = NumberHistogram(config)
    calls = 0
    original = NumberHistogram.add_value

    def counting_add_value(
        self: NumberHistogram, value: float | None, count: int = 1,
    ) -> None:
        nonlocal calls
        calls += 1
        original(self, value, count)

    NumberHistogram.add_value = counting_add_value  # type: ignore[method-assign]
    try:
        hist.add_batch(
            np.array([0.05, 0.5, 10.0, 1000.0]), np.array([1, 2, 3, 4]))
    finally:
        NumberHistogram.add_value = original  # type: ignore[method-assign]

    assert calls == 0, f"add_batch fell back to {calls} add_value calls"


def test_add_batch_matches_add_value_loop_log_fuzz() -> None:
    """Randomised log-scale equivalence, over many decades and batch sizes.

    Batch size is varied deliberately: numpy dispatches short arrays to a
    scalar loop and longer ones to a SIMD kernel, and a log histogram bins on
    ``np.log10`` -- so a vectorized implementation that agreed with the scalar
    path only for one of those widths would be a real, silent divergence.
    """
    rng = np.random.default_rng(20260724)
    for _ in range(300):
        x_min_log = float(10.0 ** rng.integers(-4, 0))
        view_max = float(10.0 ** rng.integers(1, 5))
        config = NumberHistogramConfig.from_dict({
            "type": "number",
            "view_range": {"min": 0, "max": view_max},
            "number_of_bins": int(rng.integers(2, 40)),
            "x_log_scale": True,
            "y_log_scale": False,
            "x_min_log": x_min_log,
        })
        n_bins = config.number_of_bins
        size = int(rng.integers(1, 60))
        # Spans below view_min, below x_min_log, the bulk of the decades, and
        # past view_max -- so every branch of choose_bin_log is exercised.
        random_values = 10.0 ** rng.uniform(-6, 6, size=size)
        random_values[rng.random(size) < 0.1] *= -1.0
        # Plus the exact bin boundaries.  Random draws essentially never land
        # on one, and an edge is where a log index is most fragile: it is the
        # only place a last-ulp difference in ``log10`` changes which bin a
        # value truncates into.  Without these the fuzz is insensitive to a
        # perturbation of ``log10(x_min_log)`` (verified by mutation).
        edges = np.logspace(np.log10(x_min_log), np.log10(view_max), n_bins)
        values = np.concatenate([random_values, edges])
        weights = rng.integers(1, 5, size=values.size)

        ref = _reference(config, values, weights)
        batched = NumberHistogram(config)
        batched.add_batch(values, weights)

        _assert_same(batched, ref)
