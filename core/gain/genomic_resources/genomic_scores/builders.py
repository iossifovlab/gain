"""The factories that build a score from a resource.

One pair per kind (``build_<kind>_score_from_resource`` and its
``_from_resource_id`` sibling) plus the dispatching pair that reads the
resource's type and picks the kind for you. The ``_from_resource_id`` half
falls back to the default GRR when handed no repository.
"""

from __future__ import annotations

from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceRepo,
)
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)
from gain.genomic_resources.resource_types import (
    FRAGMENT_SCORE_TYPES,
    PREFERRED_ALLELE_SCORE_TYPE,
    reject_retired_resource,
)

from .allele import AlleleScore
from .base import GenomicScore
from .fragment import FragmentScore
from .position import PositionScore


def build_position_score_from_resource(
    resource: GenomicResource,
) -> PositionScore:
    """Build a position score from a `position_score` resource."""
    return PositionScore(resource)


def build_position_score_from_resource_id(
    resource_id: str, grr: GenomicResourceRepo | None = None,
) -> PositionScore:
    """Build a position score from a `position_score` resource id."""
    if grr is None:
        grr = build_genomic_resource_repository()
    return build_position_score_from_resource(grr.get_resource(resource_id))


def build_allele_score_from_resource(
    resource: GenomicResource,
) -> AlleleScore:
    """Build an allele score from an `allele_score` resource.

    Defaults to alleles mode unless the resource configures
    `allele_score_mode` explicitly.

    The deprecated `np_score` type was accepted here until 2026.8.5, and
    defaulted to substitutions mode instead (gain#920).  A resource still
    declaring it is refused with a message naming both the replacement type
    and the mode key needed to keep the old reading.
    """
    return AlleleScore(resource)


def build_allele_score_from_resource_id(
    resource_id: str, grr: GenomicResourceRepo | None = None,
) -> AlleleScore:
    """Build an allele score from an `allele_score` resource id."""
    if grr is None:
        grr = build_genomic_resource_repository()
    return build_allele_score_from_resource(grr.get_resource(resource_id))


def build_fragment_score_from_resource(
    resource: GenomicResource,
) -> FragmentScore:
    """Build a fragment score from a fragment-score resource.

    A fresh score every call, as the position and allele factories give:
    the caller owns what it gets back and may close it without affecting
    anyone else.
    """
    return FragmentScore(resource)


def build_fragment_score_from_resource_id(
    resource_id: str, grr: GenomicResourceRepo | None = None,
) -> FragmentScore:
    """Build a fragment score from a `cnv_collection` resource id."""
    if grr is None:
        grr = build_genomic_resource_repository()
    return build_fragment_score_from_resource(grr.get_resource(resource_id))


def build_score_from_resource(
    resource: GenomicResource,
) -> GenomicScore:
    """Build a genomic score resource and return the coresponding score.

    Dispatches on the resource type to the corresponding typed factory. Use
    the typed factories directly when the resource type is known statically;
    this one exists for callers handed a resource of unknown type.

    Every kind yields a fresh instance per call, so the caller owns the
    score it gets back and closing it affects nothing else.
    """
    resource_type = resource.get_type()
    reject_retired_resource(resource)
    if resource_type == "position_score":
        return build_position_score_from_resource(resource)
    if resource_type == PREFERRED_ALLELE_SCORE_TYPE:
        return build_allele_score_from_resource(resource)
    if resource_type in FRAGMENT_SCORE_TYPES:
        return build_fragment_score_from_resource(resource)

    raise ValueError(
        f"Resource {resource.get_id()} is not of score type; "
        f"unexpected resource type {resource_type}")


def build_score_from_resource_id(
    resource_id: str, grr: GenomicResourceRepo | None = None,
) -> GenomicScore:
    if grr is None:
        grr = build_genomic_resource_repository()
    return build_score_from_resource(grr.get_resource(resource_id))
