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
draws everything a resource page does and more -- the Id cell's copy
button and its tick are the browse page's own row icons (gain#1477);
see ``test_grr_page_icon_font.py`` for the equality that keeps it small.

The markup contract these scripts read is asserted from the rendered
statistics tables, in
``tests/small/genomic_resources/test_info_page_sortable_tables.py``.
"""
from __future__ import annotations

import re

from tests.small.templates.page_css import font_faces_in
from tests.small.templates.page_dom import parse_page
from tests.small.templates.page_origins import (
    external_origins,
    pointed_at_google,
)
from tests.small.templates.vendored_fonts import (
    ICON_FONT,
    TEXT_FONT,
    ligatures_in,
    vendored_icon_font,
)

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


def test_the_sorter_ships_inert_on_a_page_with_no_sortable_table(
    gene_score_page: str,
) -> None:
    """The script is there; nothing on the page opts into it.

    Two assertions rather than one: the script mentioning ``data-sort``
    is not the same as an element carrying it, and it is the second that
    would mean the gene score page had quietly grown a sortable table.
    """
    assert "th[data-sort]" in gene_score_page

    attribute_names = {
        name for name, _ in parse_page(gene_score_page).attributes}

    assert "data-sort" not in attribute_names
    assert "data-sort-value" not in attribute_names


def test_a_resource_page_loads_no_jquery(gene_score_page: str) -> None:
    """One Array.sort() does not justify a library.

    Resource pages are jQuery-free, and the hard part -- parsing and
    ordering -- is already done in Python by ``data-sort-value``.
    """
    assert "jquery" not in gene_score_page.lower()


def test_a_resource_page_reaches_no_origin(gene_score_page: str) -> None:
    """Nothing off the repository: no font host, no CDN, nothing.

    Through the scanner that reads ``<style>`` too: the fonts are the
    one thing a resource page loads, and they are declared in CSS --
    proven on this page by pointing them back at Google and seeing the
    host named, as ``test_grr_page_icon_font.py`` does for its pages.
    """
    assert external_origins(gene_score_page) == frozenset()
    assert external_origins(pointed_at_google(gene_score_page)) == {
        "fonts.gstatic.com",
    }


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
