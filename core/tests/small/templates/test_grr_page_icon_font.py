# pylint: disable=W0621,C0114,C0115,C0116,W0212,W0613
"""What the GRR browse and about pages pay for their icon font.

The unsubsetted Material Symbols family is a ~3.8 MB woff2 -- one
``@font-face`` with no ``unicode-range`` split, so a browser fetches the
whole variable font to draw a handful of glyphs.  Subsetting the request
with ``icon_names`` brings the seven the browse page draws down to
~5.9 kB.

Material Symbols renders by *ligature*: the element's text is the glyph
name, and the font substitutes it.  A glyph missing from the subset has
no ligature to substitute, so it renders as its own name -- the copy
button would show the word "check" instead of a tick.  That failure is
invisible to any assertion phrased against the URL alone, which is why
this module scans for the glyphs the page can actually put on screen and
compares that set against what the request asks for.

Glyph names reach the page in two shapes, and the scan reads both:

- as markup, either rendered server-side by the template or built as a
  string and assigned through ``innerHTML`` by the page's script, and
- as a bare string assigned to an element's ``textContent``.

Only two of the seven are visible in ``grr_index.jinja`` itself; the
rest live in the script it includes, which is what makes the set easy to
under-count.  The issue that prompted this module counted five.

``EXPECTED_GLYPHS`` is written out by hand on purpose.  Asserting the
scan *equals* it, rather than merely that the request covers the scan,
is what keeps the scan honest: a scan that stops matching -- because a
literal moved or an assignment was rephrased -- collapses towards the
empty set, and a containment assertion would pass on that.  Equality
makes it fail.  An eighth glyph fails it too, which is the point:
adding one should be a deliberate edit to the subset, not a silent
3.8 MB download.

These assert against the rendered template source; CI runs gain-core in
python:3.12-slim, which has no JS runtime.
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urlparse

import gain.templates as templates_module
import pytest
from gain.templates import get_template

#: The class the icon font styles.  An element carrying it renders its
#: own text as a glyph.
ICON_CLASS = "material-symbols-outlined"

#: Every glyph the browse page can put on screen:
#:
#: - ``unfold_more`` / ``content_copy`` -- rendered server-side into the
#:   column headers and each row.
#: - ``arrow_upward`` / ``arrow_downward`` -- swapped into the header
#:   indicator when a column is sorted.
#: - ``check`` -- the tick the clipboard handler shows on a successful
#:   copy, reverted after a timeout.
#: - ``folder`` / ``description`` -- the hierarchical view's row icons,
#:   built as markup strings and assigned through ``innerHTML``.
EXPECTED_GLYPHS = frozenset({
    "arrow_downward",
    "arrow_upward",
    "check",
    "content_copy",
    "description",
    "folder",
    "unfold_more",
})

#: Every origin the browse page *loads* from: jQuery from Google's CDN,
#: the sqlite-wasm module from jsDelivr, the stylesheets from Google
#: Fonts, and the host serving the font files those point at.  Ordinary
#: hyperlinks are deliberately not counted -- the page links to the
#: SQLite FTS docs beside its search box, and a page that grows another
#: such link has not grown a third party it loads code from.
BROWSE_ORIGINS = frozenset({
    "ajax.googleapis.com",
    "cdn.jsdelivr.net",
    "fonts.googleapis.com",
    "fonts.gstatic.com",
})

#: The about page is styled text with no script at all.
ABOUT_ORIGINS = frozenset({"fonts.googleapis.com"})

#: A Material Symbols glyph name: lowercase, underscore-separated.  The
#: shape is what separates a glyph from the script's other string
#: literals -- ``"/"`` and ``"No resources"`` are also assigned to
#: ``textContent``, and neither is a glyph.
_GLYPH = r"([a-z][a-z0-9_]*)"

#: An icon element written as markup: ``<span class="material-symbols-
#: outlined hv-icon">folder</span>``.  This matches the template's own
#: elements and the ones the script builds as strings alike -- the
#: latter being invisible to an HTML parser, which treats a ``<script>``
#: body as opaque character data.  Newlines and angle brackets are
#: excluded so a match cannot run across statements into an unrelated
#: quote.
_MARKUP_GLYPH = re.compile(
    ICON_CLASS + r"[^\"'<>\n]*[\"'][^<>\n]*>\s*" + _GLYPH + r"\s*<",
)

#: ``el.textContent = 'content_copy'`` -- the copy button and its tick.
_ASSIGNED_GLYPH = re.compile(r"\.textContent\s*=\s*['\"]" + _GLYPH + r"['\"]")

#: ``{ none: 'unfold_more', asc: 'arrow_upward', ... }`` -- the sort
#: indicator's three states.
_SORT_STATE_GLYPH = re.compile(
    r"\b(?:none|asc|desc)\s*:\s*['\"]" + _GLYPH + r"['\"]",
)

#: ``import sqlite3 from "https://cdn.jsdelivr.net/…"`` -- an ES module
#: specifier, which reaches an origin without being an ``src``.
_MODULE_IMPORT = re.compile(r"\bfrom\s+[\"'](https?://[^\"']+)[\"']")

#: Tags whose ``href`` makes the browser fetch something.  ``<a>`` is
#: pointedly absent; ``src`` is a subresource on whatever carries it.
_FETCHING_HREF_TAGS = frozenset({"link"})


@pytest.fixture(autouse=True)
def reset_template_caches() -> Iterator[None]:
    """Reset singleton caches before and after each test."""
    templates_module._state.env = None
    templates_module._state.provider_cache = None
    yield
    templates_module._state.env = None
    templates_module._state.provider_cache = None


class _LinkReader(HTMLParser):
    """Collects the URLs a page fetches, and its preconnect hints."""

    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []
        #: host -> whether the hint warms a CORS socket.
        self.preconnects: dict[str, bool] = {}

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)

        href = attributes.get("href")
        if href and tag in _FETCHING_HREF_TAGS:
            self.urls.append(href)
        src = attributes.get("src")
        if src:
            self.urls.append(src)

        if tag == "link" and attributes.get("rel") == "preconnect" and href:
            self.preconnects[urlparse(href).hostname or ""] = (
                "crossorigin" in attributes
            )


def read_page(page: str) -> _LinkReader:
    reader = _LinkReader()
    reader.feed(page)
    return reader


def glyphs_the_page_can_draw(page: str) -> frozenset[str]:
    """Every glyph name the page renders now or can swap in later."""
    return frozenset(
        set(_MARKUP_GLYPH.findall(page))
        | set(_ASSIGNED_GLYPH.findall(page))
        | set(_SORT_STATE_GLYPH.findall(page)),
    )


def icon_font_requests(page: str) -> list[str]:
    """Every Material Symbols stylesheet the page asks for."""
    return [url for url in read_page(page).urls if "Material+Symbols" in url]


def subsetted_glyphs(url: str) -> frozenset[str]:
    """The glyphs an icon font request is subsetted to."""
    names = parse_qs(urlparse(url).query).get("icon_names", [])
    if not names:
        return frozenset()
    return frozenset(names[0].split(","))


def external_origins(page: str) -> frozenset[str]:
    """Every third-party host the page loads from.

    Attribute URLs are not the whole story: the search database's
    sqlite-wasm arrives through a bare ES module specifier, which is a
    string inside a ``<script type="module">`` rather than an ``src``.
    Counting only markup would leave a CDN out of a set this module
    claims is exhaustive.
    """
    fetched = [
        url for url in read_page(page).urls
        if urlparse(url).scheme in ("http", "https")
    ]
    return frozenset(
        urlparse(url).hostname or ""
        for url in fetched + _MODULE_IMPORT.findall(page)
    )


@pytest.fixture
def browse_page() -> str:
    """The browse page rendered around a single resource row.

    A row is required, not incidental: the copy button is rendered per
    row, so an empty ``data`` mapping renders no ``content_copy`` at all
    and a glyph assertion over the page would pass vacuously.  The shape
    mirrors what ``build_index_info`` builds, including the resource
    config spread into the row.
    """
    row: dict[str, Any] = {
        "res_full_id": "cadd",
        "res_id": "cadd",
        "type": "position_score",
        "table": {"filename": "x.tsv"},
        "res_version": "1.0",
        "res_files": 3,
        "res_size": "12 MB",
        "res_summary": "CADD scores",
    }
    return get_template("grr_index.jinja").render(
        data={"cadd": row},
        has_about=False,
        sqlite3_hash="deadbeef",
    )


@pytest.fixture
def about_page() -> str:
    """The about page rendered around already-converted markdown.

    ``build_index_info`` converts ``about.md`` with markdown2 and passes
    the HTML in; the page is only emitted when that file exists.
    """
    return get_template("grr_about.jinja").render(
        about_contents="<h1>About this GRR</h1><p>Text.</p>",
    )


def test_the_icon_font_is_subsetted_to_the_glyphs_the_page_draws(
    browse_page: str,
) -> None:
    """One request, subsetted to exactly the glyphs the page can draw."""
    requests = icon_font_requests(browse_page)

    assert len(requests) == 1, requests
    assert glyphs_the_page_can_draw(browse_page) == EXPECTED_GLYPHS
    assert subsetted_glyphs(requests[0]) == EXPECTED_GLYPHS


def test_the_browse_page_preconnects_to_the_host_serving_the_font(
    browse_page: str,
) -> None:
    """Both font hosts, and only gstatic warms a CORS socket.

    Google Fonts splits the work across two origins: the stylesheet
    comes from ``fonts.googleapis.com``, but the woff2 it points at is
    served from ``fonts.gstatic.com``.  Fonts are fetched in CORS mode,
    so the second hint needs ``crossorigin`` -- without it the browser
    warms a socket the font request will not reuse, and the hint costs a
    handshake while saving nothing.  The stylesheet is not a CORS fetch,
    so the first hint correctly does not carry it.
    """
    assert read_page(browse_page).preconnects == {
        "fonts.googleapis.com": False,
        "fonts.gstatic.com": True,
    }


def test_the_browse_page_loads_from_no_new_third_party(
    browse_page: str,
) -> None:
    """Subsetting changes the request, not who serves it.

    ``fonts.gstatic.com`` is named here because the page now declares
    it, not because it is new traffic: the font file was always fetched
    from there by the stylesheet.  What the assertion is really for is
    the next edit -- swapping a font or an icon set for one on some
    other CDN should have to change this list on purpose.
    """
    assert external_origins(browse_page) == BROWSE_ORIGINS


def test_the_about_page_loads_from_no_new_third_party(
    about_page: str,
) -> None:
    """Dropping the icon font removes no origin and adds none."""
    assert external_origins(about_page) == ABOUT_ORIGINS


def test_the_about_page_still_asks_for_its_text_font(
    about_page: str,
) -> None:
    """Roboto survives.

    The about page's icon font rode in on the *same* stylesheet URL as
    Roboto, so dropping the icon half means editing that URL rather than
    deleting the link.  This pins the half that has to stay: the about
    page is styled text, and losing its typeface would be a regression
    hiding inside the fix.
    """
    roboto = [
        url for url in read_page(about_page).urls if "family=Roboto" in url
    ]

    assert len(roboto) == 1, roboto
    assert "wght@400;500;800" in roboto[0]


def test_the_about_page_asks_for_no_icon_font(about_page: str) -> None:
    """The about page draws no glyph, so it fetches no icon font.

    It renders no element carrying the icon class and includes no script
    that could set one, so the whole ~3.8 MB family was a download that
    could never show the reader anything.
    """
    assert glyphs_the_page_can_draw(about_page) == frozenset()
    assert icon_font_requests(about_page) == []
