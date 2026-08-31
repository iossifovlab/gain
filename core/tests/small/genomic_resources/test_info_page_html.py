# pylint: disable=C0114,C0116,W0212,W0621
"""What the shared info-page reader promises the statistics tests.

These pin the reader against markup written out literally here, so a
failure names the reader rather than whichever template last changed.
The statistics tests that use it feed it real rendered pages, which is
what keeps the two in step -- both halves are needed: markup written
here cannot drift with the templates, and a page rendered there cannot
isolate which of the reader's rules broke.
"""
from __future__ import annotations

import pytest

from tests.small.genomic_resources.info_page_html import (
    section_after,
    table_after,
)

#: The shape the ``<h3>`` tables render: a ``<thead>`` and then bare rows,
#: with no ``<tbody>`` wrapping them.  The per-chromosome tables above them
#: do wrap theirs, so a reader that only knows one of the two shapes reads
#: half the page's tables as empty.
_NO_TBODY_PAGE = """
<h3>Allele classes</h3>
<table>
    <thead><tr><th>Class</th><th>Alleles</th></tr></thead>
    <tr><td>substitution</td><td>6</td></tr>
    <tr><td>insertion</td><td>1</td></tr>
</table>
<h2 style="margin-top: 70px;">Files</h2>
<table><tr><td>never reached</td></tr></table>
"""


def test_a_column_of_a_table_with_no_tbody_is_read() -> None:
    # Reading only <tbody> returns [] here -- and returns it quietly, so
    # every assertion about these tables passes while checking nothing.
    table = table_after(_NO_TBODY_PAGE, "<h3>Allele classes</h3>")

    assert [cell.text for cell in table.column("Class")] == [
        "substitution", "insertion"]


#: A count carrying gain#988's muted share beneath it, as the substitution
#: matrix renders one.  The share is a nested element, so the cell's whole
#: text runs the two together.
_MUTED_SHARE_PAGE = """
<h3>Substitution matrix</h3>
<table>
    <thead><tr><th>ref &rarr; alt</th><th>A</th></tr></thead>
    <tr><th>A</th><td>1<div class="text-muted">33.33%</div></td></tr>
</table>
"""


def test_a_count_is_readable_apart_from_the_share_nested_under_it() -> None:
    # Run together, cell.text reads "133.33%" -- a count of 1 and a share
    # of 33.33% -- which is neither number and matches no assertion about
    # either.
    table = table_after(_MUTED_SHARE_PAGE, "<h3>Substitution matrix</h3>")

    count = table.rows[0][1]
    assert count.own_text == "1"
    assert count.text == "133.33%"


#: A section that rendered no table at all, followed by one that did.  The
#: Files heading carries a style attribute, as it does on a real page.
_NOT_COMPUTED_PAGE = """
<h2>Fragments</h2>
<p>not computed</p>
<img src="statistics/coverage_fragment_lengths.png">
<h2 style="margin-top: 70px;">Files</h2>
<table><tr><td>never reached</td></tr></table>
"""


def test_a_section_that_rendered_no_table_is_still_readable_as_markup() -> None:
    # table_after refuses this section, correctly -- but the assertions
    # about it are that it says "not computed" and renders one image, and
    # those need the markup itself.
    section = section_after(_NOT_COMPUTED_PAGE, "<h2>Fragments</h2>")

    assert "not computed" in section
    assert section.count("coverage_fragment_lengths.png") == 1
    # Bounded at the next heading, style attribute and all: a section that
    # ran on would pick up the Files table below it.
    assert "never reached" not in section


def test_a_table_is_still_refused_for_a_section_that_rendered_none() -> None:
    with pytest.raises(AssertionError, match="rendered no table"):
        table_after(_NOT_COMPUTED_PAGE, "<h2>Fragments</h2>")


#: A share below the display resolution renders an escaped "<".  Raw, it
#: would open a bogus tag and the browser would swallow the cell.
_ESCAPED_PAGE = """
<h3>Allele classes</h3>
<table>
    <thead><tr><th>Class</th><th>% of alleles</th></tr></thead>
    <tr><td>complex</td><td>&lt;0.01%</td></tr>
</table>
"""


