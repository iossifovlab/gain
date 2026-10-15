# pylint: disable=C0114,C0116
# C0114, C0116: the repo tests carry no docstrings.
from collections.abc import Callable
from typing import Any

import numpy as np
import pytest
from gain.genomic_resources.histogram import (
    NumberHistogram,
    NumberHistogramConfig,
)

# The histogram calls ``np.logspace`` through the ``np`` module, so patching
# that public name perturbs the raw edges the way a host whose ``power``
# rounds differently would.


def _config(
    nbins: int, x_min_log: float, view_max: float,
) -> NumberHistogramConfig:
    return NumberHistogramConfig.from_dict({
        "type": "number",
        "view_range": {"min": 0, "max": view_max},
        "number_of_bins": nbins,
        "x_log_scale": True,
        "y_log_scale": False,
        "x_min_log": x_min_log,
    })


def _off_by_one_ulp(shift: int) -> Callable[..., Any]:
    """Build a ``logspace`` whose results are 1 ulp off in several positions."""
    real_logspace = np.logspace  # captured before the patch replaces it

    def logspace(*args: Any, **kwargs: Any) -> Any:
        result = np.array(real_logspace(*args, **kwargs), dtype=np.float64)
        flat = result.reshape(-1)
        for i in range(shift % 3, flat.size, 3):
            direction = np.inf if (i + shift) % 2 else -np.inf
            flat[i] = np.nextafter(flat[i], direction)
        return result
    return logspace


def _edges(
    monkeypatch: pytest.MonkeyPatch,
    config: NumberHistogramConfig,
    shift: int | None,
) -> tuple[list[float], str]:
    with monkeypatch.context() as patch:
        if shift is not None:
            patch.setattr(np, "logspace", _off_by_one_ulp(shift))
        hist = NumberHistogram(config)
    return hist.bins.tolist(), hist.serialize()


def test_perturbation_really_changes_raw_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as patch:
        patch.setattr(np, "logspace", _off_by_one_ulp(0))
        perturbed = np.logspace(-5, 0, 126)
    assert not np.array_equal(perturbed, np.logspace(-5, 0, 126))


@pytest.mark.parametrize("shift", [0, 1, 2, 3])
def test_log_edges_ignore_one_ulp_power_errors(
    monkeypatch: pytest.MonkeyPatch, shift: int,
) -> None:
    config = _config(126, 1e-05, 1)

    expected = _edges(monkeypatch, config, None)
    actual = _edges(monkeypatch, config, shift)

    assert actual == expected


@pytest.mark.parametrize("nbins,x_min_log,view_max", [
    (126, 1e-05, 1),
    (126, 0.001, 100),
])
def test_published_fixed_log_configs_are_stable(
    monkeypatch: pytest.MonkeyPatch,
    nbins: int, x_min_log: float, view_max: float,
) -> None:
    config = _config(nbins, x_min_log, view_max)
    expected = _edges(monkeypatch, config, None)

    for shift in range(4):
        assert _edges(monkeypatch, config, shift) == expected


def test_published_data_dependent_log_configs_are_stable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rng = np.random.default_rng(1953)
    view_maxima = 10 ** rng.uniform(np.log10(1.8e4), np.log10(2.6e5), 2000)

    for view_max in view_maxima.tolist():
        config = _config(100, 1e-05, view_max)
        expected = _edges(monkeypatch, config, None)
        for shift in range(2):
            assert _edges(monkeypatch, config, shift) == expected, view_max
