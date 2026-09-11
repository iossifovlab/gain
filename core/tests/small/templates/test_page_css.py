"""The stylesheet reader the template tests share, read at its own seam.

The two consumers project its rules differently on purpose -- see
:mod:`tests.small.templates.page_css` -- so what is pinned here is the
raw material both projections need: a selector list arrives split, a
repeated rule arrives twice, and the reader does not care whether it was
handed a page or a fragment.
"""
from __future__ import annotations

import pytest

from tests.small.templates.page_css import Rule, declarations_in, rules_in


def test_a_selector_list_comes_back_split() -> None:
    """``td, th { ... }`` is one rule naming two selectors."""
    rules = rules_in("<style>td, th { padding: 5px }</style>")

    assert rules == [Rule(["td", "th"], [("padding", "5px")])]


def test_a_selector_stated_twice_comes_back_twice_in_source_order() -> None:
    """Whether the later rule wins or both count is the consumer's call."""
    rules = rules_in(
        "<style>table { width: 100% } table { table-layout: fixed }</style>")

    assert rules == [
        Rule(["table"], [("width", "100%")]),
        Rule(["table"], [("table-layout", "fixed")]),
    ]


def test_a_commented_out_rule_is_not_a_rule() -> None:
    """A comment can span lines and hold braces; none of it is read."""
    rules = rules_in(
        "<style>"
        "/* td { color: red }\n   still commented */"
        "th { color: blue }"
        "</style>")

    assert rules == [Rule(["th"], [("color", "blue")])]


def test_a_fragment_is_read_by_its_own_first_stylesheet() -> None:
    """A shadow root is read like a page: its first ``<style>``, no other.

    The description's shadow root sits inside a page that carries a
    stylesheet of its own, and a consumer comparing the two must be able
    to hand over either and get that one's rules -- not the page's when
    asked about the fragment, nor the fragment's when asked about the
    page.
    """
    shadow_root = (
        '<template shadowrootmode="open">'
        "<style>a { color: red }</style>"
        "<p>prose</p>"
        "</template>")
    page = (
        "<style>a { color: blue }</style>"
        f"<div>{shadow_root}</div>"
        "<style>a { color: green }</style>")

    assert rules_in(shadow_root) == [Rule(["a"], [("color", "red")])]
    assert rules_in(page) == [Rule(["a"], [("color", "blue")])]


def test_markup_without_a_stylesheet_is_refused_by_name() -> None:
    """Not a bare ``ValueError`` from a substring search."""
    with pytest.raises(AssertionError, match="carries no <style>"):
        rules_in("<p>no stylesheet here</p>")


def test_only_whitespace_spanning_lines_is_folded_out_of_a_value() -> None:
    """Indentation is not part of a value; a quoted double space is.

    The list marker the shared sheet declares is ``'-  '`` -- two spaces
    -- and a reader that collapsed runs of whitespace would report a
    value no sheet contains.
    """
    rules = rules_in(
        "<style>\n"
        "  ul {\n"
        "    font-family: Georgia,\n"
        "      serif;\n"
        "    list-style-type: '-  ';\n"
        "  }\n"
        "</style>")

    assert rules == [Rule(["ul"], [
        ("font-family", "Georgia, serif"),
        ("list-style-type", "'-  '"),
    ])]


@pytest.mark.parametrize("block", [
    "max-width: min(100%, 800px); display: block",
    "max-width: min(100%, 800px); display: block;",
])
def test_a_style_attribute_reads_the_same_with_or_without_a_final_semicolon(
    block: str,
) -> None:
    """An element's own ``style`` is a declaration block, not a sheet.

    The templates spell the same inline cap both ways, so a consumer
    reading it as declarations rather than as a string sees one value.
    """
    assert declarations_in(block) == [
        ("max-width", "min(100%, 800px)"),
        ("display", "block"),
    ]
