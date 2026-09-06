"""The factory that builds a score implementation from a resource.

The same dispatch the ``gain.genomic_resources.implementations`` entry
points make by type name, for a caller holding a resource rather than a
type -- the sibling of
:func:`~gain.genomic_resources.genomic_scores.builders.build_score_from_resource`,
which picks the score class the same way.
"""
from __future__ import annotations

from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.resource_types import (
    FRAGMENT_SCORE_TYPES,
    PREFERRED_ALLELE_SCORE_TYPE,
)

from .allele import AlleleScoreImplementation
from .base import GenomicScoreImplementation
from .fragment import FragmentScoreImplementation
from .position import PositionScoreImplementation


def build_score_implementation_from_resource(
    resource: GenomicResource,
) -> GenomicScoreImplementation:
    """Builds score implementation based on resource type.

    Each kind gets the class that renders its page; the base is never
    what a real resource gets, since it names no kind's template.  The
    ladder is the one ``build_score_from_resource`` climbs, and a type
    that is no kind is refused here as it is there, rather than handed
    the position class to fail one step later.
    """
    resource_type = resource.get_type()
    if resource_type == "position_score":
        return PositionScoreImplementation(resource)
    if resource_type == PREFERRED_ALLELE_SCORE_TYPE:
        return AlleleScoreImplementation(resource)
    if resource_type in FRAGMENT_SCORE_TYPES:
        return FragmentScoreImplementation(resource)
    raise ValueError(
        f"Resource {resource.get_id()} is not of score type; "
        f"unexpected resource type {resource_type}")
