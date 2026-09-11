# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""The repository carries the search engine its index page runs on.

``grr_manage repo-index`` and ``repo-info`` publish gain's vendored
sqlite-wasm into the repository, under ``.static/sqlite-wasm-<version>/``
beside ``index.html``, and the page imports it from there (gain#1335).
These pin the two halves of that contract against each other through
the CLI: what the page asks for is what the same run put on disk.
"""
import os
import pathlib
import re
import shutil

from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.testing import build_filesystem_test_protocol
from gain.templates.static_assets import (
    SQLITE_WASM_VERSION,
    sqlite_wasm_files,
)

from .conftest import read_published_contents

#: ``import sqlite3 from "<specifier>"`` in the published page.
_MODULE_IMPORT = re.compile(r"\bimport\s+\w+\s+from\s+[\"']([^\"']+)[\"']")


def published_dir(repo: pathlib.Path) -> pathlib.Path:
    return repo / ".static" / f"sqlite-wasm-{SQLITE_WASM_VERSION}"


def forget_static_assets(repo: pathlib.Path) -> None:
    """Drop whatever a previous publish left under ``.static/``.

    ``settled_repo`` is copied from a fully repaired template, so the
    assets are already there; a test about publishing them has to start
    from a repository that has none.
    """
    shutil.rmtree(repo / ".static", ignore_errors=True)
    assert not (repo / ".static").exists()


def test_repo_index_publishes_sqlite_wasm_beside_the_page(
    settled_repo: pathlib.Path,
) -> None:
    forget_static_assets(settled_repo)

    cli_manage(["repo-index", "-R", str(settled_repo)])

    # Files only: the publish seam stages through a `.grr/` directory
    # beside its target, as it does beside every page and manifest.
    published = {
        path.name: path.read_bytes()
        for path in published_dir(settled_repo).iterdir()
        if path.is_file()
    }
    vendored = {
        pathlib.PurePosixPath(path).name: content
        for path, content in sqlite_wasm_files()
    }
    assert set(published) == {"index.mjs", "sqlite3.wasm"}
    assert published == vendored


def test_the_page_imports_the_module_the_same_run_published(
    settled_repo: pathlib.Path,
) -> None:
    """The import and the publish derive from one version, provably.

    Read from the published page rather than the template: this is the
    one place both ends of the contract are on the same disk, so a
    version bump that changed the directory name but not the import --
    or the reverse -- fails here rather than in a browser.
    """
    forget_static_assets(settled_repo)

    cli_manage(["repo-index", "-R", str(settled_repo)])

    page = (settled_repo / "index.html").read_text(encoding="utf8")
    [specifier] = _MODULE_IMPORT.findall(page)
    assert specifier.startswith("./")
    assert (settled_repo / specifier[2:]).is_file()


def test_republishing_an_unchanged_repository_leaves_the_files_alone(
    settled_repo: pathlib.Path,
) -> None:
    """Identical bytes are not rewritten, so their mtimes hold.

    The mirrors and the git repositories the published GRRs live in
    both watch mtimes; a publisher that rewrote 1.2 MB of identical
    bytes on every run would make every run look like a change.
    """
    forget_static_assets(settled_repo)
    cli_manage(["repo-index", "-R", str(settled_repo)])
    files = [p for p in published_dir(settled_repo).iterdir() if p.is_file()]
    assert files
    pinned = 1_000_000_000
    for path in files:
        os.utime(path, (pinned, pinned))

    cli_manage(["repo-index", "-R", str(settled_repo)])

    rewritten = [
        path.name for path in files if int(path.stat().st_mtime) != pinned
    ]
    assert rewritten == []


def test_a_published_file_that_differs_is_replaced(
    settled_repo: pathlib.Path,
) -> None:
    """Skipping is for identical bytes only.

    The counterpart of the mtime test above, and the one that keeps its
    skip honest: a publisher that skipped on *existence* would pass that
    test and leave a truncated or stale module in place for good.
    """
    forget_static_assets(settled_repo)
    cli_manage(["repo-index", "-R", str(settled_repo)])
    module = published_dir(settled_repo) / "index.mjs"
    module.write_bytes(b"not the module")

    cli_manage(["repo-index", "-R", str(settled_repo)])

    vendored = dict(sqlite_wasm_files())
    assert module.read_bytes() == vendored[
        f".static/sqlite-wasm-{SQLITE_WASM_VERSION}/index.mjs"]


def test_the_static_directory_is_not_a_resource(
    settled_repo: pathlib.Path,
) -> None:
    """Published beside the resources, listed with none of them.

    A contract pin rather than a mechanism test: a directory with no
    ``genomic_resource.yaml`` is no resource whether or not the walk
    descends into it, so this holds even with the dot-prefix skip
    removed (checked by mutation).  What the dot prefix buys is not
    tested here because it is not observable here -- a resource id can
    never start with a dot, so ``.static`` cannot collide with one.
    """
    cli_manage(["repo-index", "-R", str(settled_repo)])
    assert published_dir(settled_repo).is_dir()

    proto = build_filesystem_test_protocol(settled_repo)
    resource_ids = {res.resource_id for res in proto.get_all_resources()}
    assert resource_ids == {"sub/one", "sub/two"}
    assert ".static" not in read_published_contents(settled_repo)


def test_repo_info_publishes_sqlite_wasm_too(
    settled_repo: pathlib.Path,
) -> None:
    """Both commands that publish the page publish what it imports.

    ``repo-info`` and ``repo-index`` share the page's publisher, so this
    is the pin that keeps it that way: a page published by either with
    a dangling import is a page with no search.
    """
    forget_static_assets(settled_repo)

    cli_manage(["repo-info", "-R", str(settled_repo), "-j", "1"])

    published = {
        path.name for path in published_dir(settled_repo).iterdir()
        if path.is_file()
    }
    assert published == {"index.mjs", "sqlite3.wasm"}
