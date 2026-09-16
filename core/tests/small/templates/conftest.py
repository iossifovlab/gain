"""What every template test shares.

The template engine is a process-wide singleton, so each test starts and
ends with it reset.  The helpers are imported by their dotted path, as
``tests.small.templates.conftest``: the ``type: basic`` resource carrying
a Markdown description that two modules render, the two readers the
modules comparing a page with its description's shadow root share -- the
slice that *is* the shadow root, and what a fragment's stylesheet
declares for one selector -- and the entry-point stub the provider tests
register templates through.
"""
from __future__ import annotations

import textwrap
from collections.abc import Callable, Iterator
from unittest.mock import MagicMock

import pytest
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing.builders import a_basic_resource
from gain.templates import reset_caches

from tests.small.templates.page_css import rules_in


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
    at someone else's markup.  Its absence is named rather than left to
    ``str.index``: the boundary is deliberate (ADR 0030), so a consumer
    that cannot find it should say so, not report a broken lookup.
    """
    start = page.index("<th>Description</th>")
    opening = page.find('<template shadowrootmode="open">', start)
    assert opening != -1, \
        "no declarative shadow root after the Description cell (ADR 0030)"
    return page[opening:page.index("</template>", opening)]


def declared_for(markup: str, selector: str) -> list[str]:
    """Return what ``markup``'s first ``<style>`` declares for ``selector``.

    Rules are matched on the selector appearing in the rule's selector
    *list*, so ``td, th { ... }`` answers for ``td`` and for ``th`` alike,
    and every rule that names it contributes.  Compound selectors that
    would also reach the element -- ``#resource-table th``,
    ``.scrollable-table-container td`` -- are deliberately left out: what
    is compared is the rule a bare element gets on each side of the shadow
    boundary, not the full cascade any one element resolves to.

    Declarations come back as ``property: value`` strings, sorted, because
    this is used to compare two sheets and neither the order rules were
    written in nor the indentation they were written at is part of what a
    reader gets.
    """
    return sorted(
        f"{property_}: {value}"
        for rule in rules_in(markup)
        if selector in rule.selectors
        for property_, value in rule.declarations
    )


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
