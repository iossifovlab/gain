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
from gain.genomic_resources.repository import GenomicResourceProtocolRepo
from gain.genomic_resources.testing import build_filesystem_test_protocol
from gain.genomic_resources.testing.info_page_fixtures import (
    BROWSE_ID_ONLY_RESOURCE_ID,
    BROWSE_ID_ONLY_TERM,
    BROWSE_SUMMARY_ONLY_RESOURCE_ID,
    BROWSE_SUMMARY_ONLY_TERM,
    a_browse_repo,
)


def _indexed_browse_repo(
    tmp_path: pathlib.Path,
) -> GenomicResourceProtocolRepo:
    """The browse fixture with an FTS index built into it."""
    repo = a_browse_repo(tmp_path)
    _create_contents_db(build_filesystem_test_protocol(tmp_path))
    return repo


def test_the_summary_only_term_finds_only_that_resource(
    tmp_path: pathlib.Path,
) -> None:
    """The term reaches its resource, and reaches no other."""
    repo = _indexed_browse_repo(tmp_path)

    found = {
        res.resource_id
        for res in repo.search_resources(search_term=BROWSE_SUMMARY_ONLY_TERM)
    }

    assert found == {BROWSE_SUMMARY_ONLY_RESOURCE_ID}


def test_the_summary_only_term_appears_in_no_resource_id(
    tmp_path: pathlib.Path,
) -> None:
    """It matches through the summary, not through the id.

    Without this, a term that happened to also spell part of an id would
    satisfy the test above while the browser suite believed it was
    proving that summaries are searchable at all.
    """
    repo = _indexed_browse_repo(tmp_path)

    ids = [res.resource_id for res in repo.search_resources()]

    assert ids
    assert not any(BROWSE_SUMMARY_ONLY_TERM in res_id for res_id in ids)


def test_the_id_only_term_finds_only_that_resource(
    tmp_path: pathlib.Path,
) -> None:
    """The other term reaches its resource through the id."""
    repo = _indexed_browse_repo(tmp_path)

    found = {
        res.resource_id
        for res in repo.search_resources(search_term=BROWSE_ID_ONLY_TERM)
    }

    assert found == {BROWSE_ID_ONLY_RESOURCE_ID}


def test_the_id_only_term_appears_in_no_summary(
    tmp_path: pathlib.Path,
) -> None:
    """And reaches it through the id *only*.

    The mirror of the summary check: a term spelled in some resource's
    summary as well would make "found via id" indistinguishable from
    "found via summary", which is the one thing the pair exists to tell
    apart.
    """
    repo = _indexed_browse_repo(tmp_path)

    summaries = [res.get_summary() or "" for res in repo.search_resources()]

    assert summaries
    assert not any(BROWSE_ID_ONLY_TERM in summary for summary in summaries)
