"""``render_markdown`` rescues prose from bogus tags -- and nothing else.

The dividing line under test: a ``<`` or ``</`` whose tag name is neither
a known HTML/SVG/MathML element nor hyphenated is escaped; everything a
browser recognizes as a real element passes through untouched (ADR 0016 --
this is not a sanitizer).  The page-level statement of the same boundary
lives in ``tests/small/annotation/test_annotate_doc_bogus_tag_rescue.py``
and ``test_annotate_doc_trust.py``.
"""
from __future__ import annotations

import pytest
from gain.templates.markdown_support import render_markdown


def test_a_bogus_opening_tag_is_escaped_and_the_sentence_survives() -> None:
    out = render_markdown("values <thresh are dropped")

    assert "values &lt;thresh are dropped" in out
    assert "<thresh" not in out


def test_a_bogus_closing_tag_is_escaped_by_the_same_rule() -> None:
    out = render_markdown("values </thresh> are dropped")

    assert "values &lt;/thresh> are dropped" in out
    assert "</thresh" not in out


@pytest.mark.parametrize("payload", [
    "<em>kept</em>",
    '<a href="https://example.org" target="_blank">kept</a>',
    "line<br/>break",
    "<script>kept()</script>",
], ids=["em", "a-href", "br-self-closing", "script"])
def test_a_known_html_element_passes_through_untouched(payload: str) -> None:
    """Includes ``<script>``: trusted-author markup is not sanitized."""
    out = render_markdown(f"before {payload} after")

    assert payload in out


def test_an_uppercase_known_element_passes_through_untouched() -> None:
    """Tag-name recognition is case-insensitive, as in a browser."""
    out = render_markdown("keep <STRONG>this</STRONG> live")

    assert "<STRONG>this</STRONG>" in out


def test_a_hyphenated_custom_element_passes_through_untouched() -> None:
    """Out-of-tree plugin documentation may carry custom elements."""
    out = render_markdown("uses <my-widget>live</my-widget> markup")

    assert "<my-widget>live</my-widget>" in out


def test_inline_svg_in_prose_passes_through_untouched() -> None:
    """A curator's inline figure keeps rendering (ADR 0016)."""
    payload = '<svg viewBox="0 0 4 4"><path d="M0 0h4"/></svg>'

    out = render_markdown(f"figure: {payload}")

    assert payload in out


@pytest.mark.parametrize("payload", [
    "<script>if (x <thresh) { mark() }</script>",
    "<style>a.x<thresh { color: red }</style>",
], ids=["script-body", "style-body"])
def test_a_raw_text_element_body_is_never_touched(payload: str) -> None:
    """Character references do not decode inside script/style content.

    An ``&lt;`` planted there reaches the JS engine or CSS parser as five
    literal characters, so the rescue must not reach into raw-text
    elements at all -- the author's ``<`` comparisons are their own.
    """
    out = render_markdown(f"run {payload} now")

    assert payload in out


def test_a_bogus_tag_inside_a_code_span_stays_a_literal() -> None:
    """markdown2 escapes code spans itself; the rescue must not double it.

    Post-processing the rendered HTML (rather than pre-escaping the
    Markdown source) is what keeps the ``&lt;`` here single.
    """
    out = render_markdown("compare `a <thresh b` here")

    assert "<code>a &lt;thresh b</code>" in out
    assert "&amp;lt;" not in out
