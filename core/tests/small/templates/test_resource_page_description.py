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

import pathlib
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


@pytest.fixture(autouse=True)
def reset_template_caches() -> Iterator[None]:
    """Reset singleton caches before and after each test."""
    templates_module._state.env = None
    templates_module._state.provider_cache = None
    yield
    templates_module._state.env = None
    templates_module._state.provider_cache = None


@pytest.fixture
def resource_with_a_table_description(
    tmp_path: pathlib.Path,
) -> GenomicResource:
    """A resource describing itself with a table, as SFARI's do.

    It carries an explicit ``summary`` even though the test never looks
    at one: a resource with no summary displays its *description* in the
    summary cell instead (gain#1008), as plain text rather than rendered
    Markdown.  Without the summary here, the raw ``| category |`` row
    would reach the page through that cell and the assertion below that
    it does *not* survive as literal text would fail for a reason that
    has nothing to do with the dialect.
    """
    setup_directories(tmp_path, {"one": {
        "genomic_resource.yaml": textwrap.dedent("""
            type: basic
            meta:
              summary: scores for genes
              description: |
                | category | meaning |
                |---|---|
                | 1 | high confidence |
        """),
        "data.txt": "alabala",
    }})
    resource = build_filesystem_test_repository(tmp_path).get_resource("one")
    assert resource is not None
    return resource


def test_a_table_in_the_description_reaches_the_resource_page(
    resource_with_a_table_description: GenomicResource,
) -> None:
    """The dialect survives the template no longer naming it."""
    page = BasicResourceImplementation(
        resource_with_a_table_description).get_info()

    # Cells, not `<table>`: the resource page is itself laid out as a
    # table, so a bare `<table>` assertion holds whatever the description
    # renders as and would read as a check while testing nothing.  These
    # two words appear only in the description.
    assert "<th>category</th>" in page
    assert "<td>high confidence</td>" in page
    assert "| category | meaning |" not in page