def test_only_the_markup_reader_can_see_an_escaped_value() -> None:
    """Why an assertion about escaping must not be ported to parsed cells.

    This holds by construction today -- ``section_after`` slices the page and
    never parses it.  It is pinned anyway: routing it through the parser
    "for consistency" would leave the escaping tests passing against
    unescaped text, which is the one thing they exist to catch.
    """
    section = section_after(_ESCAPED_PAGE, "<h3>Allele classes</h3>")
    cells = table_after(_ESCAPED_PAGE, "<h3>Allele classes</h3>").rows[0]

    assert "&lt;0.01%" in section
    # The parser resolves the entity, so the cell can no longer tell a
    # rendered "&lt;" from a raw "<".
    assert cells[1].text == "<0.01%"


#: An <h2> section with TWO <h3> subsections inside it, then the next <h2>.
#: The Alleles section is this shape: a per-chromosome table, then Allele
#: classes, the substitution matrix and more beneath it.  Two subsections,
#: not one: with a single <h3> the next <h2> is what ends it either way, and
#: an <h3> wrongly running past its successor would go unnoticed.
_NESTED_PAGE = """
<h2>Alleles</h2>
<table>
    <thead><tr><th>Chromosome</th></tr></thead>
    <tbody><tr><td>chr1</td></tr></tbody>
</table>
<h3>Allele classes</h3>
<p>ts/tv 1.50</p>
<h3>Substitution matrix</h3>
<p>in the next subsection</p>
<h2 style="margin-top: 70px;">Files</h2>
<p>not in the section</p>
"""


def test_a_section_holds_its_own_subsections() -> None:
    # A heading's section runs to the next heading of the SAME level or
    # higher, not to the next heading of any level: the ts/tv ratio and
    # the class tables are part of Alleles, and an assertion about them
    # is an assertion about that section.
    section = section_after(_NESTED_PAGE, "<h2>Alleles</h2>")

    assert "ts/tv 1.50" in section
    assert "in the next subsection" in section
    assert "not in the section" not in section


def test_a_subsection_stops_at_the_next_subsection() -> None:
    # The mirror of the above, and the half a single-subsection fixture
    # cannot pin: an <h3> is bounded by the next <h3> and not only by the
    # next <h2>, so a value can be tied to the subsection that renders it.
    section = section_after(_NESTED_PAGE, "<h3>Allele classes</h3>")

    assert "ts/tv 1.50" in section
    assert "in the next subsection" not in section


#: A section that rendered no table, whose SUBSECTION did.  The shape the
#: templates do not currently produce -- every <h3> sits inside the same
#: conditional as its section's table -- but the one a refusal has to
#: survive, because falling through to a subsection's table is exactly the
#: silent widening the regex splitters were retired for.
_TABLELESS_WITH_A_TABLED_SUBSECTION = """
<h2>Alleles</h2>
<p>not computed</p>
<h3>Allele classes</h3>
<table>
    <thead><tr><th>Class</th></tr></thead>
    <tr><td>belongs to the subsection</td></tr>
</table>
<h2 style="margin-top: 70px;">Files</h2>
"""


def test_a_table_in_a_subsection_is_not_the_sections_own_table() -> None:
    # A table belongs to the section it is rendered in.  Reading the whole
    # section's markup still spans the subsection -- that is what a section
    # holding its subsections means -- but asking for THIS section's table
    # must not answer with a table one level down.
    assert "belongs to the subsection" in section_after(
        _TABLELESS_WITH_A_TABLED_SUBSECTION, "<h2>Alleles</h2>")

    with pytest.raises(AssertionError, match="rendered no table"):
        table_after(_TABLELESS_WITH_A_TABLED_SUBSECTION, "<h2>Alleles</h2>")


#: A cell holding a void element.  <br> and <img> never fire an end tag, so
#: a reader that counts every start tag as one level deeper never comes back
#: up, and reads the rest of the cell as though it were nested.
_VOID_ELEMENT_PAGE = """
<h3>Void elements</h3>
<table>
    <thead><tr><th>Cell</th></tr></thead>
    <tr><td>1<br>2</td><td><img src="x.png">42</td></tr>
</table>
"""


def test_a_void_element_does_not_swallow_the_rest_of_the_cell() -> None:
    # The failure is silent: own_text comes back short, or empty, and an
    # assertion about a count reads a value the cell never rendered.
    row = table_after(_VOID_ELEMENT_PAGE, "<h3>Void elements</h3>").rows[0]

    assert row[0].own_text == "12"
    assert row[1].own_text == "42"
