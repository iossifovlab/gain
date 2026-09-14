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

from tests.small.templates.page_css import font_faces_in

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


def published_paths(repo: pathlib.Path) -> list[pathlib.Path]:
    """Every file published under ``.static/``.

    Files only, and none from a ``.grr/`` directory: the publish seam
    stages through one beside its target, as it does beside every page
    and manifest.
    """
    return [
        path for path in (repo / ".static").rglob("*")
        if path.is_file() and ".grr" not in path.parts
    ]


def published_files(repo: pathlib.Path) -> dict[str, bytes]:
    """Name -> bytes of everything published under ``.static/``."""
    return {path.name: path.read_bytes() for path in published_paths(repo)}


def vendored_files() -> dict[str, bytes]:
    """Name -> bytes of what gain ships, the expected value throughout."""
    return {
        pathlib.PurePosixPath(path).name: content
        for path, content in repository_static_files()
    }


def font_faces_the_page_loads(page: pathlib.Path) -> dict[str, pathlib.Path]:
    """Family -> where its ``@font-face`` resolves on disk.

    Resolved against the page's own directory, as the browser resolves
    a relative ``url()``: a page published below the repository root
    has to climb back to ``.static/`` itself.
    """
    return {
        family: (page.parent / url).resolve()
        for family, url in font_faces_in(
            page.read_text(encoding="utf8")).items()
    }


#: The typeface every page sets, and the icon font only the pages that
#: draw a glyph carry: the browse page's sort indicators, row icons and
#: copy buttons, and the resource pages' table sorter.
TEXT_FONT = "Roboto"
ICON_FONT = "Material Symbols Outlined"


@pytest.mark.parametrize(("page", "families"), [
    ("index.html", {TEXT_FONT, ICON_FONT}),
    # Two directories down: the resource page has to climb to the root.
    ("sub/one/index.html", {TEXT_FONT, ICON_FONT}),
    # Three: the statistics page sits under the resource, and sorts no
    # table, so it carries no icon font.
    ("sub/one/statistics/index.html", {TEXT_FONT}),
])
def test_a_page_loads_its_fonts_from_files_the_same_run_published(
    bare_repo: pathlib.Path, page: str, families: set[str],
) -> None:
    """Every font a page declares is a file the publisher put beside it.

    The pages name no font host: their typeface and icon glyphs are
    ``@font-face`` blocks whose ``url()`` climbs into ``.static/``, so a
    repository behind an air gap renders them (gain#1400).  Pinned on
    the published page rather than the template because this is the
    one place both ends -- the url the page asks for and the file the
    run wrote -- are on the same disk.
    """
    cli_manage(["repo-index", "-R", str(bare_repo)])

    faces = font_faces_the_page_loads(bare_repo / page)

    assert set(faces) == families
    assert_published_by_this_gain(faces)


def assert_published_by_this_gain(faces: dict[str, pathlib.Path]) -> None:
    """Each face resolves to a file holding gain's vendored bytes."""
    vendored = vendored_files()
    for family, path in faces.items():
        assert path.is_file(), (family, path)
        assert path.read_bytes() == vendored[path.name], (family, path)


def test_the_about_page_loads_its_typeface_from_the_repository(
    bare_repo: pathlib.Path,
) -> None:
    """Styled text, so the typeface and nothing else.

    The page is only published when the repository carries an
    ``about.md``; the settled fixture has none, so this writes one.
    """
    (bare_repo / "about.md").write_text("# About\n", encoding="utf8")
    cli_manage(["repo-index", "-R", str(bare_repo)])

    faces = font_faces_the_page_loads(bare_repo / "about.html")

    assert set(faces) == {TEXT_FONT}
    assert_published_by_this_gain(faces)


@pytest.mark.parametrize("command", [
    ["repo-index"],
    ["repo-info", "-j", "1"],
])
def test_the_page_publishers_publish_everything_gain_vendors_beside_it(
    bare_repo: pathlib.Path, command: list[str],
) -> None:
    """Both commands that publish the page publish what it loads.

    A page published by either with a dangling import is a page with
    no search, and one with a dangling font url is a page of glyph
    names in words -- so the pin covers both commands rather than
    trusting that they share a publisher, and everything the registry
    names rather than the search engine alone.
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
    files = published_paths(bare_repo)
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
