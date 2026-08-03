# pylint: disable=W0621,C0114,C0115,C0116,W0212,W0613
"""Escaping tests for the GRR browse page rows (grr_index.jinja).

Every value in a browse-page row comes out of the GRR: the resource id,
the resource config (spread into the row wholesale, so ``type`` and its
siblings are resource-supplied), the version and size strings and
``meta.summary``.  None of it is written by gain, so markup in any of it
must reach the page as text rather than as DOM.

The probe token is a made-up tag name.  The page legitimately ships
``<script>`` and ``<style>`` elements of its own, so an assertion phrased
against those matches the page's own markup and passes vacuously.

These assert against the rendered template source; CI runs gain-core in
python:3.12-slim, which has no JS runtime.
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import gain.templates as templates_module
import pytest
from gain.templates import get_template

PROBE = "gainxss558"

CLEAN_ROW = {
    "res_id": "cadd",
    "res_type": "position_score",
    "res_version": "1.0",
    "res_size": "12 MB",
    "res_summary": "CADD scores",
}


@pytest.fixture(autouse=True)
def reset_template_caches() -> Iterator[None]:
    """Reset singleton caches before and after each test."""
    templates_module._state.env = None
    templates_module._state.provider_cache = None
    yield
    templates_module._state.env = None
    templates_module._state.provider_cache = None


def _render_browse_page(
    *,
    res_id: str,
    res_type: str,
    res_version: str,
    res_size: str,
    res_summary: str,
) -> str:
    """Render the browse page around a single resource row.

    The row mirrors the shape ``build_index_info`` builds, including the
    resource config spread into it -- which is how ``type`` ends up
    resource-supplied.
    """
    config: dict[str, Any] = {"type": res_type, "table": {"filename": "x.tsv"}}
    row = {
        "res_full_id": res_id,
        "res_id": res_id,
        **config,
        "res_version": res_version,
        "res_files": 3,
        "res_size": res_size,
        "res_summary": res_summary,
    }
    return get_template("grr_index.jinja").render(
        data={res_id: row},
        has_about=False,
        sqlite3_hash="deadbeef",
    )


@pytest.mark.parametrize("field", sorted(CLEAN_ROW))
def test_browse_row_renders_resource_markup_as_text(field: str) -> None:
    """Markup in any row value renders as text, not as an element."""
    probe = f"<{PROBE}{field.replace('_', '')}>"

    page = _render_browse_page(**{**CLEAN_ROW, field: CLEAN_ROW[field] + probe})

    assert probe not in page
    assert probe.replace("<", "&lt;").replace(">", "&gt;") in page


def test_browse_row_id_cannot_break_out_of_an_attribute() -> None:
    """A double quote in a resource id does not terminate its attribute.

    The id is interpolated into the row's ``<tr id="...">`` and into the
    id cell's ``title="..."``.  A raw double quote closes either one and
    lands an event handler on the surrounding tag -- no injected element
    required at all.
    """
    breakout = f'cadd" {PROBE}handler="boom()'
    escaped = f"cadd&#34; {PROBE}handler=&#34;boom()"

    page = _render_browse_page(**{**CLEAN_ROW, "res_id": breakout})

    assert f'" {PROBE}handler' not in page
    assert f'<tr id="{escaped}">' in page
    assert f'title="{escaped}"' in page
