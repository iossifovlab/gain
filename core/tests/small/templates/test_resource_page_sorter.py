# pylint: disable=W0621,C0114,C0115,C0116,W0212,W0613
"""What every resource page pays for carrying the table sorter.

``sortable_table.jinja`` is included from ``resource_template.jinja``,
which is the base of every resource page -- so it ships on the gene
score, gene models and reference genome pages too, none of which have a
sortable table.  That is deliberate, and the price is bounded by two
things this file pins: the script does nothing without opt-in markup,
and it brings no new external origin along with it.

Note what the second of those does NOT say.  The stylesheet is a real
request on every resource page, sortable table or not -- only the font
file behind it is conditional, since no page without an indicator
renders a ``.material-symbols-outlined`` element.  What is pinned here
is the origin set and the subsetting, which are what keep that request
cheap; the request itself is the accepted cost of putting the include at
the page base rather than in the statistics templates.

The sorter's header indicator uses Material Symbols to match
``grr_index.jinja`` -- which pulls the whole variable icon font for the
same three glyphs.  Subsetting with ``icon_names`` is what keeps this
copy cheap, and asserting on the *set of origins* rather than on the URL
keeps the next person from reaching for a CDN.

The markup contract these scripts read is asserted from the rendered
statistics tables, in
``tests/small/genomic_resources/test_info_page_sortable_tables.py``.
"""
from __future__ import annotations

import pathlib
import textwrap
from collections.abc import Iterator
from html.parser import HTMLParser
from urllib.parse import urlparse

import gain.templates as templates_module
import pytest
from gain.gene_scores.implementations.gene_scores_impl import (
    GeneScoreImplementation,
)
from gain.genomic_resources.testing.builders import GeneScoreBuilder

#: The origins a resource page is allowed to reach.  Both were already
#: there for Roboto, so the icon font adds no third party.
ALLOWED_ORIGINS = {"fonts.googleapis.com", "fonts.gstatic.com"}

#: The only glyphs the sorter draws.
ICON_NAMES = ("arrow_downward", "arrow_upward", "unfold_more")


@pytest.fixture(autouse=True)
def reset_template_caches() -> Iterator[None]:
    """Reset singleton caches before and after each test."""
    templates_module._state.env = None
    templates_module._state.provider_cache = None
    yield
    templates_module._state.env = None
    templates_module._state.provider_cache = None


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
    """One Array.sort() does not justify a CDN and a third origin.

    ``grr_scripts.jinja`` has a sorter already, but it is jQuery-shaped
    and that include pulls jQuery from ajax.googleapis.com.  Resource
    pages are jQuery-free, and the hard part -- parsing and ordering --
    is already done in Python by ``data-sort-value``.
    """
    assert "jquery" not in gene_score_page.lower()


def test_a_resource_page_reaches_no_origin_it_did_not_already(
    gene_score_page: str,
) -> None:
    """The icon font rides the origins Roboto already brought."""
    hosts = {
        urlparse(url).hostname
        for url in read_page(gene_score_page).urls
        if urlparse(url).scheme in ("http", "https")
    }

    assert hosts <= ALLOWED_ORIGINS, hosts


def test_the_icon_font_is_subsetted_to_the_glyphs_the_sorter_draws(
    gene_score_page: str,
) -> None:
    """Three glyphs, not the whole variable icon font.

    ``grr_index.jinja`` loads the unsubsetted family; retrofitting the
    subset onto it is a separate, cheap follow-up.  Pinning the exact
    ``icon_names`` list here is what makes a fourth glyph a deliberate
    edit rather than a silent download.
    """
    stylesheets = [
        url for url in read_page(gene_score_page).urls
        if "Material+Symbols" in url
    ]

    assert len(stylesheets) == 1, stylesheets
    assert f"icon_names={','.join(ICON_NAMES)}" in stylesheets[0]
