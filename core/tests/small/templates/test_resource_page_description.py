# pylint: disable=W0621
"""A resource page renders ``meta.description`` in the shared dialect.

This call site used to name its own ``extras=["tables"]`` in
``resource_template.jinja``.  It was the *working* half of gain#1278 --
the about page rendered plain Markdown and lost its tables -- and the
reason the two halves could disagree at all: each decided the dialect
for itself.  Removing the literal is what leaves one decision, in
``render_markdown``; this module is the guard that removing it did not
quietly cost the resource page the tables it already had.

That is not hypothetical content.  The SFARI gene-score resources
published in ``grr`` and ``grr_sfari`` describe their categories with a
Markdown table in ``meta.description``, so a regression here is visible
on a live page.
"""
from __future__ import annotations

import pytest
from gain.genomic_resources.implementations.basic_resource_impl import (
    BasicResourceImplementation,
)

from tests.small.templates.conftest import basic_resource_described_by


@pytest.fixture
def page() -> str:
    """The page of a resource describing itself with a table, as SFARI's do."""
    return BasicResourceImplementation(basic_resource_described_by("""
        | category | meaning |
        |---|---|
        | 1 | high confidence |
    """)).get_info()


def test_the_description_reaches_the_page_once(page: str) -> None:
    """Rendered in its own cell, and nowhere else.

    The fence around the summary ``basic_resource_described_by``
    declares: without one the description shows *again*, raw, in the
    summary cell, and the dialect assertion below would be reading two
    copies.
    """
    assert "<td>high confidence</td>" in page
    assert page.count("high confidence") == 1


def test_a_table_in_the_description_reaches_the_resource_page(
    page: str,
) -> None:
    """The dialect survives the template no longer naming it."""
    # Cells, not `<table>`: the resource page is itself laid out as a
    # table, so a bare `<table>` assertion holds whatever the description
    # renders as and would read as a check while testing nothing.  These
    # two words appear only in the description.
    assert "<th>category</th>" in page
    assert "<td>high confidence</td>" in page
    assert "| category | meaning |" not in page
