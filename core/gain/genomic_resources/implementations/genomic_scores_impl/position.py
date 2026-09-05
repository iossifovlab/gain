""":class:`PositionScoreImplementation` -- the position score's page.

The genomic-score page plus a Coverage section: the kind whose rows are
pairwise disjoint, so the union of their spans measures what the resource
covers (gain#1118, gain#1127).  The resource protocol every kind answers
alike is on :class:`~.base.GenomicScoreImplementation`.
"""
from __future__ import annotations

from typing import ClassVar

from .base import GenomicScoreImplementation


class PositionScoreImplementation(GenomicScoreImplementation):
    """Assists in the management of a position score resource.

    It carries its own info page, which is the genomic-score page plus a
    Coverage section, the way the other two kinds each carry theirs.
    """

    template_name: ClassVar[str] = "position_score.jinja"
