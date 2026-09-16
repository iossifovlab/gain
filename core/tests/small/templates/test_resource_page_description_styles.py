# pylint: disable=W0621
"""Description content is styled by the same rules as the page around it.

``meta.description`` renders into a declarative shadow root, and a shadow
root is a *style boundary*: the page's own ``<style>`` -- which includes
the shared page styles -- does not reach inside it.  Inherited properties
(``color``, ``font-family``) do cross, which is why description prose
looks half-right and the gap is easy to miss.

So the shadow root carries a ``<style>`` of its own, and it used to carry
a hand-copied *subset* of the shared sheet: an ``img`` cap and the two
link rules, nothing else.  A description's table therefore arrived
borderless and its headings browser-default black, on a page whose own
tables and headings were styled -- visible on the published SFARI
gene-score pages, whose descriptions carry a table and three headings
(gain#1279).

Each test asserts two things about one kind of content: the value a
reader actually gets, and that it is the *same* declaration the page uses
for its own markup.  The second half is what makes these drift fences --
a future hand-copy that falls behind the shared sheet fails here -- and
the first is what stops the comparison from passing on two rules that are
equally absent.

**What that second half does not claim.**  These pages render through the
*basic* implementation, whose per-type ``styles_template`` is an empty
file.  A page's bare-element rules are the shared sheet *plus* that
per-type sheet, and the per-type sheet is meant to stop at the shadow
root (ADR 0030 -- ``test_resource_page_shadow_root`` pins it).  What is
pinned here is that the *shared* sheet reaches both sides, which is what
gain#1279 was about -- not that every rule a page has reaches its
description.
"""
from __future__ import annotations

import pytest
from gain.genomic_resources.implementations.basic_resource_impl import (
    BasicResourceImplementation,
)
from gain.genomic_resources.repository import GenomicResource

from tests.small.templates.conftest import (
    basic_resource_described_by,
    declared_for,
    description_shadow_root,
)


def assert_shared_with_page(
    page: str, shadow_root: str, selector: str, *declarations: str,
) -> None:
    """Assert the description styles ``selector`` as the page does.

    Both halves matter: the named declarations pin what a reader gets, and
    the equality pins that it arrives from the same rule the page reads,
    so a copy that later falls behind the shared sheet fails here.
    """
    declared = declared_for(shadow_root, selector)
    for declaration in declarations:
        assert declaration in declared, (
            f"the description's {selector} does not declare {declaration}: "
            f"{declared}")
    assert declared == declared_for(page, selector), \
        f"the description and the page disagree about {selector}"


@pytest.fixture
def resource_with_a_rich_description() -> GenomicResource:
    """A resource describing itself the way the SFARI gene scores do.

    A heading, prose carrying a link, a list and a table -- the shapes a
    GRR author actually writes, each of which the page styles when it
    renders it outside the shadow root.
    """
    return basic_resource_described_by("""
        ### Score definitions

        Curated by [SFARI](https://gene.sfari.org/), who publish

        - the categories
        - the evidence behind them

        | category | meaning |
        |---|---|
        | 1 | high confidence |
    """)


@pytest.fixture
def page(resource_with_a_rich_description: GenomicResource) -> str:
    """The rendered resource page carrying that description."""
    return BasicResourceImplementation(
        resource_with_a_rich_description).get_info()


@pytest.fixture
def shadow_root(page: str) -> str:
    """The description's shadow root within that page."""
    return description_shadow_root(page)


def test_a_description_table_gets_the_pages_cell_borders(
    page: str, shadow_root: str,
) -> None:
    """A table in a description is bordered like the page's own tables."""
    # Cells, not `<table>`: the resource page is itself laid out as a
    # table, so a bare `<table>` assertion would hold whatever the
    # description rendered as.  These words appear only in the table.
    assert "<th>category</th>" in shadow_root
    assert "<td>high confidence</td>" in shadow_root

    assert_shared_with_page(
        page, shadow_root, "td",
        "border: 1px solid #cfd8df", "padding: 5px 10px")
    assert_shared_with_page(page, shadow_root, "th", "background: #ecf1f6")


def test_a_description_heading_gets_the_pages_heading_colour(
    page: str, shadow_root: str,
) -> None:
    """A heading in a description is coloured like the page's own."""
    assert "<h3>Score definitions</h3>" in shadow_root

    assert_shared_with_page(page, shadow_root, "h3", "color: #5b778c")


def test_a_description_list_gets_the_pages_list_marker(
    page: str, shadow_root: str,
) -> None:
    """A list in a description is marked like the page's own lists."""
    assert "<li>the categories</li>" in shadow_root

    assert_shared_with_page(
        page, shadow_root, "ul", "list-style-type: '-  '")


