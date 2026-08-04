# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""``search_resources(search_term=...)`` and what it does with a bad term.

The term is bound into an FTS5 ``MATCH``, whose expression grammar is a
sublanguage of its own: an empty term and a malformed one are both rejected
by FTS5 rather than by anything in this repository. These tests pin what a
caller sees instead of the ``apsw.SQLError`` that used to escape --
everything for an empty term (gain#633), a named error for a malformed one
(gain#632).
"""
import gzip
import pathlib

import apsw
import pytest
from gain.genomic_resources.cli import _create_contents_db
from gain.genomic_resources.repository import (
    GR_SQLITE_META_FILE_NAME,
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


def test_an_empty_resource_type_is_an_unset_one(
    unindexed_grr: GenomicResourceProtocolRepo,
) -> None:
    """The other filter is unset when blank too (gain#653).

    Same accident as an empty term, and the same two symptoms: nothing
    found where everything was meant to be, and a repository with no
    index reporting one as missing for a filter nobody set.
    """
    found = {res.resource_id for res in unindexed_grr.search_resources(
        resource_type="")}

    assert found == {"scores/res_a", "other/res_b"}


def test_a_padded_resource_type_still_selects(
    indexed_grr: GenomicResourceProtocolRepo,
) -> None:
    """A type name has no use for the spaces around it.

    Unlike a search term, where a space separates the parts of an
    expression, a resource type is one token: ``-t " position_score "``
    can only have meant the type, and matched nothing instead.
    """
    found = {res.resource_id for res in indexed_grr.search_resources(
        resource_type=" position_score ")}

    assert found == {"scores/res_a", "other/res_b"}


@pytest.mark.parametrize("blank", [" ", "\t", "   \n "])
def test_a_whitespace_only_search_term_is_an_unset_one(
    unindexed_grr: GenomicResourceProtocolRepo,
    blank: str,
) -> None:
    """``-s "$VAR "`` is the same accident as ``-s "$VAR"`` (gain#633).

    FTS5 reads a run of whitespace as an empty expression and rejects it,
    so without this these arrive as a malformed search naming a term the
    user cannot see.
    """
    found = {res.resource_id for res in unindexed_grr.search_resources(
        search_term=blank)}

    assert found == {"scores/res_a", "other/res_b"}


def test_a_malformed_term_is_refused_even_when_nothing_could_match(
    indexed_grr: GenomicResourceProtocolRepo,
) -> None:
    """The verdict on a term cannot depend on the other arguments.

    A resource query naming a label the index has not got can match
    nothing, and SQLite folds the whole ``WHERE`` away rather than
    running the ``MATCH`` -- so a term checked only by being executed
    goes unexamined, and the same malformed search that is a 400 on its
    own comes back 200 with an empty list.
    """
    with pytest.raises(SearchTermError):
        list(indexed_grr.search_resources(
            search_term='"', resource_query='*[nosuchkey="x"]'))


def _republish_index_with_an_odd_column(grr_path: pathlib.Path) -> None:
    """Rebuild the index carrying a column gain would not have created.

    SQLite accepts a quoted identifier that gain's own index build
    refuses, so a repository published by other hands can have one --
    and the search that names it is a search this repository can answer.
    """
    index_path = grr_path / GR_SQLITE_META_FILE_NAME
    conn = apsw.Connection(":memory:")
    conn.deserialize("main", gzip.decompress(index_path.read_bytes()))
    rows = list(conn.execute("SELECT full_id, type FROM contents"))
    conn.execute("DROP TABLE contents")
    conn.execute(
        'CREATE VIRTUAL TABLE contents USING fts5(full_id, type, "ref-genome")',
    )
    for full_id, res_type in rows:
        conn.execute(
            'INSERT INTO contents (full_id, type, "ref-genome") '
            "VALUES (?, ?, ?)",
            (full_id, res_type, "hg38"))
    index_path.write_bytes(gzip.compress(conn.serialize("main")))


def test_a_column_filter_on_an_unconventional_column_is_not_refused(
    indexed_grr: GenomicResourceProtocolRepo,
    tmp_path: pathlib.Path,
) -> None:
    """The check must not be stricter than the index it stands in for.

    A term is validated against a stand-in built from the real index's
    columns; a stand-in missing some of them rejects a column filter the
    repository would have answered -- the one direction of error that
    turns a working search into a 400.
    """
    _republish_index_with_an_odd_column(tmp_path)

    found = {res.resource_id for res in indexed_grr.search_resources(
        search_term='"ref-genome" : hg38')}

    assert found == {"scores/res_a", "other/res_b"}


def _break_the_index(grr_path: pathlib.Path) -> None:
    """Leave a ``contents`` that is not the FTS table it should be.

    One of several shapes a damaged or half-published index takes; every
    one of them fails the same statement a bad term fails, which is what
    makes them worth telling apart.
    """
    index_path = grr_path / GR_SQLITE_META_FILE_NAME
    conn = apsw.Connection(":memory:")
    conn.deserialize("main", gzip.decompress(index_path.read_bytes()))
    conn.execute("DROP TABLE contents")
    conn.execute("CREATE TABLE contents (full_id, type, domain)")
    index_path.write_bytes(gzip.compress(conn.serialize("main")))


def test_a_broken_index_is_not_blamed_on_the_search_term(
    indexed_grr: GenomicResourceProtocolRepo,
    tmp_path: pathlib.Path,
) -> None:
    """A repository fault must not be reported as the caller's typo.

    Both failures come out of the same ``execute``, so "a term was given"
    cannot be what tells them apart: that would answer 400 to every
    search against a repository whose index needs repairing, and the
    breakage would never be visible as one.
    """
    _break_the_index(tmp_path)

    with pytest.raises(apsw.SQLError):
        list(indexed_grr.search_resources(search_term="domain_a"))


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
