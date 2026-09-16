"""What every template test shares.

The template engine is a process-wide singleton, so each test starts and
ends with it reset.  The helpers are imported by their dotted path, as
``tests.small.templates.conftest``: the ``type: basic`` resource carrying
a Markdown description that two modules render, the slice of a page
that is its description's shadow root, and the entry-point stub the
provider tests register templates through.  The gene-score page is a
fixture, shared by the sorter and shadow-root modules.
"""
from __future__ import annotations

import pathlib
import textwrap
from collections.abc import Callable, Iterator
from unittest.mock import MagicMock

import pytest
from gain.gene_scores.implementations.gene_scores_impl import (
    GeneScoreImplementation,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing.builders import (
    a_basic_resource,
    a_gene_score,
)
from gain.templates import reset_caches


@pytest.fixture(autouse=True)
def reset_template_caches() -> Iterator[None]:
    """Reset the template singletons before and after each test."""
    reset_caches()
    yield
    reset_caches()


def basic_resource_described_by(description: str) -> GenomicResource:
    """A ``type: basic`` resource carrying the given description.

    The description is taken as a triple-quoted literal reads: common
    indentation and the blank first line are dropped, so what the
    resource carries is the Markdown as written, starting at its first
    line -- as a YAML ``|`` block would have stored it.

    The summary is a fence, not content -- see
    ``test_the_description_reaches_the_page_once`` for why every
    resource built here carries one.
    """
    return (
        a_basic_resource()
        .with_meta(
            summary="scores for genes",
            description=textwrap.dedent(description).lstrip("\n"))
        .build_inmemory()
    )


def description_shadow_root(page: str) -> str:
    """Return the declarative shadow root the description renders into.

    Addressed from the ``Description`` header cell rather than by index
    among the page's ``<template>`` elements, so that adding a shadow
    root elsewhere on the page cannot silently redirect these assertions
    at someone else's markup.
    """
    start = page.index("<th>Description</th>")
    opening = page.find('<template shadowrootmode="open">', start)
    assert opening != -1, \
        "no declarative shadow root after the Description cell (ADR 0030)"
    return page[opening:page.index("</template>", opening)]


@pytest.fixture
def gene_score_page(tmp_path: pathlib.Path) -> str:
    """A gene-score page whose description carries a two-column table.

    A gene score because its per-type sheet is one of the two that lay
    the page's tables out ``fixed``, and because its page carries a
    content block of its own below the resource table -- the richest
    page a description shares with.  Nothing on it opts into the table
    sorter.
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
    assert "<td>high confidence</td>" in page
    return page


def make_entry_point(
    name: str, provider: Callable[[], dict[str, str]],
) -> MagicMock:
    """An entry point in the ``gain.templates.providers`` group.

    What ``gain.templates`` asks of one: a ``name`` and a ``load()``
    returning the provider, a callable giving template name to source.
    """
    entry_point = MagicMock()
    entry_point.name = name
    entry_point.load.return_value = provider
    return entry_point
