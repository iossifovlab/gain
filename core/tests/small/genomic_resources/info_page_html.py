"""Read a rendered info page: its sections, its tables and their cells.

Every statistics test that asserts something about a generated info page
reaches the markup through here.  Before iossifovlab/gain#992 each of them
carried its own splitter, and the four had already drifted apart in where they
thought a section *ends*: one stopped at the next ``<h2>``, one ran to the end
of the page, one stopped at the first ``</table>``, and the fourth -- the
parser this module grew from -- stopped at the next heading of any level.
Two of the three string splitters failed by silently *widening* their section
rather than by erroring, so an assertion could go on passing against a table
it was never pointed at.

Two entry points, bounded differently on purpose:

* :func:`section_after` when the assertion is about the *markup itself* --
  that a value came out escaped, or that an element sits where it does.
  Parsing destroys exactly that.  A section holds its own subsections, so it
  is bounded at the next heading of its own level or higher: Alleles runs
  past Allele classes and the substitution matrix and stops at Files.
* :func:`table_after` when the assertion is about tabulated *values*.  It
  parses, so it is immune to attributes arriving on a tag -- the reason the
  regex splitters had to be loosened twice already, once when
  ``data-sort-value`` landed (gain#984) and once when a column was added to
  a neighbouring table (gain#988).  It is bounded at the next heading of any
  level, because a table under a subheading is that subsection's: answering
  a table-less section with one from below it would be the same silent
  widening the string splitters were retired for.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import NamedTuple

#: The level of the heading a section is asked for, e.g. 2 for ``<h2>``.
_HEADING_LEVEL = re.compile(r"<h([1-6])")

#: Elements that never fire an end tag.  Counting one as a level deeper
#: would leave the reader permanently inside it, so the rest of the cell
#: would be attributed to a nested element and vanish from ``own_text``.
_VOID_ELEMENTS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
})

#: Any heading at all.  What bounds the search for a section's OWN table:
#: a table rendered under a subheading belongs to that subsection, and
#: answering with it would be the silent widening this module exists to end.
_ANY_HEADING = r"<h[1-6][ >]"


def _next_heading_of(heading: str) -> str:
    """The pattern bounding ``heading``'s section: the next peer or above.

    A section holds its own subsections, so an ``<h2>`` runs past every
    ``<h3>`` beneath it and stops at the next ``<h2>``, while an ``<h3>``
    stops at either.  Bounding every section at the next heading of *any*
    level would cut the Alleles section off at its own Allele-classes
    subheading and quietly narrow every assertion about it.

    ``[ >]`` rather than ``>`` because the Files heading that ends the last
    statistics section carries a ``style`` attribute; a bound that missed it
    would let a scan run off the end of the page and into the Files table.
    """
    match = _HEADING_LEVEL.match(heading)
    assert match is not None, f"not a heading: {heading!r}"
    levels = "".join(str(level) for level in range(1, int(match.group(1)) + 1))
    return rf"<h[{levels}][ >]"


class Cell(NamedTuple):
    """One rendered cell: its attributes and its text.

    ``text`` and ``own_text`` differ only for a cell with something nested
    inside it, which on these pages means a count carrying a muted share:
    gain#988 renders ``<td>{count}<div class="text-muted">{share}</div></td>``.
    ``text`` is then the two run together -- a count of 1 at a 33.33% share
    reads ``133.33%``, which is neither number -- so an assertion about the
    count wants ``own_text`` and one about the whole rendering wants ``text``.
    """

    attrs: dict[str, str]
    text: str
    own_text: str

    @property
    def sort_value(self) -> str | None:
        """The sort key the sorter would read, or ``None`` if absent."""
        return self.attrs.get("data-sort-value")


class Table(NamedTuple):
    """A parsed table, its rows grouped by the section they sit in."""

    head: list[list[Cell]]
    body: list[list[Cell]]
    foot: list[list[Cell]]
    loose: list[list[Cell]]

    @property
    def rows(self) -> list[list[Cell]]:
        """The data rows, whether or not a ``<tbody>`` wraps them.

        The per-chromosome tables wrap theirs and put the total in a
        ``<tfoot>``; the ``<h3>`` tables beneath them -- Allele classes, the
        substitution matrix, Complex alleles -- have a ``<thead>`` and then
        bare rows.  Reading ``body`` alone therefore returns nothing at all
        for those, and returns it *quietly*: an assertion about them passes
        while checking nothing.

        ``foot`` is deliberately excluded.  A total is not a data row, and
        gain#984 split it out precisely so that no comparator could float it
        into the middle of the data.
        """
        return self.body or self.loose

    def column(self, name: str) -> list[Cell]:
        """The data cells under the header named ``name``.

        By header text rather than index, because the Coverage table's
        column set is conditional on the statistic that was built.
        """
        assert self.head, "the table has no <thead> to name a column from"
        headers = [cell.text for cell in self.head[0]]
        assert name in headers, f"no {name!r} column in {headers}"
        index = headers.index(name)
        return [row[index] for row in self.rows]


class _TableReader(HTMLParser):
    """Collects ``<tr>``s into the section that encloses them."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: dict[str, list[list[Cell]]] = {
            "thead": [], "tbody": [], "tfoot": [], "": []}
        self._section = ""
        self._row: list[Cell] | None = None
        self._text: list[str] | None = None
        self._own_text: list[str] | None = None
        #: How deep inside the open cell the parser currently is.  Text is
        #: the cell's *own* only at depth 0; below that it belongs to a
        #: nested element, which is where the muted share sits.
        self._depth = 0
        self._attrs: dict[str, str] = {}

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag in ("thead", "tbody", "tfoot"):
            self._section = tag
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._text = []
            self._own_text = []
            self._depth = 0
            self._attrs = {k: v if v is not None else "" for k, v in attrs}
        elif self._text is not None and tag not in _VOID_ELEMENTS:
            self._depth += 1

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        # Overridden so an XHTML-style ``<br />`` is not counted as an open
        # element; the base class would route it through handle_starttag.
        if tag in ("td", "th"):
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("thead", "tbody", "tfoot"):
            self._section = ""
        elif tag == "tr" and self._row is not None:
            self.rows[self._section].append(self._row)
            self._row = None
        elif tag in ("td", "th") and self._text is not None:
            if self._row is not None:
                self._row.append(Cell(
                    self._attrs,
                    "".join(self._text).strip(),
                    "".join(self._own_text or []).strip(),
                ))
            self._text = None
            self._own_text = None
        elif self._text is not None and self._depth:
            self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._text is None:
            return
        self._text.append(data)
        if not self._depth and self._own_text is not None:
            self._own_text.append(data)


