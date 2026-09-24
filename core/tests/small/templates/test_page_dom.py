# pylint: disable=C0114,C0116
"""The page reader the markup-level page tests stand on.

Several of those tests assert an absence -- no ``on*`` handler, no
``data-sort`` attribute -- and an empty answer is also what a reader that
missed the markup would give.  So the reader is held here to small pages
whose markup is known, including each ``html.parser`` quirk its callers
rely on.
"""
from __future__ import annotations

from tests.small.templates.page_dom import Element, parse_page


def test_each_element_is_recorded_with_its_own_attributes_in_order() -> None:
    page = '<div id="a"><img src="x.png" class="f"><p>t</p></div>'

    dom = parse_page(page)

    assert dom.elements == [
        Element("div", {"id": "a"}),
        Element("img", {"src": "x.png", "class": "f"}),
        Element("p", {}),
    ]


def test_an_attribute_reads_as_a_browser_would() -> None:
    """A bare attribute has the empty value; a repeated one keeps its first.

    The HTML tokenizer drops a duplicate attribute, so the first value is
    the one the browser acts on -- a later ``class`` is not the element's
    class.
    """
    page = '<th data-sort class="first" class="second">'

    dom = parse_page(page)

    assert dom.elements == [
        Element("th", {"data-sort": "", "class": "first"}),
    ]


def test_an_unquoted_value_with_a_space_injects_a_further_attribute() -> None:
    """An unquoted value ends at the space, and what follows it is read
    as further attributes of the same tag -- the reader must report them."""
    page = "<img alt=x onload=gainxss604()>"

    dom = parse_page(page)

    assert ("onload", "gainxss604()") in dom.attributes


def test_text_belongs_to_the_innermost_open_element() -> None:
    """A void element encloses nothing: the text after ``<br>`` is the
    paragraph's, not the break's."""
    page = "<p>one<em>two</em><br>three</p>"

    dom = parse_page(page)

    assert dom.element_text == [("p", "one"), ("em", "two"), ("p", "three")]
    assert dom.text_in("p") == "onethree"


def test_escaped_markup_is_text_and_opens_no_element() -> None:
    """Character references come back decoded, so an escaped tag reads as
    the tag it spells: only ``elements`` tells escaped from live."""
    page = "<p>&lt;script&gt;x()&lt;/script&gt;</p>"

    dom = parse_page(page)

    assert dom.text_in("p") == "<script>x()</script>"
    assert [element.tag for element in dom.elements] == ["p"]


def test_a_self_closing_tag_is_recorded_and_encloses_nothing() -> None:
    page = '<p>one<img src="x.png" />two<span/>three</p>'

    dom = parse_page(page)

    assert dom.elements == [
        Element("p", {}),
        Element("img", {"src": "x.png"}),
        Element("span", {}),
    ]
    assert dom.text_in("p") == "onetwothree"
