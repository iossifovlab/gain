# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""What the repository-scoped commands do to an emptied repository.

Deleting the last resource of a GRR leaves its published globals --
``.CONTENTS.json.gz``, the FTS search index and the repository index
page -- advertising a resource that is gone. A repository-scoped
command used to exit 0 without touching any of them, so the run
reported success and an http consumer kept serving the deleted
resource's manifest (gain#782).

There is no early exit any more: an empty repository is the n=0 case of
the normal path. Each command still publishes exactly its own artifact
set, which is what the byte-identical assertions below pin -- emptiness
does not promote a command to publishing artifacts it never touches.
"""
import gzip
import pathlib

import apsw
import pytest
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.repository import (
    GR_INDEX_FILE_NAME,
    GR_SQLITE_META_FILE_NAME,
)

from .conftest import (
    empty_the_repository,
    read_published_contents,
    snapshot_globals,
)


def read_published_index_ids(repo_path: pathlib.Path) -> set[str]:
    """The resource ids the published FTS search index carries.

    An index built with no resource in it has no ``contents`` table at
    all -- that is how the builder records an empty repository, and
    searching such a repository raises rather than answering with zero
    rows (gain#464, ADR 0012).  Carrying no table and carrying no id are
    the same fact to these tests, so it reads as the empty set.
    """
    conn = apsw.Connection(":memory:")
    conn.deserialize("main", gzip.decompress(
        (repo_path / GR_SQLITE_META_FILE_NAME).read_bytes()))
    # The same probe the search path makes before querying the index
    # (`_search_resources` in repository.py); there it turns into a
    # raise, here into the empty set.
    if not conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'contents'").fetchone():
        return set()
    # fetchall() rather than iterating the cursor: pylint cannot infer
    # that an apsw cursor is iterable and reports E1133 on the loop.
    return {
        row[0]
        for row in conn.execute("SELECT full_id FROM contents").fetchall()
    }


@pytest.mark.parametrize("argv", [
    # repair is info plus nothing, and repo-index is the publish step on
    # its own, so all three settle the same three artifacts.  Only the
    # commands that build a task graph take `-j`.
    ["repo-repair", "-j", "1"],
    ["repo-info", "-j", "1"],
    ["repo-index"],
], ids=lambda argv: str(argv[0]))
def test_the_full_commands_settle_all_three_globals(
    settled_repo: pathlib.Path,
    argv: list[str],
) -> None:
    empty_the_repository(settled_repo)

    cli_manage([*argv, "-R", str(settled_repo)])

    assert "sub/one" not in read_published_contents(settled_repo)
    assert read_published_index_ids(settled_repo) == set()
    assert "sub/one" not in (
        settled_repo / GR_INDEX_FILE_NAME).read_text()


def test_repo_stats_settles_the_two_artifacts_it_publishes(
    settled_repo: pathlib.Path,
) -> None:
    # repo-stats publishes the contents file and the search index, and
    # never the repository index page -- emptiness does not promote it
    # to publishing one.
    empty_the_repository(settled_repo)
    page_before = (settled_repo / GR_INDEX_FILE_NAME).read_bytes()

    cli_manage(["repo-stats", "-R", str(settled_repo), "-j", "1"])

    assert "sub/one" not in read_published_contents(settled_repo)
    assert read_published_index_ids(settled_repo) == set()
    assert (settled_repo / GR_INDEX_FILE_NAME).read_bytes() == page_before


def test_repo_manifest_settles_the_one_artifact_it_publishes(
    settled_repo: pathlib.Path,
) -> None:
    # repo-manifest publishes the contents file only.  The search index
    # and the repository index page are left exactly as they were --
    # stale, and settled by repo-repair or repo-index, as on a
    # repository that still has resources in it.
    empty_the_repository(settled_repo)
    index_before = (settled_repo / GR_SQLITE_META_FILE_NAME).read_bytes()
    page_before = (settled_repo / GR_INDEX_FILE_NAME).read_bytes()

    cli_manage(["repo-manifest", "-R", str(settled_repo)])

    assert "sub/one" not in read_published_contents(settled_repo)
    assert (
        settled_repo / GR_SQLITE_META_FILE_NAME).read_bytes() == index_before
    assert (settled_repo / GR_INDEX_FILE_NAME).read_bytes() == page_before


def test_repo_fix_histograms_on_an_emptied_repository_writes_nothing(
    settled_repo: pathlib.Path,
) -> None:
    # Its republish is gated on something having been fixed or failed,
    # so n=0 needs no carve-out to stay a no-op -- it falls out.
    empty_the_repository(settled_repo)
    globals_before = snapshot_globals(settled_repo)

    cli_manage(["repo-fix-histograms", "-R", str(settled_repo)])

    assert snapshot_globals(settled_repo) == globals_before


@pytest.mark.parametrize("argv", [
    # Every repository-scoped command that offers `--dry-run`.  Their
    # dry-run early returns are separate pieces of code -- repo-manifest
    # returns from a different one than the other three -- so pinning
    # one would not pin the others.
    ["repo-manifest"],
    ["repo-stats", "-j", "1"],
    ["repo-info", "-j", "1"],
    ["repo-repair", "-j", "1"],
], ids=lambda argv: str(argv[0]))
def test_a_dry_run_on_an_emptied_repository_writes_nothing(
    settled_repo: pathlib.Path,
    argv: list[str],
) -> None:
    empty_the_repository(settled_repo)
    globals_before = snapshot_globals(settled_repo)

    cli_manage([*argv, "-R", str(settled_repo), "-n"])

    assert snapshot_globals(settled_repo) == globals_before


def test_a_resource_command_matching_nothing_still_fails(
    settled_repo: pathlib.Path,
) -> None:
    # The sibling of the branch this issue emptied out, on the very
    # repository that made it empty: a repo-scoped command over zero
    # resources is a run, but a resource-scoped command that selected
    # nothing is still a usage error, and "n=0 is just a run" must not
    # leak across the branch.
    empty_the_repository(settled_repo)

    with pytest.raises(SystemExit) as exit_info:
        cli_manage([
            "resource-repair", "-R", str(settled_repo),
            "-r", "sub/one", "-j", "1"])

    assert exit_info.value.code == 1
