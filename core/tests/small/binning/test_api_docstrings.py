"""Pin the numpy-style sections of the documented binning functions."""
from __future__ import annotations

import inspect
from collections.abc import Callable
from itertools import pairwise
from typing import Any

import gain.binning as binning
import pytest


@pytest.mark.parametrize("func", [
    binning.read_tracks,
    binning.read_bins,
    binning.read_values,
    binning.track_labels,
    binning.resolve_binners,
])
@pytest.mark.parametrize("section", ["Parameters", "Returns", "Raises"])
def test_docstring_has_section(
    func: Callable[..., Any], section: str,
) -> None:
    doc = inspect.getdoc(func) or ""
    lines = doc.splitlines()

    assert any(
        line == section and nxt == "-" * len(section)
        for line, nxt in pairwise(lines)
    ), f"{func.__name__} has no {section} section"
