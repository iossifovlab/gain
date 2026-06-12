# pylint: disable=W0621,C0114,C0115,C0116,W0212,W0613
"""Tests for the rendered GRR browse page (grr_index.jinja).

The browse page must fetch its search database (.CONTENTS.sqlite3.gz)
relative to the current page so the in-page search works when the GRR is
served under a sub-path (e.g. https://gpf.sfari.org/grr/), not only at an
origin root.  See iossifovlab/gain#129.
"""
from __future__ import annotations

from collections.abc import Iterator

import gain.templates as templates_module
import pytest
from gain.templates import get_template


@pytest.fixture(autouse=True)
def reset_template_caches() -> Iterator[None]:
    """Reset singleton caches before and after each test."""
    templates_module._state.env = None
    templates_module._state.provider_cache = None
    yield
    templates_module._state.env = None
    templates_module._state.provider_cache = None


def _render_browse_page() -> str:
    return get_template("grr_index.jinja").render(
        data={},
        has_about=False,
        sqlite3_hash="deadbeef",
    )


def test_sqlite_fetch_is_resolved_relative_to_the_page() -> None:
    """The sqlite db is fetched relative to the page, not the origin root.

    Resolving against document.baseURI keeps the fetch under the page's
    sub-path (…/grr/.CONTENTS.sqlite3.gz) instead of jumping to the
    scheme+host root, which is what window.location.origin would do.
    """
    rendered = _render_browse_page()

    assert "document.baseURI" in rendered
    assert "new URL(" in rendered
    assert "window.location.origin" not in rendered


def test_sqlite_fetch_carries_the_cache_busting_hash() -> None:
    """The rendered fetch still includes the sqlite3 hash query param."""
    rendered = _render_browse_page()

    assert ".CONTENTS.sqlite3.gz?v=deadbeef" in rendered
