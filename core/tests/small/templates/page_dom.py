"""Read a rendered page as markup rather than as text.

Tests that ask which elements and attributes a page carries read it through
here, so a caveat learned about ``html.parser`` is learned once.  Readers
that track scope as they go -- a table's cells, a breadcrumb's crumbs --
keep their own parser, and take ``VOID_ELEMENTS`` from here when they
need it.  ``test_page_dom.py`` holds this reader to each caveat
below.

``html.parser`` is not an HTML5-spec tokenizer, and where it differs it
under-reports live markup (``<script/>`` opens an element for a browser but
not here).  That is the safe direction for these tests: anything it reports
as an element really is one.

``convert_charrefs`` (on by default) decodes ``&lt;script&gt;`` back into
``<script>``, so character data cannot tell escaped markup from live markup.
Deliberately no whole-page text accumulator, then: ask ``elements`` whether
an element exists, and ``element_text`` what an element encloses.

The open-element stack has no implied-end-tag handling, so ``<p>one<p>two``
nests rather than siblings.  Void elements never fire an end tag and so
never go on it; a self-closing ``<x/>`` is routed by the base class through
``handle_starttag`` then ``handle_endtag``, and so encloses nothing either.
"""
from __future__ import annotations

from html.parser import HTMLParser
from typing import NamedTuple

#: Elements HTML gives no end tag, so they never enclose anything.
VOID_ELEMENTS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
})


class Element(NamedTuple):
    """One element: its tag and the attributes it carries.

    Attributes read as a browser reads them: a bare one has the empty
    value, and of a repeated one only the first is kept, as a browser's
    HTML tokenizer does (``html.parser`` itself reports every copy).
    """

    tag: str
    attributes: dict[str, str]


class PageDom(HTMLParser):
    """A page's elements and the character data each one encloses.

    ``elements`` lists every element in document order.  ``element_text``
    pairs each run of character data with the innermost element open at
    the time: a bare "is there a ``script``" says only that *some* element
    of that name is on the page, and these pages ship their own.
    """

    def __init__(self) -> None:
        super().__init__()
        self.elements: list[Element] = []
        self.element_text: list[tuple[str, str]] = []
        self._open: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes: dict[str, str] = {}
        for name, value in attrs:
            attributes.setdefault(name, value or "")
        self.elements.append(Element(tag, attributes))
        if tag not in VOID_ELEMENTS:
            self._open.append(tag)

    def handle_endtag(self, tag: str) -> None:
        # Unwind to the innermost matching open tag.  The guard matters: a
        # stray end tag would otherwise pop an empty stack and raise.
        if tag in self._open:
            while self._open.pop() != tag:
                pass

    def handle_data(self, data: str) -> None:
        if self._open:
            self.element_text.append((self._open[-1], data))

    @property
    def attributes(self) -> list[tuple[str, str]]:
        """Every ``(name, value)`` any element carries, in document order."""
        return [
            item for element in self.elements
            for item in element.attributes.items()
        ]

    def text_in(self, tag: str) -> str:
        """All the character data ``tag`` encloses, concatenated."""
        return "".join(
            data for open_tag, data in self.element_text if open_tag == tag)


def parse_page(page: str) -> PageDom:
    dom = PageDom()
    dom.feed(page)
    return dom
