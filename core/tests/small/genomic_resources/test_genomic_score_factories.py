# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Tests for the typed genomic score factories.

The point of the typed factories is a return type mypy can narrow, and mypy
does not run over ``core/tests``.  What these tests can pin is the runtime
half of that contract: the concrete class actually returned, the rejection of
a mismatched resource type, the CNV collection's shared-instance caching, and
the ``_from_resource_id`` repository resolution.
"""
import pathlib
from collections.abc import Callable

import pytest
import pytest_mock
from gain.genomic_resources.genomic_scores import (
    AlleleScore,
    CnvCollection,
    GenomicScore,
    PositionScore,
    build_allele_score_from_resource,
    build_allele_score_from_resource_id,
    build_cnv_collection_from_resource,
    build_cnv_collection_from_resource_id,
    build_position_score_from_resource,
    build_position_score_from_resource_id,
    build_score_from_resource,
)
from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceRepo,
)
from gain.genomic_resources.testing.builders import (
    a_cnv_collection,
    a_gene_score,
    a_grr,
    a_np_score,
    a_position_score,
    an_allele_score,
)

# A `build_*_from_resource` factory: resource in, concrete score out.
FromResource = Callable[[GenomicResource], GenomicScore]
# A `build_*_from_resource_id` factory: `grr` defaults to the ambient GRR.
FromResourceId = Callable[..., GenomicScore]


@pytest.fixture
def grr(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    """A GRR holding one resource of each score type, plus a decoy."""
    return (
        a_grr()
        .with_resource("scores/pos", a_position_score())
        .with_resource("scores/allele", an_allele_score())
        .with_resource("scores/np", a_np_score())
        .with_resource("scores/cnv", a_cnv_collection())
        .build_repo(tmp_path)
    )


@pytest.mark.parametrize("factory,resource_id,expected", [
    (build_position_score_from_resource, "scores/pos", PositionScore),
    (build_allele_score_from_resource, "scores/allele", AlleleScore),
    (build_allele_score_from_resource, "scores/np", AlleleScore),
    (build_cnv_collection_from_resource, "scores/cnv", CnvCollection),
])
def test_from_resource_returns_the_concrete_type(
    grr: GenomicResourceRepo,
    factory: FromResource,
    resource_id: str,
    expected: type,
) -> None:
    score = factory(grr.get_resource(resource_id))
    assert type(score) is expected


@pytest.mark.parametrize("factory,resource_id,expected", [
    (build_position_score_from_resource_id, "scores/pos", PositionScore),
    (build_allele_score_from_resource_id, "scores/allele", AlleleScore),
    (build_allele_score_from_resource_id, "scores/np", AlleleScore),
    (build_cnv_collection_from_resource_id, "scores/cnv", CnvCollection),
])
def test_from_resource_id_returns_the_concrete_type(
    grr: GenomicResourceRepo,
    factory: FromResourceId,
    resource_id: str,
    expected: type,
) -> None:
    score = factory(resource_id, grr)
    assert type(score) is expected


@pytest.mark.parametrize("factory,resource_id", [
    (build_position_score_from_resource, "scores/allele"),
    (build_position_score_from_resource, "scores/cnv"),
    (build_allele_score_from_resource, "scores/pos"),
    (build_allele_score_from_resource, "scores/cnv"),
    (build_cnv_collection_from_resource, "scores/pos"),
    (build_cnv_collection_from_resource, "scores/allele"),
])
def test_from_resource_rejects_a_mismatched_resource_type(
    grr: GenomicResourceRepo,
    factory: FromResource,
    resource_id: str,
) -> None:
    with pytest.raises(ValueError, match="should be of"):
        factory(grr.get_resource(resource_id))


@pytest.mark.parametrize("factory,resource_id", [
    (build_position_score_from_resource_id, "scores/cnv"),
    (build_allele_score_from_resource_id, "scores/pos"),
    (build_cnv_collection_from_resource_id, "scores/allele"),
])
def test_from_resource_id_rejects_a_mismatched_resource_type(
    grr: GenomicResourceRepo,
    factory: FromResourceId,
    resource_id: str,
) -> None:
    with pytest.raises(ValueError, match="should be of"):
        factory(resource_id, grr)


def test_cnv_collection_is_cached_and_shared(
    grr: GenomicResourceRepo,
) -> None:
    """Repeated builds hand back the SAME object, not an equal one.

    Pins the deliberate asymmetry: cnv_collection is cached process-wide,
    position/allele scores are not.
    """
    resource = grr.get_resource("scores/cnv")

    first = build_cnv_collection_from_resource(resource)
    second = build_cnv_collection_from_resource(resource)
    assert first is second

    # ... and the generic dispatcher hands back that same cached instance.
    assert build_score_from_resource(resource) is first
    assert build_cnv_collection_from_resource_id("scores/cnv", grr) is first


def test_position_and_allele_scores_are_not_cached(
    grr: GenomicResourceRepo,
) -> None:
    pos = grr.get_resource("scores/pos")
    assert build_position_score_from_resource(pos) \
        is not build_position_score_from_resource(pos)

    allele = grr.get_resource("scores/allele")
    assert build_allele_score_from_resource(allele) \
        is not build_allele_score_from_resource(allele)


@pytest.mark.parametrize("factory,resource_id", [
    (build_position_score_from_resource_id, "scores/pos"),
    (build_allele_score_from_resource_id, "scores/allele"),
    (build_cnv_collection_from_resource_id, "scores/cnv"),
])
def test_from_resource_id_falls_back_to_the_default_repository(
    grr: GenomicResourceRepo,
    factory: FromResourceId,
    resource_id: str,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """With no ``grr`` argument the default GRR is built and used."""
    default_repo = mocker.patch(
        "gain.genomic_resources.genomic_scores"
        ".build_genomic_resource_repository",
        return_value=grr,
    )
    score = factory(resource_id)
    default_repo.assert_called_once_with()
    assert score.resource_id == resource_id


def test_dispatcher_rejects_a_non_score_resource(
    tmp_path: pathlib.Path,
) -> None:
    repo = (
        a_grr()
        .with_resource("genes/score", a_gene_score())
        .build_repo(tmp_path)
    )
    with pytest.raises(ValueError, match="is not of score type"):
        build_score_from_resource(repo.get_resource("genes/score"))
