# pylint: disable=W0621,C0114,C0115,C0116,W0212,W0613
"""Tests for the rendered GRR browse page (grr_index.jinja).

These tests assert the browse page expresses the search-database
(.CONTENTS.sqlite3.gz) fetch as a page-relative URL in the *template
source* — a leading-dot ref resolved against document.baseURI, never a
root-absolute ref or window.location.origin. That is what keeps the
in-page search working when the GRR is served under a sub-path (e.g.
https://gpf.sfari.org/grr/) rather than at an origin root.

These tests do not execute the JS URL resolution; behavioral sub-path
resolution is covered by web_e2e, not here.  See iossifovlab/gain#129.
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
    """The sqlite db ref in the template source is page-relative.

    The template source must express the fetch as a leading-dot relative
    URL resolved against document.baseURI, which keeps the fetch under
    the page's sub-path (…/grr/.CONTENTS.sqlite3.gz). A root-absolute ref
    (leading slash) or window.location.origin would jump to the
    scheme+host root and rebreak sub-path serving — so both are asserted
    absent. This checks the rendered source only, not JS URL resolution.
    """
    rendered = _render_browse_page()

    # Correct: relative, leading-dot ref resolved against the page URL.
    assert "new URL(`.CONTENTS.sqlite3.gz" in rendered
    assert "document.baseURI" in rendered

    # Broken: a root-absolute ref re-introduces the origin-root jump.
    assert "new URL(`/" not in rendered
    assert "`/.CONTENTS.sqlite3.gz" not in rendered

    # Broken: explicitly anchoring to the origin root.
    assert "window.location.origin" not in rendered


def test_sqlite_fetch_carries_the_cache_busting_hash() -> None:
    """The rendered fetch still includes the sqlite3 hash query param."""
    rendered = _render_browse_page()

    assert ".CONTENTS.sqlite3.gz?v=deadbeef" in rendered
