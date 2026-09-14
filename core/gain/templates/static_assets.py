"""Static files a repository's generated pages need, shipped with gain.

The GRR index page's search runs on sqlite-wasm, and every generated
page sets its text in Roboto and draws its icons from a Material
Symbols subset.  Rather than loading any of that from a CDN or a font
host at view time, gain vendors the files under
``gain/templates/static/`` and publishes them into every repository it
indexes, beside ``index.html``, at paths the pages reach by relative URL
(gain#1335 for the engine, gain#1400 for the fonts).  A repository then
carries everything its pages need -- no third party at view time, a
working search and real icons on an intranet or behind an air gap, and
the bytes that run are the bytes the generating gain was tested with.

Two ways of versioning what is published, for two kinds of coupling.
The sqlite-wasm version is in the published *directory name*, not a
query string: the module locates its ``sqlite3.wasm`` through
``import.meta.url``, so a ``?v=`` on the import would never reach the
wasm, and a gain upgrade must not run a browser's cached module against
a new wasm.  ``version.txt`` beside the vendored files is the single
source of truth -- the directory name and the page's import are both
derived from it here, so a bump is one edit.  The fonts have no such
sibling to keep in step, so each is named by a digest of its own bytes
(:func:`_published_font_path`), with nothing to bump.

The directory is dot-prefixed on purpose.  A resource id can never
start with a dot, so nothing under ``.static/`` can collide with one,
and the repository scanner passes over every dot-prefixed entry.

:func:`repository_static_files` is the registry the publisher iterates:
it names no asset, so the next thing the pages should load from the
repository rather than a CDN is added here and reaches every repository
without the publisher changing.
"""
from __future__ import annotations

import hashlib
from importlib.resources import files
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

#: Where the vendored files live in the package.
_SQLITE_WASM_DIR = files("gain.templates") / "static" / "sqlite-wasm"

#: The two files the browser needs, and the only two: the module, and
#: the wasm it locates beside itself through ``import.meta.url``.
_SQLITE_WASM_FILES = ("index.mjs", "sqlite3.wasm")

#: The ``@sqlite.org/sqlite-wasm`` npm version the vendored files came
#: from.  Read once at import; the file is one line.
SQLITE_WASM_VERSION: str = (_SQLITE_WASM_DIR / "version.txt").read_text(
    encoding="utf8").strip()

#: The directory sqlite-wasm is published to, relative to the repository
#: root.  Forward slashes, no leading ``./``: it is a URL path for the
#: page as much as a repository path for the publisher, and both join
#: it themselves.
SQLITE_WASM_PATH: str = f".static/sqlite-wasm-{SQLITE_WASM_VERSION}"

#: Where the vendored fonts live in the package (gain#1400).
_FONTS_DIR = files("gain.templates") / "static" / "fonts"

#: The directory the fonts are published to, relative to the repository
#: root.  Unversioned, unlike sqlite-wasm's: each font file is versioned
#: by name instead (below).
FONTS_PATH: str = ".static/fonts"


def _published_font_path(name: str) -> str:
    """Where a vendored font is published: its name, content-stamped.

    A font's url is only ever written by the page beside it, which is
    republished on every run -- so unlike sqlite-wasm, where the module
    locates the wasm through ``import.meta.url``, nothing but the page
    binds to the name and there is no version to keep in step by hand.
    What the name still has to do is change whenever the bytes do: a
    browser holding yesterday's icon subset would render a glyph added
    today as its name, in words.  A digest of the bytes does that with
    no ``version.txt`` to forget; the vendored README records where each
    file came from.
    """
    digest = hashlib.sha256((_FONTS_DIR / name).read_bytes()).hexdigest()
    stem, suffix = name.rsplit(".", 1)
    return f"{FONTS_PATH}/{stem}.{digest[:8]}.{suffix}"


#: The page's typeface: Roboto's latin subset, variable across weights.
ROBOTO_FONT_PATH: str = _published_font_path("roboto-v51-latin.woff2")

#: The icon font: Material Symbols Outlined, subsetted to the glyphs the
#: pages draw -- ``tests/small/templates/test_grr_page_icon_font.py`` is
#: the authority on which those are.
MATERIAL_SYMBOLS_FONT_PATH: str = _published_font_path(
    "material-symbols-outlined-v371.woff2")


def repository_static_files() -> Iterator[tuple[str, bytes]]:
    """Each file to publish: its repository-relative path, and its bytes.

    The bytes are read on every call rather than cached: the publisher
    runs once per ``repo-info``, and 1.2 MB held for the life of every
    process that imports the templates is the wrong trade.
    """
    for name in _SQLITE_WASM_FILES:
        yield (
            f"{SQLITE_WASM_PATH}/{name}",
            (_SQLITE_WASM_DIR / name).read_bytes(),
        )
    for name, published in (
        ("roboto-v51-latin.woff2", ROBOTO_FONT_PATH),
        ("material-symbols-outlined-v371.woff2", MATERIAL_SYMBOLS_FONT_PATH),
    ):
        yield published, (_FONTS_DIR / name).read_bytes()
