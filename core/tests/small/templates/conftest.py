"""Fixtures every template test shares.

The template engine is a process-wide singleton, so each test starts and
ends with it reset; and the ``type: basic`` resource carrying a Markdown
description that two modules build is defined here once, with the one
non-obvious thing about it written down once.
"""
from __future__ import annotations

import pathlib
import textwrap
from collections.abc import Callable, Iterator

import pytest
import yaml
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing import (
    build_filesystem_test_repository,
    setup_directories,
)
from gain.templates import reset_caches


@pytest.fixture(autouse=True)
def reset_template_caches() -> Iterator[None]:
    """Reset the template singletons before and after each test."""
    reset_caches()
    yield
    reset_caches()


@pytest.fixture
def basic_resource_described_by(
    tmp_path: pathlib.Path,
) -> Callable[[str], GenomicResource]:
    """Build a ``type: basic`` resource carrying the given description.

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
    def build(description: str) -> GenomicResource:
        setup_directories(tmp_path, {"one": {
            "genomic_resource.yaml": yaml.safe_dump({
                "type": "basic",
                "meta": {
                    "summary": "scores for genes",
                    "description": textwrap.dedent(description).lstrip("\n"),
                },
            }),
            "data.txt": "alabala",
        }})
        resource = build_filesystem_test_repository(tmp_path) \
            .get_resource("one")
        assert resource is not None
        return resource
    return build
