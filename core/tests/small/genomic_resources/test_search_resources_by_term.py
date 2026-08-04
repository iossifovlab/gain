# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""``search_resources(search_term=...)`` and what it does with a bad term.

The term is bound into an FTS5 ``MATCH``, whose expression grammar is a
sublanguage of its own: an empty term and a malformed one are both rejected
by FTS5 rather than by anything in this repository. These tests pin what a
caller sees instead of the ``apsw.SQLError`` that used to escape --
everything for an empty term (gain#633), a named error for a malformed one
(gain#632).
"""
import pathlib

import pytest
from gain.genomic_resources.cli import _create_contents_db
from gain.genomic_resources.repository import (
    GenomicResourceProtocolRepo,
    SearchTermError,
)
from gain.genomic_resources.testing import build_filesystem_test_protocol
from gain.genomic_resources.testing.builders import (
    a_grr,
    a_position_score,
)


@pytest.fixture
def unindexed_grr(tmp_path: pathlib.Path) -> GenomicResourceProtocolRepo:
    """A repository with resources but no FTS index.

    The shape a plain checked-out GRR has before ``grr_manage`` builds an
    index into it, and the one on which an empty term must not be mistaken
    for a search that needs an index.
    """
    return (
        a_grr()
        .with_resource(
            "scores/res_a",
            a_position_score().with_labels(domain="domain_a"),
        )
        .with_resource(
            "other/res_b",
            a_position_score().with_labels(domain="domain_b"),
        )
        .build_repo(tmp_path)
    )


@pytest.fixture
def indexed_grr(tmp_path: pathlib.Path) -> GenomicResourceProtocolRepo:
    """The same repository with an FTS index built into it."""
    repo = (
        a_grr()
        .with_resource(
            "scores/res_a",
            a_position_score().with_labels(domain="domain_a"),
        )
        .with_resource(
            "other/res_b",
            a_position_score().with_labels(domain="domain_b"),
        )
        .build_repo(tmp_path)
    )
    _create_contents_db(build_filesystem_test_protocol(tmp_path))
    return repo


def test_an_empty_search_term_needs_no_index(
    unindexed_grr: GenomicResourceProtocolRepo,
) -> None:
    """An empty term is unset, so it must not reach for the index.

    Normalising it any later than the branch that decides whether to open
    the index leaves this case reporting a missing index to a caller who
    never asked to search.
    """
    found = {res.resource_id for res in unindexed_grr.search_resources(
        search_term="")}

    assert found == {"scores/res_a", "other/res_b"}


def test_a_malformed_search_term_is_reported_as_such(
    indexed_grr: GenomicResourceProtocolRepo,
) -> None:
    """A term FTS5 cannot parse is the caller's mistake, and says so.

    ``apsw.SQLError`` names neither the term nor the argument it came
    from, and carries a database failure's meaning to a caller who only
    mistyped a search (gain#632).
    """
    with pytest.raises(SearchTermError) as excinfo:
        list(indexed_grr.search_resources(search_term='"'))

    assert '"' in str(excinfo.value)


def test_a_column_filter_is_still_a_search_expression(
    indexed_grr: GenomicResourceProtocolRepo,
) -> None:
    """``key : value`` selects on a label, and must keep doing so.

    This is what stops the malformed-term fix from being "quote the term
    into a literal": label keys are columns of the FTS index, and the
    column filter is a supported search. Quoted, this term would be text
    to look for and would match nothing.
    """
    found = {res.resource_id for res in indexed_grr.search_resources(
        search_term="domain : domain_a")}

    assert found == {"scores/res_a"}
