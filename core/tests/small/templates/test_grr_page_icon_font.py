# pylint: disable=W0621,C0114,C0115,C0116,W0613
"""What the GRR browse and about pages carry for their fonts.

Both faces are files of the repository itself since gain#1400: the
typeface and the icon font are ``@font-face`` blocks over files that
``repo-index`` publishes under ``.static/``, so the pages load nothing
from any third party.  The icon font is a subset: the unsubsetted
Material Symbols family is a ~3.8 MB woff2, against ~6 kB for the eight
glyphs the browse page draws.

Material Symbols renders by *ligature*: the element's text is the glyph
name, and the font substitutes it.  A glyph missing from the subset has
no ligature to substitute, so it renders as its own name -- the copy
button would show the word "check" instead of a tick.  That failure is
invisible to any assertion phrased against the page alone, which is why
this module scans for the glyphs the page can actually put on screen and
compares that set against what the vendored file's ligature table
carries (``tests.small.templates.vendored_fonts``).

Glyph names reach the page in two shapes, and the scan reads both:

- as markup, either rendered server-side by the template or built as a
  string and assigned through ``innerHTML`` by the page's script, and
- as a bare string assigned to an element's ``textContent``.

Only two of the eight are visible in ``grr_index.jinja`` itself; the
rest live in the script it includes, which is what makes the set easy to
under-count.  The issue that prompted this module counted five.

``EXPECTED_GLYPHS`` is written out by hand on purpose.  Asserting the
scan *equals* it, rather than merely that the file covers the scan,
is what keeps the scan honest: a scan that stops matching -- because a
literal moved or an assignment was rephrased -- collapses towards the
empty set, and a containment assertion would pass on that.  Equality
makes it fail.  A ninth glyph fails it too, which is the point: adding
one should be a deliberate edit to the page *and* a re-subset of the
vendored file (the recipe is in the README beside it), not words on
the page.

These assert against the rendered template source; CI runs gain-core in
python:3.12-slim, which has no JS runtime.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import pytest
from gain.templates import get_template
from gain.templates.static_assets import (
    MATERIAL_SYMBOLS_FONT_PATH,
    SQLITE_WASM_PATH,
    repository_static_files,
)

from tests.small.templates.page_css import font_faces_in, rules_in
from tests.small.templates.vendored_fonts import ligatures_in

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
#: - ``close`` -- the button that clears the search term (gain#1454).
EXPECTED_GLYPHS = frozenset({
    "arrow_downward",
    "arrow_upward",
    "check",
    "close",
    "content_copy",
    "description",
    "folder",
    "unfold_more",
})

#: Names the vendored subset answers to that no page draws: Google's
#: subsetter emits every ligature name a requested glyph has, and
#: ``close`` and ``clear`` are one glyph.  Listed so the equality
#: against the file can stay an equality.
ALIASES = frozenset({"clear"})


def vendored_icon_font() -> bytes:
    """The icon font as the publisher will write it into a repository."""
    return dict(repository_static_files())[MATERIAL_SYMBOLS_FONT_PATH]


#: Every origin the pages *load* from: none.  The search engine and both
#: fonts ship inside the repository (gain#1335, gain#1400), so a page
#: behind an air gap is the same page.  Ordinary hyperlinks are
#: deliberately not counted -- the browse page links to the SQLite FTS
#: docs beside its search box, and a page that grows another such link
#: has not grown a third party it loads code from.
NO_ORIGINS: frozenset[str] = frozenset()

#: The typeface every page sets, and the icon face only pages that draw
#: a glyph declare.
TEXT_FONT = "Roboto"
ICON_FONT = "Material Symbols Outlined"

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

#: ``import x from "<specifier>"`` -- an ES module specifier, which can
#: reach an origin without being an ``src``.  Any specifier, so the same
#: match serves both the relative import the page carries and the
#: origin count that must notice an absolute one.
_MODULE_IMPORT = re.compile(r"\bimport\s+\w+\s+from\s+[\"']([^\"']+)[\"']")

#: Tags whose ``href`` makes the browser fetch something.  ``<a>`` is
#: pointedly absent; ``src`` is a subresource on whatever carries it.
_FETCHING_HREF_TAGS = frozenset({"link"})


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


def external_origins(page: str) -> frozenset[str]:
    """Every third-party host the page loads from.

    Attribute URLs are not the whole story: the search database's
    sqlite-wasm arrives through a bare ES module specifier, which is a
    string inside a ``<script type="module">`` rather than an ``src``.
    Counting only markup would leave a module's origin out of a set this
    module claims is exhaustive.
    """
    fetched = [
        url for url in read_page(page).urls + _MODULE_IMPORT.findall(page)
        if urlparse(url).scheme in ("http", "https")
    ]
    return frozenset(urlparse(url).hostname or "" for url in fetched)


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
    """The vendored subset carries exactly the glyphs the page can draw.

    Read from the font file itself, not from a request URL: since
    gain#1400 the face is a file the repository publishes, so the only
    evidence that ``folder`` will render as a glyph and not as the word
    is the file's own ligature table.  Equality both ways: a glyph the
    page draws but the file lacks renders in words, and a glyph the
    file carries but no page draws is a subset that grew without anyone
    deciding it should.
    """
    assert glyphs_the_page_can_draw(browse_page) == EXPECTED_GLYPHS
    assert ligatures_in(vendored_icon_font()) - ALIASES == EXPECTED_GLYPHS


def test_the_browse_page_preconnects_nowhere(
    browse_page: str,
) -> None:
    """Nothing to warm: every socket the page opens is to its own origin.

    The hints went with the Google Fonts links they were for.  A hint
    left behind would be harmless in a browser and misleading in this
    file -- it would name a host the page no longer loads from.
    """
    assert not read_page(browse_page).preconnects


def test_the_browse_page_loads_from_no_third_party(
    browse_page: str,
) -> None:
    """No host at all, not merely no new one.

    What the assertion is really for is the next edit -- reaching for a
    CDN for a font, an icon set or a script should have to change this
    on purpose, and the change should read as what it is.
    """
    assert external_origins(browse_page) == NO_ORIGINS


def test_the_browse_page_declares_both_faces_from_the_repository(
    browse_page: str,
) -> None:
    """Its typeface and its icon font, each a relative url into ``.static/``.

    Relative for the reason the sqlite-wasm import below is; the
    directory is the one ``gain.templates.static_assets`` publishes to,
    and the CLI tests pin that the file is really there.
    """
    faces = font_faces_in(browse_page)

    assert set(faces) == {TEXT_FONT, ICON_FONT}
    assert all(url.startswith(".static/") for url in faces.values()), faces


def test_the_browse_page_turns_ligatures_on_for_the_icon_class(
    browse_page: str,
) -> None:
    """The class rule is what makes ``folder`` a glyph rather than a word.

    The face alone is not enough: Material Symbols substitutes a glyph
    for its name through the font's ligature feature, and that has to
    be on for the element.  The rule rode in with the Google stylesheet
    before gain#1400; now the page carries it.  ``letter-spacing`` is
    pinned beside it because any value but ``normal`` switches
    ligatures back off.
    """
    rules = [
        dict(rule.declarations) for rule in rules_in(browse_page)
        if f".{ICON_CLASS}" in rule.selectors
    ]

    assert len(rules) == 1, rules
    assert rules[0]["font-family"].strip("'\"") == ICON_FONT
    assert rules[0]["font-feature-settings"].strip("'\"") == "liga"
    assert rules[0]["letter-spacing"] == "normal"


def test_the_browse_page_imports_sqlite_wasm_from_inside_the_repository(
    browse_page: str,
) -> None:
    """One import, relative, naming the vendored version's directory.

    Relative, because an inline module script resolves its specifiers
    against the document's base URL -- the same rule the search index's
    own ``fetch()`` relies on to follow the repository under whatever
    host and sub-path it is served at (gain#129).  The version is in
    the directory name because that is what the module's own
    ``import.meta.url`` lookup of ``sqlite3.wasm`` follows; see
    ``gain.templates.static_assets``.
    """
    assert _MODULE_IMPORT.findall(browse_page) == [
        f"./{SQLITE_WASM_PATH}/index.mjs",
    ]


def test_the_about_page_loads_from_no_third_party(about_page: str) -> None:
    """Styled text with no script: nothing to load from anywhere."""
    assert external_origins(about_page) == NO_ORIGINS
    assert not read_page(about_page).preconnects


def test_the_about_page_declares_its_typeface_and_no_icon_font(
    about_page: str,
) -> None:
    """Roboto from the repository, and nothing for glyphs it never draws.

    The about page renders no element carrying the icon class and
    includes no script that could set one, so an icon face here would
    be a declaration that can never show the reader anything -- as the
    ~3.8 MB family it once linked was.  The typeface is the half that
    has to stay: losing it would be a regression hiding inside the fix.
    """
    faces = font_faces_in(about_page)

    assert glyphs_the_page_can_draw(about_page) == frozenset()
    assert set(faces) == {TEXT_FONT}
    assert faces[TEXT_FONT].startswith(".static/"), faces
