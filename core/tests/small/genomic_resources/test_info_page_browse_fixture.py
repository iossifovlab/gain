# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""What the browse fixture's search terms are allowed to match.

``a_browse_repo`` exists to give ``info_pages_e2e`` an index page worth
driving: enough folders to descend through, and two terms whose *reason*
for matching differs -- one reaches a resource only through its summary,
the other only through its id.  The Playwright suite asserts that typing
each one leaves exactly one row in the table.

Those assertions are only worth anything while the two terms really do
discriminate, and nothing about the fixture makes that self-evident. An
unqualified FTS5 ``MATCH`` searches every indexed column -- ``full_id``,
``id``, ``type``, ``description``, ``summary``, a score's ``score_ids``
and ``score_descriptions``, and every label -- so a term that leaks into
any of them still matches, from the wrong column, and the browser test
stays green while proving something weaker than it claims.

Pinned here rather than in the browser, because this is a property of the
fixture and of the index built from it: a fast Python test fails on the
commit that retunes a summary, instead of leaving a passing e2e suite
that has quietly stopped distinguishing the two routes.
"""
import pathlib

from gain.genomic_resources.cli import _create_contents_db
from gain.genomic_resources.repository import (
    GR_INDEX_NON_LABEL_COLUMNS,
    GenomicResourceProtocolRepo,
)
from gain.genomic_resources.testing import build_filesystem_test_protocol
from gain.genomic_resources.testing.info_page_fixtures import (
    BROWSE_ID_ONLY_RESOURCE_ID,
    BROWSE_ID_ONLY_TERM,
    BROWSE_RESOURCE_IDS,
    BROWSE_SUMMARY_ONLY_RESOURCE_ID,
    BROWSE_SUMMARY_ONLY_TERM,
    BROWSE_TOP_LEVEL_FOLDERS,
    a_browse_repo,
)

#: The columns that carry a resource's id.  A term spelled in an id
#: reaches the index through both of them.
_ID_COLUMNS = frozenset({"full_id", "id"})

#: The column a resource's summary is indexed in.
_SUMMARY_COLUMN = frozenset({"summary"})


def _indexed_browse_repo(
    tmp_path: pathlib.Path,
) -> GenomicResourceProtocolRepo:
    """The browse fixture with an FTS index built into it."""
    repo = a_browse_repo(tmp_path)
    _create_contents_db(build_filesystem_test_protocol(tmp_path))
    return repo


def _found(repo: GenomicResourceProtocolRepo, term: str) -> set[str]:
    """The resource ids an FTS5 expression matches."""
    return {res.resource_id for res in repo.search_resources(search_term=term)}


def _only(columns: frozenset[str]) -> str:
    """An FTS5 column filter naming exactly these columns."""
    return "{" + " ".join(sorted(columns)) + "}"


def _anywhere_but(columns: frozenset[str]) -> str:
    """An FTS5 column filter naming every indexed column except these.

    Built from the index's own column vocabulary rather than spelled out,
    so a column added to the index later is covered without anyone
    remembering to come back here -- which is the whole failure this
    guards against.  ``test_the_fixture_declares_no_labels`` is what
    keeps that vocabulary complete: a label would add a column these
    constants do not know about.

    Paired with :func:`_only` so the two halves of an assertion are fed
    from one set of column names: spelling the included half literally
    lets a third id-bearing column be excluded correctly and included
    wrongly, on adjacent lines.
    """
    return _only(frozenset(GR_INDEX_NON_LABEL_COLUMNS) - columns)


def test_the_summary_only_term_finds_only_that_resource(
    tmp_path: pathlib.Path,
) -> None:
    """The term reaches its resource, and reaches no other."""
    repo = _indexed_browse_repo(tmp_path)

    assert _found(repo, BROWSE_SUMMARY_ONLY_TERM) == {
        BROWSE_SUMMARY_ONLY_RESOURCE_ID}


def test_the_summary_only_term_matches_through_the_summary_alone(
    tmp_path: pathlib.Path,
) -> None:
    """It matches in the summary column, and in no other column.

    Asked of the index column by column rather than by looking for the
    word in the resource ids.  ``description`` is the column that makes
    the difference: it is a sibling of ``summary`` holding prose about
    the same resource, so a later slice giving this resource a
    description that repeats its summary -- an entirely natural edit --
    would leave the word matching from *two* columns.  The term would
    still find exactly one resource and still appear in no id, so every
    id-based check stays green while the browser suite's claim to be
    proving that summaries are searchable has quietly become false.
    """
    repo = _indexed_browse_repo(tmp_path)

    assert _found(
        repo, f"{_only(_SUMMARY_COLUMN)} : {BROWSE_SUMMARY_ONLY_TERM}") == {
        BROWSE_SUMMARY_ONLY_RESOURCE_ID}
    assert _found(
        repo,
        f"{_anywhere_but(_SUMMARY_COLUMN)} : "
        f"{BROWSE_SUMMARY_ONLY_TERM}") == set()


def test_the_id_only_term_finds_only_that_resource(
    tmp_path: pathlib.Path,
) -> None:
    """The other term reaches its resource through the id."""
    repo = _indexed_browse_repo(tmp_path)

    assert _found(repo, BROWSE_ID_ONLY_TERM) == {
        BROWSE_ID_ONLY_RESOURCE_ID}


def test_the_id_only_term_matches_through_the_id_alone(
    tmp_path: pathlib.Path,
) -> None:
    """The mirror: it matches in the id columns and nowhere else.

    A term spelled in some resource's summary too would make "found via
    id" indistinguishable from "found via summary", which is the one
    thing the pair exists to tell apart.  Leaking into ``score_ids`` --
    a score called ``phylop`` is the obvious future edit -- does the same
    damage and no id- or summary-based check would see it.
    """
    repo = _indexed_browse_repo(tmp_path)

    assert _found(
        repo, f"{_only(_ID_COLUMNS)} : {BROWSE_ID_ONLY_TERM}") == {
        BROWSE_ID_ONLY_RESOURCE_ID}
    assert _found(
        repo,
        f"{_anywhere_but(_ID_COLUMNS)} : {BROWSE_ID_ONLY_TERM}") == set()


def test_the_fixture_declares_no_labels(
    tmp_path: pathlib.Path,
) -> None:
    """No resource carries a label, so the index has no label columns.

    The two column-filtered tests above enumerate "every other column"
    from the index's fixed vocabulary.  A label becomes a column of its
    own, outside that vocabulary, so a labelled resource would put a
    column beyond their reach and silently narrow what they exclude.
    """
    repo = _indexed_browse_repo(tmp_path)

    labelled = {
        res.resource_id
        for res in repo.search_resources()
        if res.get_labels()
    }

    assert labelled == set()


def test_the_declared_top_level_folders_are_the_real_ones(
    tmp_path: pathlib.Path,
) -> None:
    """``BROWSE_TOP_LEVEL_FOLDERS`` says what the fixture holds.

    The tree assertions in ``info_pages_e2e`` read a hand-copied twin of
    this constant, so nothing on the TypeScript side can notice it going
    stale -- a fourth top-level folder added here would be described
    wrongly by both copies at once.
    """
    repo = _indexed_browse_repo(tmp_path)

    top_level = sorted({
        res.resource_id.split("/")[0] for res in repo.search_resources()})

    assert top_level == sorted(BROWSE_TOP_LEVEL_FOLDERS)
    assert sorted(
        res.resource_id for res in repo.search_resources()
    ) == sorted(BROWSE_RESOURCE_IDS)
