"""Tests for the deprecated ``gain.annotation.score_annotator`` facade.

The annotators it used to define live one per module since gain#1152:
``genomic_score_annotator_base``, ``position_score_annotator`` and
``allele_score_annotator``.  The facade stays only so that gpf keeps
building against gain's master wheel while it retargets its own imports
(iossifovlab/gpf#1012).  These tests keep the facade honest -- same
objects, a warning on import -- and go with it when gain#1154 deletes it.
"""
from __future__ import annotations

import subprocess
import sys

from gain.annotation import (
    allele_score_annotator,
    genomic_score_annotator_base,
    position_score_annotator,
    score_annotator,
)


def test_the_facade_reexports_the_base_class() -> None:
    assert score_annotator.GenomicScoreAnnotatorBase \
        is genomic_score_annotator_base.GenomicScoreAnnotatorBase


def test_the_facade_reexports_the_position_score_annotator() -> None:
    assert score_annotator.PositionScoreAnnotator \
        is position_score_annotator.PositionScoreAnnotator


def test_the_facade_reexports_the_allele_score_annotator() -> None:
    assert score_annotator.AlleleScoreAnnotator \
        is allele_score_annotator.AlleleScoreAnnotator


def test_importing_the_facade_emits_a_deprecation_warning() -> None:
    """The warning names the facade and where its classes went.

    Run in a fresh interpreter: the warning fires once per process, at
    import, and this process imported the facade at collection.
    """
    result = subprocess.run(
        [
            sys.executable, "-W", "always::DeprecationWarning",
            "-c", "import gain.annotation.score_annotator",
        ],
        capture_output=True, text=True, check=True,
    )

    assert "DeprecationWarning" in result.stderr
    assert "gain.annotation.score_annotator" in result.stderr
    assert "allele_score_annotator" in result.stderr
    assert "position_score_annotator" in result.stderr
