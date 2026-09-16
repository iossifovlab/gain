# pylint: disable=W0621
"""The description is the one thing on a resource page behind a shadow root.

A resource page renders ``meta.description`` into a declarative shadow
root on purpose, and this module is the fence around that decision (ADR
0030, gain#1321).  Two things follow from it, and each has a test here:
the boundary is there, and it does what it is kept for -- the page's
per-type sheet stops at it.  The other two tests are the rest of the same
decision: the Summary cell, which renders autoescaped text and no
markup, gets no shadow root, so the page has exactly one and it is the
one the record explains.

What is *not* here: the rules the description shares with the page.  That
the shared sheet reaches both sides is ``test_resource_page_description_
styles``' concern, and it stays there.
"""
from __future__ import annotations

import pytest
from gain.genomic_resources.implementations.basic_resource_impl import (
    BasicResourceImplementation,
)
from gain.genomic_resources.testing.builders import a_basic_resource

from tests.small.templates.conftest import (
    basic_resource_described_by,
    description_shadow_root,
)
from tests.small.templates.page_css import declared_for, rules_in


def test_the_description_renders_inside_a_declarative_shadow_root() -> None:
    """The reader names the boundary's absence; this test exists to hit it."""
    page = BasicResourceImplementation(
        basic_resource_described_by("A *described* score."),
    ).get_info()

    assert "<em>described</em>" in description_shadow_root(page)


def test_the_summary_cell_renders_its_text_in_the_page_with_no_shadow_root(
) -> None:
    """A summary is autoescaped text, so nothing about it needs containing.

    Both halves are pinned -- no boundary, *and* the text still escaped --
    so that dropping the one (ADR 0030) is never read as licence to drop
    the other.
    """
    page = BasicResourceImplementation(
        a_basic_resource()
        .with_meta(summary="<b>bold</b> summary", description="described")
        .build_inmemory(),
    ).get_info()

    start = page.index("<th>Summary</th>")
    cell = page[start:page.index("<th>Description</th>", start)]
    assert "&lt;b&gt;bold&lt;/b&gt; summary" in cell
    assert "shadowrootmode" not in cell, \
        "the Summary cell is wrapped in a shadow root it has no use for"


@pytest.fixture
def basic_page() -> str:
    """A basic resource's page, describing itself with the same table."""
    return BasicResourceImplementation(basic_resource_described_by("""
        | category | meaning |
        |---|---|
        | 1 | high confidence |
    """)).get_info()


def test_a_pages_per_type_sheet_stops_at_the_description(
    gene_score_page: str, basic_page: str,
) -> None:
    """What the boundary is kept for: page layout does not reach content.

    The gene-score sheet lays the page's own tables out ``fixed`` -- right
    for a score table with many equal columns, wrong for the two-column
    table a curator writes into a description, which reads better sized
    by its content (gain#1279).  The per-type sheet is all that differs
    between these two pages' stylesheets, so their descriptions reading
    one and the same sheet is the whole per-type sheet stopping at the
    boundary -- not only the one rule named here, which is asserted on
    the page as the anchor that the two pages really do differ.

    Mutation-proved: including the per-type sheet inside the shadow root's
    ``<style>`` turns this red.
    """
    assert "table-layout: fixed" in declared_for(gene_score_page, "table")
    assert "table-layout: fixed" not in declared_for(basic_page, "table")

    assert (
        rules_in(description_shadow_root(gene_score_page))
        == rules_in(description_shadow_root(basic_page))
    ), "the page's per-type sheet reached the description"


def test_the_description_is_the_only_shadow_root_on_the_page(
    gene_score_page: str,
) -> None:
    """One boundary, with one reason written down.

    A second shadow root anywhere on the page -- a content block, a
    statistics partial -- would need a reason of its own (ADR 0030), and
    would silently re-raise the question this one answers.
    """
    assert gene_score_page.count("shadowrootmode") == 1, \
        "a shadow root other than the description's is on the page"
