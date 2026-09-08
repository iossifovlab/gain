# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""What ``resource_type`` means on a repository with no search index.

``search_resources`` takes three filters. Only ``search_term`` genuinely
needs the FTS index -- FTS5 tokenisation is not reproducible in Python. A
type is one token every resource already carries, so a type filter with no
term beside it is answered in Python, over ``get_all_resources()``, exactly
as a ``resource_query`` on its own is (gain#1212).

These tests are written against a repository that publishes
``.CONTENTS.json.gz`` and no ``.CONTENTS.sqlite3.gz`` -- the shape of a
plain checked-out GRR, which the CLI calls the common case.
"""
from collections.abc import Iterable

import pytest
from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceProtocolRepo,
)
from gain.genomic_resources.testing.builders import (
    a_fragment_score,
    a_grr,
    a_position_score,
    a_reference_genome,
)


@pytest.fixture(scope="module")
def unindexed_mixed_grr(
    tmp_path_factory: pytest.TempPathFactory,
) -> GenomicResourceProtocolRepo:
    """Resources of three types, so a type filter has something to reject.

    The fragment score is spelled the legacy way on purpose: asking for it
    by the preferred spelling is what proves the filter expands the type
    rather than comparing the string.
    """
    return (
        a_grr()
        .with_resource(
            "scores/res_a",
            a_position_score().with_labels(domain="alpha"),
        )
        .with_resource(
            "scores/res_b",
            a_position_score().with_labels(domain="beta"),
        )
        .with_resource("genomes/res_g", a_reference_genome())
        .with_resource(
            "fragments/res_f",
            a_fragment_score().with_resource_type("cnv_collection"),
        )
        .build_repo(tmp_path_factory.mktemp("unindexed_mixed_grr"))
    )


def _ids(resources: Iterable[GenomicResource]) -> set[str]:
    return {res.resource_id for res in resources}


def test_a_type_alone_needs_no_index(
    unindexed_mixed_grr: GenomicResourceProtocolRepo,
) -> None:
    found = _ids(
        unindexed_mixed_grr.search_resources(resource_type="position_score"))

    assert found == {"scores/res_a", "scores/res_b"}


@pytest.mark.parametrize("spelling", ["fragment_score", "cnv_collection"])
def test_either_fragment_score_spelling_finds_the_other(
    unindexed_mixed_grr: GenomicResourceProtocolRepo, spelling: str,
) -> None:
    """The type is expanded, not compared, as the indexed route does it."""
    found = _ids(unindexed_mixed_grr.search_resources(resource_type=spelling))

    assert found == {"fragments/res_f"}


@pytest.mark.parametrize(("resource_type", "query", "expected"), [
    ("genome", "*", {"genomes/res_g"}),
    ("position_score", '*[domain="beta"]', {"scores/res_b"}),
    ("genome", "scores/*", set()),
])
def test_a_type_and_a_query_conjoin_without_an_index(
    unindexed_mixed_grr: GenomicResourceProtocolRepo,
    resource_type: str, query: str, expected: set[str],
) -> None:
    found = _ids(unindexed_mixed_grr.search_resources(
        resource_type=resource_type, resource_query=query))

    assert found == expected


def test_a_term_beside_a_type_still_needs_the_index(
    unindexed_mixed_grr: GenomicResourceProtocolRepo,
) -> None:
    """Only the term is the index's filter; adding a type does not excuse it."""
    with pytest.raises(ValueError, match="SQLite metadata DB not found"):
        list(unindexed_mixed_grr.search_resources(
            search_term="alpha", resource_type="position_score"))


@pytest.mark.parametrize("blank", ["", " ", "\t"])
def test_a_blank_type_selects_everything_and_opens_no_index(
    unindexed_mixed_grr: GenomicResourceProtocolRepo, blank: str,
) -> None:
    found = _ids(unindexed_mixed_grr.search_resources(resource_type=blank))

    assert found == _ids(unindexed_mixed_grr.get_all_resources())
    assert len(found) == 4
