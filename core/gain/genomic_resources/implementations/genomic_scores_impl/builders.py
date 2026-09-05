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


def build_score_implementation_from_resource(
    resource: GenomicResource,
) -> GenomicScoreImplementation:
    """Builds score implementation based on resource type.

    A kind whose page carries an extra section gets the subclass that
    renders it.
    """
    resource_type = resource.get_type()
    if resource_type in FRAGMENT_SCORE_TYPES:
        return FragmentScoreImplementation(resource)
    if resource_type == PREFERRED_ALLELE_SCORE_TYPE:
        return AlleleScoreImplementation(resource)
    return GenomicScoreImplementation(resource)
