# pylint: disable=W0621
"""The description is the one thing on a resource page behind a shadow root.

A resource page renders ``meta.description`` into a declarative shadow
root on purpose, and this module is the fence around that decision (ADR
0030, gain#1321).  Two things follow from it, and each has a test here:
the boundary is there, and it does what it is kept for -- the page's
per-type layout rules stop at it.  The third test is the other half of
the same decision: the Summary cell, which renders autoescaped text and
no markup, gets no shadow root, so the page has exactly one and it is
the one the record explains.

What is *not* here: the rules the description shares with the page.  That
the shared sheet reaches both sides is ``test_resource_page_description_
styles``' concern, and it stays there.
"""
from __future__ import annotations

import pathlib
import textwrap

from gain.gene_scores.implementations.gene_scores_impl import (
    GeneScoreImplementation,
)
from gain.genomic_resources.implementations.basic_resource_impl import (
    BasicResourceImplementation,
)
from gain.genomic_resources.testing.builders import (
    a_basic_resource,
    a_gene_score,
)

from tests.small.templates.conftest import (
    declared_for,
    description_shadow_root,
)


def summary_cell(page: str) -> str:
    """Return the Summary row's cell, up to the row that follows it."""
    start = page.index("<th>Summary</th>")
    return page[start:page.index("<th>Description</th>", start)]


def description_cell(page: str) -> str:
    """Return the Description row's cell, up to the row that follows it."""
    start = page.index("<th>Description</th>")
    return page[start:page.index("<th>Labels</th>", start)]


def test_the_description_renders_inside_a_declarative_shadow_root() -> None:
    """The boundary is deliberate, so its absence is a failure by name.

    The description-styles tests find the shadow root by substring and
    would fail without it too -- as a bare ``ValueError`` from inside a
    helper, which reads as a broken test rather than as the decision it
    is.  This one says what was expected and where the reason is written.
    """
    page = BasicResourceImplementation(
        a_basic_resource()
        .with_meta(summary="a score", description="A *described* score.")
        .build_inmemory(),
    ).get_info()

    cell = description_cell(page)
    assert "<em>described</em>" in cell
    assert '<template shadowrootmode="open">' in cell, (
        "the description is not rendered inside a declarative shadow root; "
        "the boundary is deliberate -- ADR 0030 says why")


def test_the_summary_cell_renders_its_text_in_the_page_with_no_shadow_root(
) -> None:
    """A summary is autoescaped text, so nothing about it needs containing.

    The cell used to be wrapped in a shadow root like the description's,
    which contained nothing: the template autoescapes, so authored markup
    in ``meta.summary`` reaches the reader as literal text either way,
    and there is no ``<style>`` for the boundary to keep in or out.  Both
    halves are pinned -- no boundary, *and* the text still escaped -- so
    that removing the one is never read as licence to drop the other.
    """
    page = BasicResourceImplementation(
        a_basic_resource()
        .with_meta(summary="<b>bold</b> summary", description="described")
        .build_inmemory(),
    ).get_info()

    cell = summary_cell(page)
    assert "&lt;b&gt;bold&lt;/b&gt; summary" in cell
    assert "shadowrootmode" not in cell, \
        "the Summary cell is wrapped in a shadow root it has no use for"


def test_a_pages_per_type_layout_rule_stops_at_the_description(
    tmp_path: pathlib.Path,
) -> None:
    """What the boundary is kept for: page layout does not reach content.

    The gene-score sheet lays the page's own tables out ``fixed`` -- right
    for a score table with many equal columns, wrong for the two-column
    table a curator writes into a description, which reads better sized
    by its content (gain#1279).  Both sides are asserted: the page's rule
    is really there, so the description's lack of it is the boundary
    holding and not the rule having gone.

    Mutation-proved: including the per-type sheet inside the shadow root's
    ``<style>`` turns this red.
    """
    resource = (
        a_gene_score()
        .with_score("sc", column_name="sc")
        .with_data(textwrap.dedent("""
            gene sc
            A  1.0
            B  2.0
        """))
        .with_meta(
            summary="a gene score",
            description=textwrap.dedent("""
                | category | meaning |
                |---|---|
                | 1 | high confidence |
            """))
        .build_resource(tmp_path)
    )
    page = GeneScoreImplementation(resource).get_info()
    shadow_root = description_shadow_root(page)
    assert "<td>high confidence</td>" in shadow_root

    assert "table-layout: fixed" in declared_for(page, "table")
    assert not [
        d for d in declared_for(shadow_root, "table")
        if d.startswith("table-layout")
    ], "the page's per-type table layout reached the description"
