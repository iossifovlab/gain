"""Tests for the deprecated ``gain.annotation.score_annotator`` facade.

They keep the facade honest -- same objects as the per-annotator modules,
a warning on import -- and go with it when gain#1154 deletes it.
"""
from __future__ import annotations

import importlib
from types import ModuleType

import pytest
from gain.annotation import (
    allele_score_annotator,
    genomic_score_annotator_base,
    position_score_annotator,
    score_annotator,
)


@pytest.mark.parametrize(("name", "canonical"), [
    ("GenomicScoreAnnotatorBase", genomic_score_annotator_base),
    ("PositionScoreAnnotator", position_score_annotator),
    ("AlleleScoreAnnotator", allele_score_annotator),
])
def test_the_facade_reexports_the_class_its_module_defines(
    name: str, canonical: ModuleType,
) -> None:
    """The facade hands out the same object, not a copy or a subclass."""
    assert getattr(score_annotator, name) is getattr(canonical, name)


def test_importing_the_facade_emits_a_deprecation_warning() -> None:
    """The warning names the facade and the modules to import instead.

    Reloading re-runs the module body, and with it the warning that fired
    once already when this module imported the facade.
    """
    with pytest.warns(
        DeprecationWarning,
        match=r"gain\.annotation\.score_annotator is deprecated.*"
              r"position_score_annotator.*allele_score_annotator",
    ):
        importlib.reload(score_annotator)
