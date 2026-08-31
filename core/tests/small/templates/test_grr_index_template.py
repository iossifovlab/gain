# pylint: disable=W0621,C0114,C0115,C0116,W0212,W0613
"""Tests for the rendered GRR browse page (grr_index.jinja).

These tests assert properties of the page's client-side JavaScript by
reading the *rendered template source*:

- the search-database (.CONTENTS.sqlite3.gz) fetch is a page-relative
  URL — a leading-dot ref resolved against document.baseURI, never a
  root-absolute ref or window.location.origin. That is what keeps the
  in-page search working when the GRR is served under a sub-path (e.g.
  https://gpf.sfari.org/grr/) rather than at an origin root.
- the column rescaler ignores a zero-width measurement, so toggling to
  the hierarchical view does not collapse the table's columns.
- the tree view formats sizes and version labels the way the Python
  side does. Those tests also assert the Python contracts being
  mirrored (convert_size, version_tuple_to_string), so a change on the
  Python side fails here and flags the JS that copies it.

None of these execute the JavaScript: CI runs gain-core in
python:3.12-slim (core/Dockerfile), which has no JS runtime.  Behavioural
coverage lives in ``info_pages_e2e`` -- its own Playwright project, which
generates a GRR and drives the pages a browser actually loads.  That
suite covers the *resource* pages' sortable tables today; this page's
search, tree view and column rescaler are not driven there yet, so for
now the reading-the-source tests above are all that stands behind them.
"""
from __future__ import annotations

from collections.abc import Iterator

import gain.templates as templates_module
import pytest
from gain.genomic_resources.repository import version_tuple_to_string
from gain.templates import get_template
from gain.utils.helpers import convert_size


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


def _wrapper_observer_source(rendered: str) -> str:
    """Return the source of the #table-wrapper ResizeObserver callback."""
    start = rendered.index("const wrapperObserver = new ResizeObserver(")
    end = rendered.index("wrapperObserver.observe(wrapper)", start)
    return rendered[start:end]


def test_column_rescale_bails_out_when_the_table_has_no_width() -> None:
    """The column rescaler must ignore a zero-width measurement.

    Switching to the hierarchical view hides #table-wrapper, which is the
    element the rescaling ResizeObserver watches. The observer therefore
    fires with the table at clientWidth 0. Without a zero-width bail it
    computes scale = 0 and writes every <col> to "0px" — and because the
    colgroup then totals 0, the pre-existing `!colTotal` guard returns
    early forever, so the widths never recover on the way back to the
    table view. Guarding on the measured width is what keeps the toggle
    non-destructive.
    """
    observer = _wrapper_observer_source(_render_browse_page())

    assert "!newWidth" in observer


def _format_size_source(rendered: str) -> str:
    """Return the source of the tree view's formatSize() helper."""
    start = rendered.index("function formatSize(bytes)")
    return rendered[start:rendered.index("}", rendered.index("return", start))]


def test_tree_folder_sizes_are_formatted_like_convert_size() -> None:
    """Folder sizes must render in the same format as resource sizes.

    Folder rows are aggregated and formatted in JS; resource rows are
    formatted by gain.utils.helpers.convert_size and passed through
    verbatim. Both appear in the same list, so the two formats have to
    agree. convert_size rounds to two places and interpolates the
    resulting float, which always leaves at least one decimal digit
    ("2.0 GB", never "2 GB"); JS String() drops it. The JS mirror must
    restore it for integral values.

    The convert_size assertions below are the contract the JS copies —
    if they ever change, the JS needs changing with them.
    """
    assert convert_size(2 * 1024**3) == "2.0 GB"
    assert convert_size(500) == "500.0 B"
    # Non-integral magnitudes already agree, and zero is special-cased.
    assert convert_size(1536) == "1.5 KB"
    assert convert_size(0) == "0B"

    format_size = _format_size_source(_render_browse_page())

    assert "toFixed(1)" in format_size


def test_tree_treats_version_zero_as_unversioned() -> None:
    """An unversioned resource must not be labelled "v0" in the tree.

    The tree's metadata line reads the version out of the rendered table
    cell, which is GenomicResource.get_version_str(). That returns the
    string "0" for an unversioned resource — truthy in JS, so a bare
    truthiness check labels the majority of resources "v0". Version 0
    means *absent* by this codebase's own convention, which is why
    get_genomic_resource_id_version() omits it from the id.

    The version_tuple_to_string assertion below is the contract the JS
    depends on — if it changes, the JS needs changing with it.
    """
    assert version_tuple_to_string((0,)) == "0"

    rendered = _render_browse_page()

    assert 'd.version !== "0"' in rendered
