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
per-type sheet, and two of the per-type sheets add
``table { table-layout: fixed }``.  So on a gene-score page the page's
``table`` rule and its description's really do differ, deliberately: a
two-column description table is better sized by its content than forced
into equal columns.  What is pinned here is that the *shared* sheet
reaches both sides, which is what gain#1279 was about -- not that every
rule a page has reaches its description.
"""
from __future__ import annotations

import pathlib
import re
import textwrap
from collections.abc import Iterator

import gain.templates as templates_module
import pytest
from gain.genomic_resources.implementations.basic_resource_impl import (
    BasicResourceImplementation,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing import (
    build_filesystem_test_repository,
    setup_directories,
)

#: One rule of a stylesheet: everything up to ``{`` is the selector list,
#: everything to the matching ``}`` is the declarations.  Adequate because
#: the sheets read here are flat -- no ``@media``, no nesting -- and a
#: test that needed more would be reading the wrong thing.
_CSS_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")

_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


@pytest.fixture(autouse=True)
def reset_template_caches() -> Iterator[None]:
    """Reset singleton caches before and after each test."""
    templates_module._state.env = None
    templates_module._state.provider_cache = None
    yield
    templates_module._state.env = None
    templates_module._state.provider_cache = None


def description_shadow_root(page: str) -> str:
    """Return the declarative shadow root the description renders into.

    Addressed from the ``Description`` header cell rather than by index
    among the page's ``<template>`` elements, so that adding a shadow
    root elsewhere on the page cannot silently redirect these assertions
    at someone else's markup.
    """
    start = page.index("<th>Description</th>")
    opening = page.index('<template shadowrootmode="open">', start)
    return page[opening:page.index("</template>", opening)]


def declarations_in(markup: str, selector: str) -> list[str]:
    """Return what ``markup``'s first ``<style>`` declares for ``selector``.

    Extraction and reading are one step on purpose.  Handing raw markup to
    the rule reader would quietly make it part of the first rule's
    selector list -- everything between one ``}`` and the next ``{`` reads
    as a selector -- and that goes wrong silently, for one rule only.
    There is no caller that wants the sheet without reading it, so there
    is no way to make that mistake.

    Rules are matched on the selector appearing in the rule's selector
    *list*, so ``td, th { ... }`` answers for ``td`` and for ``th`` alike,
    and every rule that names it contributes.  Compound selectors that
    would also reach the element -- ``#resource-table th``,
    ``.scrollable-table-container td`` -- are deliberately left out: what
    is compared is the rule a bare element gets on each side of the shadow
    boundary, not the full cascade any one element resolves to.

    Declarations come back normalized and sorted, because this is used to
    compare two sheets and neither the order rules were written in nor the
    indentation they were written at is part of what a reader gets.  The
    normalization folds only whitespace that spans lines: a CSS value can
    carry significant spaces inside quotes -- the list marker is
    ``'-  '``, two of them -- and collapsing those would make this report
    a value no sheet contains.
    """
    opening = markup.index("<style>") + len("<style>")
    stylesheet = markup[opening:markup.index("</style>", opening)]

    declarations: list[str] = []
    for selectors, body in _CSS_RULE.findall(_CSS_COMMENT.sub("", stylesheet)):
        if selector not in [s.strip() for s in selectors.split(",")]:
            continue
        declarations.extend(
            re.sub(r"\s*\n\s*", " ", declaration).strip()
            for declaration in body.split(";") if declaration.strip()
        )
    return sorted(declarations)


def assert_shared_with_page(
    page: str, shadow_root: str, selector: str, *declarations: str,
) -> None:
    """Assert the description styles ``selector`` as the page does.

    Both halves matter: the named declarations pin what a reader gets, and
    the equality pins that it arrives from the same rule the page reads,
    so a copy that later falls behind the shared sheet fails here.
    """
    declared = declarations_in(shadow_root, selector)
    for declaration in declarations:
        assert declaration in declared, \
            f"the description's {selector} does not declare {declaration}: " \
            f"{declared}"
    assert declared == declarations_in(page, selector), \
        f"the description and the page disagree about {selector}"


@pytest.fixture
def resource_with_a_rich_description(
    tmp_path: pathlib.Path,
) -> GenomicResource:
    """A resource describing itself the way the SFARI gene scores do.

    A heading, prose carrying a link, a list and a table -- the shapes a
    GRR author actually writes, each of which the page styles when it
    renders it outside the shadow root.

    It carries an explicit ``summary`` even though no test here reads
    one: a resource with no summary displays its *description* in the
    summary cell instead (gain#1008), which would put a second, plainer
    copy of this markup on the page and make the assertions below
    ambiguous about which copy they found.
    """
    setup_directories(tmp_path, {"one": {
        "genomic_resource.yaml": textwrap.dedent("""
            type: basic
            meta:
              summary: scores for genes
              description: |
                ### Score definitions

                Curated by [SFARI](https://gene.sfari.org/), who publish

                - the categories
                - the evidence behind them

                | category | meaning |
                |---|---|
                | 1 | high confidence |
        """),
        "data.txt": "alabala",
    }})
    resource = build_filesystem_test_repository(tmp_path).get_resource("one")
    assert resource is not None
    return resource


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