def test_a_description_link_takes_its_colour_from_the_pages_rule(
    page: str, shadow_root: str,
) -> None:
    """A link in a description is coloured like the page's own links.

    Unlike its neighbours this one held before gain#1279 as well: the
    link rules were the part of the shared sheet somebody had already
    copied in by hand.  It is here as the fence around removing that
    copy -- the description must keep the colours it had, and now get
    them from the same place as everything else.
    """
    assert '<a href="https://gene.sfari.org/">SFARI</a>' in shadow_root

    assert_shared_with_page(page, shadow_root, "a", "color: #24699E")
    assert_shared_with_page(page, shadow_root, "a:hover", "color: #4C93C9")


@pytest.fixture
def resource_described_by_a_directory_tree() -> GenomicResource:
    """A resource whose description lays out its siblings as a tree.

    The NYGC single-cell demo GRR's dataset summaries do exactly this: an
    indented block drawn with box characters, one "N resources" column
    aligned by spaces.  Its meaning is in the alignment, so it only reads
    right in a font where every glyph is the same width.
    """
    return basic_resource_described_by("""
        The dataset is laid out as

            johansen2025Crossspecies/
            ├── rna_expression_matrix/       15 resources
            │   ├── HMBA-10xMultiome-BG/      6 resources
            │   └── HMBA-Macaque-PatchSeq/    1 resource
            └── atac_fragments/             518 resources
    """)


@pytest.fixture
def tree_page(resource_described_by_a_directory_tree: GenomicResource) -> str:
    """The rendered resource page carrying that description."""
    return BasicResourceImplementation(
        resource_described_by_a_directory_tree).get_info()


def test_a_description_code_block_is_set_in_a_monospace_font(
    tree_page: str,
) -> None:
    """A code block in a description keeps every glyph the same width.

    The shared sheet's ``*`` reset names Roboto for every element, which
    outranks the browser's own ``monospace`` default for ``pre`` and
    ``code``, and until gain#1452 nothing restored it: a directory tree
    drawn in a description came out ragged, its aligned column wandering
    with the width of each name's letters.  Both elements are pinned --
    the block's ``pre`` and the ``code`` markdown2 nests inside it --
    because either one naming a proportional font is enough to break the
    alignment.
    """
    shadow_root = description_shadow_root(tree_page)
    assert "├── rna_expression_matrix/       15 resources" in shadow_root
    assert "<pre><code>" in shadow_root

    for selector in ("pre", "code"):
        declared = declared_for(shadow_root, selector)
        fonts = [d for d in declared if d.startswith("font-family: ")]
        assert fonts, f"nothing names a font for {selector}: {declared}"
        assert all(font.rstrip().endswith("monospace") for font in fonts), \
            f"{selector} falls back to a proportional font: {fonts}"
        assert_shared_with_page(tree_page, shadow_root, selector, *fonts)


def test_a_link_inside_a_description_code_block_keeps_the_monospace_font(
) -> None:
    """A tree written as raw HTML so its folders can be linked stays aligned.

    Markdown cannot put a link inside a code block, so an author who
    wants a clickable tree writes the ``<pre>`` themselves.  The reset
    sets the font *on* every element rather than leaving it to inherit,
    so the ``<a>`` inside would come out in Roboto and shift the column
    it sits in, unless the descendants of ``pre`` are named as well.
    """
    page = BasicResourceImplementation(basic_resource_described_by("""
        The dataset is laid out as

        <pre>demo/
        ├── <a href="../index.html#/demo/scores">scores/</a>      15 resources
        └── <a href="../index.html#/demo/genomes">genomes/</a>     3 resources
        </pre>
    """)).get_info()
    shadow_root = description_shadow_root(page)
    assert '<a href="../index.html#/demo/scores">scores/</a>' in shadow_root

    fonts = [
        d for d in declared_for(shadow_root, "pre")
        if d.startswith("font-family: ")]
    assert fonts
    assert_shared_with_page(page, shadow_root, "pre *", *fonts)
    assert_shared_with_page(page, shadow_root, "code *", *fonts)


def test_a_wide_description_code_block_scrolls_rather_than_wraps(
    tree_page: str,
) -> None:
    """A tree wider than the page scrolls sideways; a wrapped line is a
    broken tree just as surely as a proportional one."""
    shadow_root = description_shadow_root(tree_page)

    assert_shared_with_page(
        tree_page, shadow_root, "pre", "overflow-x: auto")


def test_description_content_is_laid_out_in_the_pages_box_model(
    page: str, shadow_root: str,
) -> None:
    """Description content measures widths the way the page does.

    Not cosmetic: the shared sheet gives a cell padding and asks its
    table to fill the width it is given.  Under the browser default box
    model that padding is added *outside* the declared width, so the same
    table that fits on the page overflows in a shadow root the reset
    never reached.
    """
    assert "<td>high confidence</td>" in shadow_root

    assert_shared_with_page(page, shadow_root, "*", "box-sizing: border-box")