def sort_keys(cells: list[Cell]) -> list[str]:
    """The cells' sort keys, asserting that every one of them has a key.

    A cell that renders a value but carries no ``data-sort-value`` sorts
    last whatever the direction, so a whole column silently missing the
    attribute would not throw -- it would just sit still.
    """
    missing = [cell for cell in cells if cell.sort_value is None]
    assert not missing, f"cells with no sort key: {missing}"
    return [cell.sort_value for cell in cells if cell.sort_value is not None]


def section_after(page: str, heading: str) -> str:
    """``heading``'s own section, as raw markup.

    For the assertions that are about the markup rather than about the
    values in it: that a section says "not computed" and carries no table at
    all, that it renders one image and not three, that a rendered ``<`` came
    out escaped.  :func:`table_after` serves none of those -- it refuses a
    section with no table, and parsing undoes the escaping that the last of
    them exists to check, since ``convert_charrefs`` turns ``&lt;`` back into
    a character that would have opened a tag.
    """
    return _bounded(page, heading, _next_heading_of(heading))


def _bounded(page: str, heading: str, bound: str) -> str:
    """The markup between ``heading`` and the first match of ``bound``."""
    assert heading in page, f"no {heading} section on the page"
    after = page.split(heading, 1)[1]
    return re.split(bound, after, maxsplit=1)[0]


def table_after(page: str, heading: str) -> Table:
    """Parse ``heading``'s own table.

    Bounded at the next heading of *any* level, unlike
    :func:`section_after`.  A section holds its subsections, but a table
    rendered under a subheading is that subsection's, not this section's:
    a section that said ``not computed`` and a subsection that drew a
    table would otherwise answer with the subsection's, and every
    assertion about the section would pass against the wrong markup.
    """
    section = _bounded(page, heading, _ANY_HEADING)
    assert "<table>" in section, f"the {heading} section rendered no table"
    fragment = section.split("<table>", 1)[1].split("</table>", 1)[0]
    reader = _TableReader()
    reader.feed(f"<table>{fragment}</table>")
    reader.close()
    return Table(
        reader.rows["thead"], reader.rows["tbody"],
        reader.rows["tfoot"], reader.rows[""])
