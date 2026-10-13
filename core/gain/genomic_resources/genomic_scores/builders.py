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
    """Build a position score from a resource.

    The score is closed. Call ``open()`` before the first read. Each call
    returns a new score, so the caller owns it and closing it affects no
    other score.

    Parameters
    ----------
    resource : GenomicResource
        A resource of type ``position_score``.

    Returns
    -------
    PositionScore
        A closed position score for the resource.

    Raises
    ------
    ValueError
        If the resource is not of type ``position_score``.
    """
    return PositionScore(resource)


def build_position_score_from_resource_id(
    resource_id: str, grr: GenomicResourceRepo | None = None,
) -> PositionScore:
    """Build a position score from the id of a resource.

    The score is closed. Call ``open()`` before the first read. Each call
    returns a new score, so the caller owns it and closing it affects no
    other score.

    Parameters
    ----------
    resource_id : str
        The id of a resource of type ``position_score`` in the repository.
    grr : GenomicResourceRepo | None
        The repository that resolves ``resource_id``. When it is ``None``,
        the function builds the default repository with
        ``build_genomic_resource_repository()``.

    Returns
    -------
    PositionScore
        A closed position score for the resource.

    Raises
    ------
    FileNotFoundError
        If the repository has no resource with this id.
    ValueError
        If the resource is not of type ``position_score``.
    """
    if grr is None:
        grr = build_genomic_resource_repository()
    return build_position_score_from_resource(grr.get_resource(resource_id))


def build_allele_score_from_resource(
    resource: GenomicResource,
) -> AlleleScore:
    """Build an allele score from an ``allele_score`` resource.

    Every call builds a new score. The score is closed: call ``open()``
    before the first read.

    Parameters
    ----------
    resource : GenomicResource
        A resource of type ``allele_score``.

    Returns
    -------
    AlleleScore
        A new, closed score. The mode is ``alleles`` unless the resource
        sets ``allele_score_mode``.

    Raises
    ------
    ValueError
        If the resource is not of type ``allele_score``. A resource that
        still declares the removed type ``np_score`` is also refused. The
        message names the replacement type and the mode key that keeps the
        old reading.
    """
    # The deprecated `np_score` type was accepted here until 2026.8.5, and
    # it defaulted to substitutions mode instead (gain#920).  AlleleScore
    # refuses it with a message that names the replacement.
    return AlleleScore(resource)


def build_allele_score_from_resource_id(
    resource_id: str, grr: GenomicResourceRepo | None = None,
) -> AlleleScore:
    """Build an allele score from the id of an ``allele_score`` resource.

    Every call builds a new score. The score is closed: call ``open()``
    before the first read.

    Parameters
    ----------
    resource_id : str
        The id of a resource of type ``allele_score`` in the repository.
    grr : GenomicResourceRepo or None, optional
        The repository that resolves ``resource_id``. ``None``, the
        default, builds the default repository with
        ``build_genomic_resource_repository()``.

    Returns
    -------
    AlleleScore
        A new, closed score, as ``build_allele_score_from_resource``
        builds it.

    Raises
    ------
    FileNotFoundError
        If the repository has no resource with this id.
    ValueError
        If the resource is not of type ``allele_score``.
    """
    if grr is None:
        grr = build_genomic_resource_repository()
    return build_allele_score_from_resource(grr.get_resource(resource_id))


def build_fragment_score_from_resource(
    resource: GenomicResource,
) -> FragmentScore:
    """Build a fragment score from a fragment-score resource.

    Every call builds a new score. You own the score and can close it
    without any effect on other scores built from the same resource. The
    score is closed: call ``open()`` before the first read.

    Parameters
    ----------
    resource : GenomicResource
        A resource of type ``fragment_score``, or of the older type
        ``cnv_collection``, which gives a deprecation warning.

    Returns
    -------
    FragmentScore
        A new, closed fragment score.

    Raises
    ------
    ValueError
        If the type of the resource is not a fragment score type.
    """
    return FragmentScore(resource)


def build_fragment_score_from_resource_id(
    resource_id: str, grr: GenomicResourceRepo | None = None,
) -> FragmentScore:
    """Build a fragment score from the id of a fragment-score resource.

    Parameters
    ----------
    resource_id : str
        The id of a resource of type ``fragment_score``, or of the older
        type ``cnv_collection``, which gives a deprecation warning.
    grr : GenomicResourceRepo | None, default None
        The repository that holds the resource. ``None`` builds the
        default repository.

    Returns
    -------
    FragmentScore
        A new, closed fragment score. Call ``open()`` before the first
        read.

    Raises
    ------
    FileNotFoundError
        If the repository has no resource with this id.
    ValueError
        If the type of the resource is not a fragment score type.
    """
    if grr is None:
        grr = build_genomic_resource_repository()
    return build_fragment_score_from_resource(grr.get_resource(resource_id))


def build_score_from_resource(
    resource: GenomicResource,
) -> GenomicScore:
    """Build the score of a score resource, whatever its kind.

    The function reads the ``type`` of the resource and calls the factory
    of that kind. Use the factory of the kind when the type is known. Use
    this function when the type is not.

    Parameters
    ----------
    resource : GenomicResource
        A resource of type ``position_score``, ``allele_score`` or
        ``fragment_score``. The legacy type ``cnv_collection`` also
        names a fragment score.

    Returns
    -------
    GenomicScore
        A new :class:`PositionScore`, :class:`AlleleScore` or
        :class:`FragmentScore`. Each call returns a new score that the
        caller owns, so closing it affects nothing else. The score is
        closed: call ``open()`` before the first read.

    Raises
    ------
    ValueError
        If the resource is not of a score type, or if it declares a
        retired type such as ``np_score``.
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
    """Build the score of the score resource with a given id.

    The function finds the resource in the repository and calls
    :func:`build_score_from_resource`.

    Parameters
    ----------
    resource_id : str
        The id of a score resource in the repository.
    grr : GenomicResourceRepo | None, default None
        The repository that resolves ``resource_id``. When it is
        ``None``, the function builds the default repository with
        ``build_genomic_resource_repository()``.

    Returns
    -------
    GenomicScore
        A new :class:`PositionScore`, :class:`AlleleScore` or
        :class:`FragmentScore`. The score is closed: call ``open()``
        before the first read.

    Raises
    ------
    FileNotFoundError
        If the repository has no resource with this id.
    ValueError
        If the resource is not of a score type, or if it declares a
        retired type such as ``np_score``.
    """
    if grr is None:
        grr = build_genomic_resource_repository()
    return build_score_from_resource(grr.get_resource(resource_id))
