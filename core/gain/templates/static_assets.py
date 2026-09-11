"""Static files a repository's generated pages need, shipped with gain.

The GRR index page's search runs on sqlite-wasm.  Rather than loading it
from a CDN at view time, gain vendors the two files the browser needs
under ``gain/templates/static/sqlite-wasm/`` and publishes them into
every repository it indexes, beside ``index.html``, at a path the page
imports by relative URL (gain#1335).  A repository then carries
everything its search needs -- no third party at view time, a working
search on an intranet or behind an air gap, and the bytes that run are
the bytes the generating gain was tested with.

The version is in the published *directory name*, not a query string:
the module locates its ``sqlite3.wasm`` through ``import.meta.url``, so
a ``?v=`` on the import would never reach the wasm, and a gain upgrade
must not run a browser's cached module against a new wasm.
``version.txt`` beside the vendored files is the single source of
truth -- the directory name and the page's import are both derived from
it here, so a bump is one edit.

The directory is dot-prefixed on purpose.  A resource id can never
start with a dot, so nothing under ``.static/`` can collide with one,
and the repository scanner passes over every dot-prefixed entry.

:func:`repository_static_files` is the registry the publisher iterates:
it names no asset, so the next thing the page should load from the
repository rather than a CDN (a font, say) is added here and reaches
every repository without the publisher changing.
"""
from __future__ import annotations

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
