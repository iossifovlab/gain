# pylint: disable=W0621,C0114,C0115,C0116,W0212,W0613
"""Autoescape behaviour of the shared GAIn Jinja environment.

Templates are all named ``*.jinja`` whether they emit HTML or Markdown,
so the environment cannot decide by extension.  The HTML ones escape;
the Markdown ones must not, because GPF renders their output as Markdown
downstream and HTML escaping mangles both the syntax and the ``&`` in a
histogram image URL.
"""
from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import gain.templates as templates_module
import pytest
from gain.templates import get_template


@pytest.fixture(autouse=True)
def reset_template_caches() -> Iterator[None]:
    """Reset singleton caches before and after each test."""
    templates_module._state.env = None
    templates_module._state.provider_cache = None
    yield
    templates_module._state.env = None
    templates_module._state.provider_cache = None


def test_histogram_url_query_separator_survives_in_markdown() -> None:
    """The histogram image URL keeps its raw ``&``.

    score_histogram.jinja emits a Markdown image ref, not HTML.  Escaping
    the URL to ``&amp;`` breaks the query string GPF's help pane fetches.
    """
    rendered = get_template("score_histogram.jinja").render(
        hist_url="http://example.org/hist.png?a=1&b=2",
        score_def={},
    )

    assert "http://example.org/hist.png?a=1&b=2" in rendered
    assert "&amp;" not in rendered


def test_score_help_keeps_its_embedded_markdown_intact() -> None:
    """A help pane's Markdown reaches GPF as Markdown, not as entities."""
    rendered = get_template("genomic_score_help.jinja").render(
        data={"description": "scores > 0.5 are **strong** & useful"},
    )

    assert "scores > 0.5 are **strong** & useful" in rendered
    assert "&gt;" not in rendered
    assert "&amp;" not in rendered


def test_a_plugin_supplied_template_is_autoescaped_too() -> None:
    """Out-of-tree templates load through the same environment."""
    def provider() -> dict[str, str]:
        return {"plugin_page.jinja": "<p>{{ value }}</p>"}

    entry_point = MagicMock()
    entry_point.name = "plugin"
    entry_point.load.return_value = provider

    with patch(
        "gain.templates.entry_points", return_value=[entry_point],
    ):
        rendered = get_template("plugin_page.jinja").render(
            value="<gainxss558plugin>")

    assert rendered == "<p>&lt;gainxss558plugin&gt;</p>"
