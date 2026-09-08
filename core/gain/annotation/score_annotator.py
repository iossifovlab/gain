"""Deprecated re-exports of the score annotators (gain#1152).

Import each from its own module instead:
:mod:`gain.annotation.genomic_score_annotator_base`,
:mod:`gain.annotation.position_score_annotator` and
:mod:`gain.annotation.allele_score_annotator`.  Kept for gpf
(iossifovlab/gpf#1012) until gain#1154 removes it.
"""
import warnings

from gain.annotation.allele_score_annotator import AlleleScoreAnnotator
from gain.annotation.genomic_score_annotator_base import (
    GenomicScoreAnnotatorBase,
)
from gain.annotation.position_score_annotator import PositionScoreAnnotator

warnings.warn(
    "gain.annotation.score_annotator is deprecated; import from "
    "gain.annotation.genomic_score_annotator_base, "
    "gain.annotation.position_score_annotator or "
    "gain.annotation.allele_score_annotator instead (gain#1152).",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "AlleleScoreAnnotator",
    "GenomicScoreAnnotatorBase",
    "PositionScoreAnnotator",
]
