# pylint: disable=W0621,C0114,C0115,C0116,W0212,W0613
"""The browse page's search engine ships inside the repository.

The index page's search runs on sqlite-wasm, which used to arrive at
view time from jsDelivr.  A GRR served on an intranet, behind an
air gap, or on a day the CDN is down then had a page with no search --
and the bytes that ran were whatever the CDN served, not the build the
generating gain was tested against (gain#1335).

The module now lives under the repository's own ``.static/`` directory,
published by the same command that publishes the page, and the page
imports it by a *relative* specifier.  Relative, because an inline
module script resolves its specifiers against the document's base URL:
that keeps the import pointing at the right repository whichever host
and sub-path the GRR is served under (gain#129), exactly as the search
index's own ``fetch()`` already does.

These assert against the rendered template source; CI runs gain-core in
python:3.12-slim, which has no JS runtime.
"""
from __future__ import annotations

import re
from collections.abc import Iterator

import gain.templates as templates_module
import pytest
from gain.templates import get_template
from gain.templates.static_assets import SQLITE_WASM_VERSION

#: ``import sqlite3 from "<specifier>"`` -- the one static import the
#: page's module script carries.
_MODULE_IMPORT = re.compile(r"\bimport\s+\w+\s+from\s+[\"']([^\"']+)[\"']")


@pytest.fixture(autouse=True)
def reset_template_caches() -> Iterator[None]:
    """Reset singleton caches before and after each test."""
    templates_module._state.env = None
    templates_module._state.provider_cache = None
    yield
    templates_module._state.env = None
    templates_module._state.provider_cache = None


@pytest.fixture
def browse_page() -> str:
    """The browse page as ``build_index_info`` renders it, no rows.

    The import sits in the script the template includes, not in any
    row, so an empty repository renders it all the same.
    """
    return get_template("grr_index.jinja").render(
        data={},
        has_about=False,
        sqlite3_hash="deadbeef",
    )


def test_the_browse_page_imports_sqlite_wasm_from_inside_the_repository(
    browse_page: str,
) -> None:
    """One import, relative, naming the vendored version's directory.

    The version is in the *directory name* rather than a query string:
    the module locates its ``sqlite3.wasm`` through ``import.meta.url``,
    so a ``?v=`` on the import would not reach the wasm, and a gain
    upgrade must never run a cached module against a new wasm.
    """
    assert _MODULE_IMPORT.findall(browse_page) == [
        f"./.static/sqlite-wasm-{SQLITE_WASM_VERSION}/index.mjs",
    ]
