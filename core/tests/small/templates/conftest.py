"""What every template test shares.

The template engine is a process-wide singleton, so each test starts and
ends with it reset.  The helpers are imported by their dotted path, as
``tests.small.templates.conftest``: the ``type: basic`` resource carrying
a Markdown description that two modules render, and the entry-point stub
the provider tests register templates through.
"""
from __future__ import annotations

import textwrap
from collections.abc import Callable, Iterator
from unittest.mock import MagicMock

import pytest
import yaml
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing import build_inmemory_test_resource
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

    The resource carries an explicit ``summary`` even though no test
    reads one: a resource with no summary displays its *description* in
    the summary cell instead (gain#1008), as plain text rather than
    rendered Markdown.  Without it the description would reach the page
    twice -- once rendered, once raw -- and an assertion about "the
    description on the page" would be ambiguous about which copy it
    found, failing or passing for a reason that has nothing to do with
    what it is about.
    """
    return build_inmemory_test_resource({
        "genomic_resource.yaml": yaml.safe_dump({
            "type": "basic",
            "meta": {
                "summary": "scores for genes",
                "description": textwrap.dedent(description).lstrip("\n"),
            },
        }),
        "data.txt": "alabala",
    })


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
