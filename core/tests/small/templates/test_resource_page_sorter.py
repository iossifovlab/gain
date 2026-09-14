# pylint: disable=W0621,C0114,C0115,C0116,W0613
"""What every resource page pays for carrying the table sorter.

``sortable_table.jinja`` is included from ``resource_template.jinja``,
which is the base of every resource page -- so it ships on the gene
score, gene models and reference genome pages too, none of which have a
sortable table.  That is deliberate, and the price is bounded by two
things this file pins: the script does nothing without opt-in markup,
and it reaches no external origin at all.

The icon face is declared on every resource page, sortable table or
not, and that costs nothing: a browser fetches a font only for an
element that uses the face, and no page without an indicator renders a
``.material-symbols-outlined`` element.  Since gain#1400 the face is a
``@font-face`` over a file of the repository itself, the same vendored
subset the browse page uses -- so what is pinned here is that the page
declares it from the repository and that the subset carries the three
glyphs the sorter draws.  The subset is sized by the browse page, which
draws a copy button and a tick that no resource page has; see
``test_grr_page_icon_font.py`` for the equality that keeps it small.

The markup contract these scripts read is asserted from the rendered
statistics tables, in
``tests/small/genomic_resources/test_info_page_sortable_tables.py``.
"""
from __future__ import annotations

import pathlib
import re
import textwrap
from html.parser import HTMLParser
from urllib.parse import urlparse

import pytest
from gain.gene_scores.implementations.gene_scores_impl import (
    GeneScoreImplementation,
)
from gain.genomic_resources.testing.builders import GeneScoreBuilder

from tests.small.templates.page_css import font_faces_in
from tests.small.templates.test_grr_page_icon_font import (
    ICON_FONT,
    TEXT_FONT,
    vendored_icon_font,
)
from tests.small.templates.vendored_fonts import ligatures_in

#: The only glyphs the sorter draws, written out by hand so the scan
#: below is held to it rather than trusted.
ICON_NAMES = frozenset({"arrow_downward", "arrow_upward", "unfold_more"})

#: How the sorter's script names them: one ``var`` per indicator state,
#: ``var IDLE = "unfold_more";``.
_STATE_GLYPH = re.compile(
    r"\bvar\s+(?:IDLE|ASCENDING|DESCENDING)\s*=\s*\"([a-z][a-z0-9_]*)\"",
)


def glyphs_the_sorter_draws(page: str) -> frozenset[str]:
    """Every glyph name the sorter's script can put in a header."""
    return frozenset(_STATE_GLYPH.findall(page))


class _PageReader(HTMLParser):
    """Collects the page's element attributes and its linked URLs."""

    def __init__(self) -> None:
        super().__init__()
        self.attribute_names: set[str] = set()
        self.urls: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        for name, value in attrs:
            self.attribute_names.add(name)
            if name in ("href", "src") and value is not None:
                self.urls.append(value)


def read_page(page: str) -> _PageReader:
    reader = _PageReader()
    reader.feed(page)
    return reader


@pytest.fixture
def gene_score_page(tmp_path: pathlib.Path) -> str:
    """A resource page with no sortable table anywhere on it."""
    resource = (
        GeneScoreBuilder()
        .with_score("sc984", column_name="sc")
        .with_data(textwrap.dedent("""
            gene sc
            A  1.0
            B  2.0
        """))
        .build_resource(tmp_path)
    )
    return GeneScoreImplementation(resource).get_info()


def test_the_sorter_ships_inert_on_a_page_with_no_sortable_table(
    gene_score_page: str,
) -> None:
    """The script is there; nothing on the page opts into it.

    Two assertions rather than one: the script mentioning ``data-sort``
    is not the same as an element carrying it, and it is the second that
    would mean the gene score page had quietly grown a sortable table.
    """
    assert "th[data-sort]" in gene_score_page

    reader = read_page(gene_score_page)

    assert "data-sort" not in reader.attribute_names
    assert "data-sort-value" not in reader.attribute_names


def test_a_resource_page_loads_no_jquery(gene_score_page: str) -> None:
    """One Array.sort() does not justify a library.

    Resource pages are jQuery-free, and the hard part -- parsing and
    ordering -- is already done in Python by ``data-sort-value``.
    """
    assert "jquery" not in gene_score_page.lower()


def test_a_resource_page_reaches_no_origin(gene_score_page: str) -> None:
    """Nothing off the repository: no font host, no CDN, nothing."""
    hosts = {
        urlparse(url).hostname
        for url in read_page(gene_score_page).urls
        if urlparse(url).scheme in ("http", "https")
    }

    assert hosts == set()


def test_a_resource_page_declares_both_faces_from_the_repository(
    gene_score_page: str,
) -> None:
    """The typeface and the sorter's icon font, each climbing to ``.static/``.

    A resource page is published one directory per id segment below the
    root, so its urls have to climb; how far is pinned on a published
    repository by the CLI tests, where the file is really there.  What
    this pins is that both faces are declared and that neither names a
    host.
    """
    faces = font_faces_in(gene_score_page)

    assert set(faces) == {TEXT_FONT, ICON_FONT}
    assert all(url.startswith("../") for url in faces.values()), faces
    assert all(".static/" in url for url in faces.values()), faces


def test_the_vendored_subset_carries_the_glyphs_the_sorter_draws(
    gene_score_page: str,
) -> None:
    """Three glyphs the sorter can show, all in the one file it loads.

    Containment against the file, not equality: the subset is sized by
    the browse page and the equality lives with it.  What this guards
    is the other direction -- an indicator state renamed in the script
    without a re-subset would render as its name, in words, in every
    sortable header.  The scan is held to the hand-written table first,
    so a script rephrased past the regex fails here rather than
    shrinking the set the file is checked against.
    """
    assert glyphs_the_sorter_draws(gene_score_page) == ICON_NAMES
    assert ligatures_in(vendored_icon_font()) >= ICON_NAMES
