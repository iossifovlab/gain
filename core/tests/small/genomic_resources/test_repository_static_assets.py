# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""The repository carries the search engine its index page runs on.

``grr_manage repo-index`` and ``repo-info`` publish gain's vendored
sqlite-wasm into the repository, under ``.static/sqlite-wasm-<version>/``
beside ``index.html``, and the page imports it from there
(``gain.templates.static_assets``, gain#1335).  These pin the two halves
of that contract against each other through the CLI: what the page asks
for is what the same run put on disk.
"""
import os
import pathlib
import shutil

import pytest
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.testing import build_filesystem_test_protocol
from gain.templates.static_assets import (
    SQLITE_WASM_PATH,
    repository_static_files,
)

from .conftest import read_published_contents


@pytest.fixture
def bare_repo(settled_repo: pathlib.Path) -> pathlib.Path:
    """``settled_repo`` with nothing under ``.static/``.

    The settled template is fully repaired, assets included; a test
    about publishing them has to start from a repository that has none.
    """
    shutil.rmtree(settled_repo / ".static", ignore_errors=True)
    assert not (settled_repo / ".static").exists()
    return settled_repo


def published_files(repo: pathlib.Path) -> dict[str, bytes]:
    """Name -> bytes of what was published, files only.

    Files only: the publish seam stages through a ``.grr/`` directory
    beside its target, as it does beside every page and manifest.
    """
    return {
        path.name: path.read_bytes()
        for path in (repo / SQLITE_WASM_PATH).iterdir()
        if path.is_file()
    }


def vendored_files() -> dict[str, bytes]:
    """Name -> bytes of what gain ships, the expected value throughout."""
    return {
        pathlib.PurePosixPath(path).name: content
        for path, content in repository_static_files()
    }


@pytest.mark.parametrize("command", [
    ["repo-index"],
    ["repo-info", "-j", "1"],
])
def test_the_page_publishers_publish_sqlite_wasm_beside_it(
    bare_repo: pathlib.Path, command: list[str],
) -> None:
    """Both commands that publish the page publish what it imports.

    A page published by either with a dangling import is a page with
    no search, so the pin covers both rather than trusting that they
    share a publisher.
    """
    cli_manage([*command, "-R", str(bare_repo)])

    assert published_files(bare_repo) == vendored_files()


def test_the_page_imports_the_module_the_same_run_published(
    bare_repo: pathlib.Path,
) -> None:
    """The import and the publish derive from one version, provably.

    Read from the published page rather than the template: this is the
    one place both ends of the contract are on the same disk, so a
    version bump that changed the directory name but not the import --
    or the reverse -- fails here rather than in a browser.
    """
    cli_manage(["repo-index", "-R", str(bare_repo)])

    page = (bare_repo / "index.html").read_text(encoding="utf8")
    specifier = f"./{SQLITE_WASM_PATH}/index.mjs"
    assert f'from "{specifier}"' in page
    assert (bare_repo / specifier).is_file()


def test_republishing_an_unchanged_repository_leaves_the_files_alone(
    bare_repo: pathlib.Path,
) -> None:
    """Identical bytes are not rewritten, so their mtimes hold.

    The mirrors and the git repositories the published GRRs live in
    both watch mtimes; a publisher that rewrote 1.2 MB of identical
    bytes on every run would make every run look like a change.
    """
    cli_manage(["repo-index", "-R", str(bare_repo)])
    files = [
        bare_repo / SQLITE_WASM_PATH / name
        for name in published_files(bare_repo)
    ]
    pinned = 1_000_000_000
    for path in files:
        os.utime(path, (pinned, pinned))

    cli_manage(["repo-index", "-R", str(bare_repo)])

    rewritten = [
        path.name for path in files if int(path.stat().st_mtime) != pinned
    ]
    assert rewritten == []


def test_a_published_file_that_differs_is_replaced(
    bare_repo: pathlib.Path,
) -> None:
    """Skipping is for identical bytes only.

    The counterpart of the mtime test, and the one that keeps its skip
    honest: a publisher that skipped on *existence* would pass that
    test and leave a truncated or stale module in place for good.
    """
    cli_manage(["repo-index", "-R", str(bare_repo)])
    module = bare_repo / SQLITE_WASM_PATH / "index.mjs"
    module.write_bytes(b"not the module")

    cli_manage(["repo-index", "-R", str(bare_repo)])

    assert module.read_bytes() == vendored_files()["index.mjs"]


def test_the_static_directory_is_not_a_resource(
    settled_repo: pathlib.Path,
) -> None:
    """Published beside the resources, listed with none of them.

    A contract pin rather than a mechanism test: a directory with no
    ``genomic_resource.yaml`` is no resource whether or not the walk
    descends into it, so this holds even with the dot-prefix skip
    removed (checked by mutation).  What the dot prefix buys is not
    observable here -- a resource id can never start with a dot, so
    ``.static`` cannot collide with one.
    """
    assert (settled_repo / SQLITE_WASM_PATH).is_dir()

    proto = build_filesystem_test_protocol(settled_repo)
    resource_ids = {res.resource_id for res in proto.get_all_resources()}
    assert resource_ids == {"sub/one", "sub/two"}
    assert ".static" not in read_published_contents(settled_repo)
