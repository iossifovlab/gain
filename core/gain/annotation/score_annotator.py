"""Deprecated: the score annotators live one per module (gain#1152).

This module used to define the shared base and both genomic score
annotators.  They are now

* :mod:`gain.annotation.genomic_score_annotator_base`
* :mod:`gain.annotation.position_score_annotator`
* :mod:`gain.annotation.allele_score_annotator`

and this facade only re-exports them, so that a consumer built against
the old path keeps importing while it moves.  It imports *from* the new
modules and nothing imports it back, which is what keeps the split free
of a circular import.  It is scheduled for removal by gain#1154.
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
