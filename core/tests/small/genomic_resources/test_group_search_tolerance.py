# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""What a group repository does with a child that cannot answer a filter.

A search filter is not equally answerable by every child of a group. A
``search_term`` carrying an FTS5 column filter is a valid expression only
against an index that publishes the column, and the column vocabulary is
per-repository; a child may also have no index at all, or one with no
``contents`` table. Each of those used to take the whole search down, after
the children that *could* answer had already streamed their rows.

These tests pin ADR 0012: such a child is skipped with a warning, and the
search fails only when no child could answer (gain#680).
"""
import argparse
import pathlib

import pytest
from gain.genomic_resources.cached_repository import (
    GenomicResourceCachedRepo,
)
from gain.genomic_resources.cli import _create_contents_db
from gain.genomic_resources.cli_list import run_list_command
from gain.genomic_resources.group_repository import GenomicResourceGroupRepo
from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceProtocolRepo,
    GenomicResourceRepo,
    SearchIndexUnavailableError,
    SearchTermError,
)
from gain.genomic_resources.testing import build_filesystem_test_protocol
from gain.genomic_resources.testing.builders import (
    a_grr,
    a_position_score,
)


def _build_child(
    root: pathlib.Path,
    resource_id: str,
    labels: dict[str, str],
    *,
    indexed: bool = True,
) -> GenomicResourceProtocolRepo:
    """Realize a one-resource GRR at ``root``, optionally with an index."""
    repo = (
        a_grr()
        .with_resource(resource_id, a_position_score().with_labels(**labels))
        .build_repo(root)
    )
    if indexed:
        _create_contents_db(build_filesystem_test_protocol(root))
    return repo


@pytest.fixture
def disjoint_group(tmp_path: pathlib.Path) -> GenomicResourceGroupRepo:
    """Two indexed children whose label columns do not overlap.

    The shape of the reported bug: ``assay_term_name`` is a column of the
    encode index and of no other, so a column filter naming it is a valid
    FTS5 expression against one child and a syntax error against the other.
    """
    return GenomicResourceGroupRepo([
        _build_child(tmp_path / "one", "scores/a", {"assay": "atac"}),
        _build_child(tmp_path / "two", "scores/b", {"unrelated": "x"}),
    ])


def test_a_column_filter_only_one_child_indexes_is_answered_by_that_child(
    disjoint_group: GenomicResourceGroupRepo,
) -> None:
    """The child that publishes the column answers; the other is skipped.

    Before gain#680 this raised ``SearchTermError`` out of the second child,
    discarding the first child's rows along with it.
    """
    found = {
        res.resource_id
        for res in disjoint_group.search_resources(search_term='assay: "atac"')
    }

    assert found == {"scores/a"}


def test_a_column_filter_no_child_indexes_still_fails(
    disjoint_group: GenomicResourceGroupRepo,
) -> None:
    """Skipping is per-child; a filter nobody can answer is still an error.

    Absorbing this too would answer a mistyped label key with zero rows and
    no signal at all, which is the one thing a tolerant search must not do.
    """
    with pytest.raises(SearchTermError) as err:
        list(disjoint_group.search_resources(search_term='mistyped: "atac"'))

    assert "mistyped" in str(err.value)


def test_a_child_with_no_index_is_skipped_rather_than_fatal(
    tmp_path: pathlib.Path,
) -> None:
    """A freshly checked-out GRR has no index; that must not end the search.

    ``.CONTENTS.json.gz`` with no ``.CONTENTS.sqlite3.gz`` is the normal shape
    of a checked-out GRR, so a group is one repair away from this at any
    time.
    """
    group = GenomicResourceGroupRepo([
        _build_child(tmp_path / "one", "scores/a", {"assay": "atac"}),
        _build_child(
            tmp_path / "two", "scores/b", {"assay": "atac"}, indexed=False),
    ])

    found = {
        res.resource_id
        for res in group.search_resources(search_term="scores")
    }

    assert found == {"scores/a"}


def test_an_unrepaired_child_makes_the_failure_about_the_repository(
    tmp_path: pathlib.Path,
) -> None:
    """A probe rejection does not prove the filter wrong; a repair may fix it.

    The child that rejected the column filter proves only that *its* index
    lacks the column. If a sibling could not be read at all, repairing it
    might well publish that column -- so blaming the caller for a malformed
    search would be wrong, and would name a repair the caller cannot make.

    Ordered with the rejecting child first, so the verdict cannot come from
    whichever child happened to be skipped earliest.
    """
    group = GenomicResourceGroupRepo([
        _build_child(tmp_path / "one", "scores/a", {"unrelated": "x"}),
        _build_child(
            tmp_path / "two", "scores/b", {"assay": "atac"}, indexed=False),
    ])

    with pytest.raises(SearchIndexUnavailableError):
        list(group.search_resources(search_term='assay: "atac"'))


class _YieldsThenFails(GenomicResourceRepo):
    """A child that streams rows and only then discovers it cannot go on.

    Stands in for a failure the search cannot see coming -- an index that
    reads far enough to start and then does not, say. The point is only
    *when* it is raised, so it raises one of the two types the group
    absorbs before a first row.
    """

    def __init__(
        self, repo_id: str, resources: list[GenomicResource],
    ) -> None:
        super().__init__(repo_id)
        self.resources = resources

    def invalidate(self) -> None:
        return

    def get_all_resources(self):  # type: ignore[no-untyped-def]
        yield from self.resources

    def find_resource(  # type: ignore[no-untyped-def]
        self, resource_id, version_constraint=None, repository_id=None,
    ):
        return None

    def get_resource(  # type: ignore[no-untyped-def]
        self, resource_id, version_constraint=None, repository_id=None,
    ):
        raise ValueError(resource_id)

    def search_resources(  # type: ignore[no-untyped-def]
        self, search_term=None, resource_type=None, resource_query=None,
    ):
        def rows():  # type: ignore[no-untyped-def]
            yield from self.resources
            raise SearchTermError(search_term or "", ValueError("mid-scan"))
        return rows()


def test_a_child_failing_after_its_first_row_is_not_absorbed(
    tmp_path: pathlib.Path,
) -> None:
    """Absorption is bounded to failures raised before a child's first row.

    Both failures the group absorbs are raised before their child yields
    anything -- the index is opened and the filter probed ahead of the
    statement. Absorbing one that arrives mid-scan would turn a truncated
    child into a warning and still report success, which is the one way a
    tolerant search can silently lose resources.
    """
    healthy = _build_child(tmp_path / "one", "scores/a", {"assay": "atac"})
    partial = _YieldsThenFails(
        "half_answered", list(healthy.get_all_resources()))
    group = GenomicResourceGroupRepo([healthy, partial])

    with pytest.raises(SearchTermError):
        list(group.search_resources(search_term="scores"))


def test_a_contents_less_child_is_skipped_rather_than_answering_nothing(
    tmp_path: pathlib.Path,
) -> None:
    """An index with no ``contents`` table has not answered anything.

    That state means no resource could be indexed at all (gain#464), so
    yielding zero rows makes it indistinguishable from a child that
    searched and matched nothing. Reporting it as a skip is what lets the
    group tell "nothing matched" from "nothing was searched" -- otherwise a
    group whose every child is in this state answers a plainly malformed
    term with zero rows and success.
    """
    empty = tmp_path / "unindexable"
    empty.mkdir()
    repo = a_grr().build_repo(empty)
    _create_contents_db(build_filesystem_test_protocol(empty))
    group = GenomicResourceGroupRepo([repo])

    with pytest.raises(SearchIndexUnavailableError):
        list(group.search_resources(search_term='"unclosed'))


def test_a_nested_group_names_the_leaf_that_holds_the_resource(
    tmp_path: pathlib.Path,
) -> None:
    """The pair carries the repository that actually holds the resource.

    ``cli_list`` prints the holder's id beside each row and asks it for a
    cached-file count, so naming an intermediate group instead would label
    rows with a repository that holds nothing and lose the count.
    """
    inner_leaf = _build_child(tmp_path / "one", "scores/a", {"assay": "atac"})
    outer_leaf = _build_child(tmp_path / "two", "scores/b", {"assay": "atac"})
    group = GenomicResourceGroupRepo([
        GenomicResourceGroupRepo([inner_leaf], repo_id="inner"),
        outer_leaf,
    ])

    holders = {
        res.resource_id: child.repo_id
        for child, res in group.search_resources_by_child(
            search_term='assay: "atac"')
    }

    assert holders == {
        "scores/a": inner_leaf.repo_id,
        "scores/b": outer_leaf.repo_id,
    }


def test_a_cached_group_tolerates_a_child_that_cannot_answer(
    tmp_path: pathlib.Path,
) -> None:
    """A ``cache_dir`` on a group definition must not lose the tolerance.

    ``GenomicResourceCachedRepo(GenomicResourceGroupRepo(...))`` is not a
    ``GenomicResourceGroupRepo``, so ``cli_list`` does not take it apart and
    the search runs through the group itself. Wrapping is exactly the shape
    that would slip past a fix written only for the bare group.
    """
    group = GenomicResourceGroupRepo([
        _build_child(tmp_path / "one", "scores/a", {"assay": "atac"}),
        _build_child(tmp_path / "two", "scores/b", {"unrelated": "x"}),
    ])
    cached = GenomicResourceCachedRepo(group, str(tmp_path / "cache"))

    found = {
        res.resource_id
        for res in cached.search_resources(search_term='assay: "atac"')
    }

    assert found == {"scores/a"}


def test_grr_browse_lists_what_it_can_instead_of_exiting(
    disjoint_group: GenomicResourceGroupRepo,
    capsys: pytest.CaptureFixture,
) -> None:
    """The reported command: list the rows one child can answer for.

    ``grr_browse -s 'assay: "atac"'`` used to print the answering child's
    rows and then die on the next child. The listing now completes, and
    every row still carries the id of the repository that holds it.
    """
    run_list_command(
        disjoint_group, argparse.Namespace(search='assay: "atac"'))

    out, err = capsys.readouterr()
    assert err == ""
    assert "scores/a" in out
    assert "scores/b" not in out


def test_a_type_filter_is_skipped_per_child_like_a_term(
    tmp_path: pathlib.Path,
) -> None:
    """The rule is about a filter, not only about a search term.

    ``resource_type`` routes through the index too -- the search only
    short-circuits to ``get_all_resources`` when the term and the type are
    both unset -- so ``-t`` against a group with an unindexed child is the
    same bug, and must be skipped the same way.
    """
    group = GenomicResourceGroupRepo([
        _build_child(tmp_path / "one", "scores/a", {"assay": "atac"}),
        _build_child(
            tmp_path / "two", "scores/b", {"assay": "atac"}, indexed=False),
    ])

    found = {
        res.resource_id
        for res in group.search_resources(resource_type="position_score")
    }

    assert found == {"scores/a"}


def test_a_nested_group_reports_the_leaves_not_the_group_between(
    tmp_path: pathlib.Path,
) -> None:
    """The reasons flatten: a reader repairs a leaf, never a group.

    Naming the intermediate group would report a repository that has no
    index of its own to build, and would bury the ones that do behind a
    nested message.
    """
    inner_leaf = _build_child(
        tmp_path / "one", "scores/a", {"assay": "atac"}, indexed=False)
    outer_leaf = _build_child(
        tmp_path / "two", "scores/b", {"assay": "atac"}, indexed=False)
    group = GenomicResourceGroupRepo([
        GenomicResourceGroupRepo([inner_leaf], repo_id="the_middle_group"),
        outer_leaf,
    ])

    with pytest.raises(SearchIndexUnavailableError) as excinfo:
        list(group.search_resources(search_term="scores"))

    message = str(excinfo.value)
    assert inner_leaf.repo_id in message
    assert outer_leaf.repo_id in message
    assert "the_middle_group" not in message
