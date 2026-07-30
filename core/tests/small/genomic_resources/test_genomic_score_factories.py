# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Tests for the typed genomic score factories.

The point of the typed factories is a return type mypy can narrow, and mypy
does not run over ``core/tests``.  What these tests can pin is the runtime
half of that contract: the concrete class actually returned, the rejection of
a mismatched resource type, the fragment score's shared-instance caching,
and the ``_from_resource_id`` repository resolution.
"""
import pathlib
from collections.abc import Callable

import pytest
import pytest_mock
from gain.genomic_resources.genomic_scores import (
    AlleleScore,
    FragmentScore,
    GenomicScore,
    PositionScore,
    build_allele_score_from_resource,
    build_allele_score_from_resource_id,
    build_fragment_score_from_resource,
    build_fragment_score_from_resource_id,
    build_position_score_from_resource,
    build_position_score_from_resource_id,
    build_score_from_resource,
)
from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceRepo,
)
from gain.genomic_resources.testing.builders import (
    a_fragment_score,
    a_gene_score,
    a_grr,
    a_np_score,
    a_position_score,
    an_allele_score,
)

# The wrong-resource-type rejection message, spelled out in full: the three
# score constructors each build it by hand, so a loose `should be of` match
# lets one of them drift (a dropped space, a misspelled word) unnoticed.
_WRONG_TYPE_MESSAGE = (
    r"The resource provided to \w+ should be of "
    r"'\w+'(?: or '\w+')* type, not a '\w+'"
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
        .with_resource("scores/fragment", a_fragment_score())
        .build_repo(tmp_path)
    )


@pytest.mark.parametrize("factory,resource_id,expected", [
    (build_position_score_from_resource, "scores/pos", PositionScore),
    (build_allele_score_from_resource, "scores/allele", AlleleScore),
    (build_allele_score_from_resource, "scores/np", AlleleScore),
    (build_fragment_score_from_resource, "scores/fragment", FragmentScore),
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
    (build_fragment_score_from_resource_id, "scores/fragment", FragmentScore),
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
    (build_position_score_from_resource, "scores/fragment"),
    (build_allele_score_from_resource, "scores/pos"),
    (build_allele_score_from_resource, "scores/fragment"),
    (build_fragment_score_from_resource, "scores/pos"),
    (build_fragment_score_from_resource, "scores/allele"),
])
def test_from_resource_rejects_a_mismatched_resource_type(
    grr: GenomicResourceRepo,
    factory: FromResource,
    resource_id: str,
) -> None:
    with pytest.raises(ValueError, match=_WRONG_TYPE_MESSAGE):
        factory(grr.get_resource(resource_id))


@pytest.mark.parametrize("factory,resource_id", [
    (build_position_score_from_resource_id, "scores/fragment"),
    (build_allele_score_from_resource_id, "scores/pos"),
    (build_fragment_score_from_resource_id, "scores/allele"),
])
def test_from_resource_id_rejects_a_mismatched_resource_type(
    grr: GenomicResourceRepo,
    factory: FromResourceId,
    resource_id: str,
) -> None:
    with pytest.raises(ValueError, match=_WRONG_TYPE_MESSAGE):
        factory(resource_id, grr)


def test_no_score_kind_is_shared_between_builds(
    grr: GenomicResourceRepo,
) -> None:
    """Every kind hands back a FRESH score, so each caller owns what it got.

    A shared instance would mean one holder's ``close()`` closing it for
    every other holder, which is why no kind is shared.
    """
    resource = grr.get_resource("scores/fragment")
    first = build_fragment_score_from_resource(resource)

    assert build_fragment_score_from_resource(resource) is not first
    # ... and neither does the generic dispatcher nor the by-id factory
    # hand back something already given out.
    assert build_score_from_resource(resource) is not first
    assert build_fragment_score_from_resource_id(
        "scores/fragment", grr) is not first


def test_two_versions_of_one_resource_id_are_distinct_scores(
    tmp_path: pathlib.Path,
) -> None:
    """``fragments(2.0)`` reads its own data, not ``fragments(1.0)``'s.

    ``get_id()`` is version-less, so anything keyed on it alone would
    conflate the two and hand back the older version's data.
    """
    repo = (
        a_grr()
        .with_resource("fragments(1.0)", a_fragment_score().with_data("""
            chrom  pos_begin  pos_end  score
            1      100        200      0.1
        """))
        .with_resource("fragments(2.0)", a_fragment_score().with_data("""
            chrom  pos_begin  pos_end  score
            1      300        400      0.2
        """))
        .build_repo(tmp_path)
    )

    old = build_fragment_score_from_resource(
        repo.get_resource("fragments(1.0)"))
    new = build_fragment_score_from_resource(
        repo.get_resource("fragments(2.0)"))

    assert old is not new
    assert old.resource.get_full_id() == "fragments(1.0)"
    assert new.resource.get_full_id() == "fragments(2.0)"

    with old.open() as old_open, new.open() as new_open:
        # Distinct score values, so each is demonstrably reading its own
        # version's data.
        assert old_open.fetch_fragment_scores("1", 1, 1000) \
            == [{"score": 0.1}]
        assert new_open.fetch_fragment_scores("1", 1, 1000) \
            == [{"score": 0.2}]


def test_position_and_allele_scores_are_fresh_per_build(
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
    (build_fragment_score_from_resource_id, "scores/fragment"),
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
