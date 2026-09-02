# pylint: disable=C0114,C0116,W0212,W0621
"""The markup contract the info page's client-side sorter reads.

The sorter (``sortable_table.jinja``) is opt-in through two attributes
and nothing else: ``data-sort`` on a ``<th>`` says the column sorts and
how to compare it, ``data-sort-value`` on a ``<td>`` carries the key.
CI has no JS runtime, so what is testable here is the contract the
templates emit.  The sorter's own click behaviour is driven in a real
browser by the ``info_pages_e2e`` Playwright project, against a page it
generates from the same Coverage fixture this file imports --
:mod:`gain.genomic_resources.testing.info_page_fixtures`, which is where
that fixture lives so both projects can reach it.

That makes these tests the only thing standing between a template edit
and a silently dead sorter, so they assert the contract from the
rendered page rather than by reading the templates: a ``data-sort`` on a
header that the browser never pairs with a ``<td>`` key is exactly the
failure a source-level grep would miss.

The ``<tbody>``/``<tfoot>`` split is load-bearing rather than tidiness.
The sorter reorders ``tbody > tr``; the ``all chromosomes`` total sits
in ``<tfoot>``, so no comparator, and no bug in one, can float the
total into the middle of the data.  ``Table.loose`` in
``info_page_html`` exists to make the old shape -- rows sitting directly
under ``<table>`` -- visible to an assertion instead of quietly
reappearing, and ``test_every_per_chromosome_table_pins_its_total_in_a_tfoot``
below asserts these tables keep none.

The fixtures here look like the ones in
``test_info_page_chromosome_order.py`` and deliberately are not: the
Coverage score's counts are 9, 10 and 2 so that text and numeric order
disagree, and its genome resolves only two of the three contigs so one
row has no fraction.  Sharing them would mean parameterising that file's
builders for this file's traps.
"""
from __future__ import annotations

import pathlib
from collections.abc import Callable

import pytest
from gain.genomic_resources.implementations.genomic_scores_impl import (
    build_score_implementation_from_resource,
    scan,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing.builders import (
    a_fragment_score,
    a_grr,
    a_position_score,
    a_reference_genome,
    an_allele_score,
)
from gain.genomic_resources.testing.info_page_fixtures import (
    COVERED_POSITIONS,
    a_coverage_repo,
)

from tests.small.genomic_resources.info_page_html import (
    sort_keys,
    table_after,
)


def an_allele_repo(where: pathlib.Path) -> GenomicResourceRepo:
    """A three-contig allele score."""
    return (
        a_grr()
        .with_resource(
            "scores/alleles",
            an_allele_score()
            .with_score("score", "float")
            .with_data(
                """
                chrom  pos_begin  reference  alternative  score
                chr1   10         A          G            0.1
                chr2   10         A          C            0.2
                chr10  10         A          T            0.3
                """)
            .with_tabix())
        .build_repo(where)
    )


def a_fragment_repo(where: pathlib.Path) -> GenomicResourceRepo:
    """A three-contig fragment score."""
    return (
        a_grr()
        .with_resource(
            "scores/fragments",
            a_fragment_score()
            .with_score("score", "float")
            .with_data(
                """
                chrom  pos_begin  pos_end  score
                chr1   1          5        0.1
                chr2   1          5        0.2
                chr10  1          5        0.3
                """)
            .with_tabix())
        .build_repo(where)
    )


#: Every per-chromosome table, by the resource that renders it and the
#: heading that introduces it.
PER_CHROMOSOME_TABLES = [
    ("scores/coverage", "<h2>Coverage</h2>"),
    ("scores/alleles", "<h2>Alleles</h2>"),
    ("scores/fragments", "<h2>Fragments</h2>"),
]

#: The two tables that opt into sorting.  Written out rather than sliced
#: off the list above, so that reordering that list cannot silently
#: re-point these assertions at a table that does not sort.
SORTABLE_TABLES = [
    ("scores/coverage", "<h2>Coverage</h2>"),
    ("scores/alleles", "<h2>Alleles</h2>"),
]

#: The contigs every fixture here carries, in natural order.  Local
#: rather than imported with the Coverage fixture: the allele and
#: fragment repositories below declare these contigs themselves.
CONTIGS = ["chr1", "chr2", "chr10"]

#: Which repository each of those resources comes out of.
REPO_BUILDERS: dict[str, Callable[[pathlib.Path], GenomicResourceRepo]] = {
    "scores/coverage": a_coverage_repo,
    "scores/alleles": an_allele_repo,
    "scores/fragments": a_fragment_repo,
}

#: How each per-chromosome column compares.  Chromosome sorts on the
#: natural-order key, which is a string; every other column is a count
#: or a fraction, and must not compare lexicographically.
SORT_KINDS = {
    "Chromosome": "text",
    "Covered positions": "number",
    "Covered %": "number",
    "Segments": "number",
    "Alleles": "number",
    # One share column per allele class (gain#1118).  Written out
    # rather than derived from CLASS_NAMES: a column added to the
    # template must be added HERE too, deliberately, or this map stops
    # being an independent statement of what the table emits.
    "substitution %": "number",
    "insertion %": "number",
    "deletion %": "number",
    "complex %": "number",
    "other %": "number",
}


def built_page(repo: GenomicResourceRepo, resource_id: str) -> str:
    """The resource's info page, statistics built, genome resolvable.

    Through the factory rather than :class:`GenomicScoreImplementation`
    directly, because only the kind-specific implementation renders the
    Fragments section.  ``repo=`` is what lets the Coverage denominator
    resolve the labelled genome, which is the rung that gives one row a
    fraction and another none.
    """
    resource = repo.get_resource(resource_id)
    scan.do_noregion_histograms(resource)
    return build_score_implementation_from_resource(
        repo.get_resource(resource_id)).get_info(repo=repo)


@pytest.fixture(scope="module")
def pages(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, str]:
    """Every fixture page, rendered once for the whole module.

    The pages, not the repositories: ``built_page`` writes statistics
    into the repository it is given, so sharing a repo would share
    mutable state.  Every test here only reads the rendered string, so
    handing out the string keeps them independent while paying the
    build cost three times instead of seventeen.
    """
    return {
        resource_id: built_page(
            build(tmp_path_factory.mktemp(resource_id.replace("/", "-"))),
            resource_id)
        for resource_id, build in REPO_BUILDERS.items()
    }


@pytest.mark.parametrize(("resource_id", "heading"), PER_CHROMOSOME_TABLES)
def test_every_per_chromosome_table_pins_its_total_in_the_thead(
    pages: dict[str, str],
    resource_id: str,
    heading: str,
) -> None:
    """Data in ``<tbody>``, the total as a second ``<thead>`` row.

    It moved out of ``<tfoot>`` (gain#1118) so that a reader scrolling
    a few hundred contigs keeps the total in view: the container
    already makes ``thead`` sticky, and a second row inside it inherits
    that for nothing.

    The guarantee that the total cannot be reordered is unchanged and
    needs no JavaScript: the sorter reads its headers from
    ``tHead.rows[0]`` and only ever reorders ``tBodies[0].rows``, so a
    second head row is outside both.  What ``<tfoot>`` used to give,
    ``<thead>`` gives for the same structural reason.

    Fragments is here despite not opting into sorting: the structure is
    what makes opting it in later two attributes and nothing else, and
    leaving one of three sibling tables in the old shape is how this
    drifts back.
    """
    table = table_after(pages[resource_id], heading)

    assert [row[0].text for row in table.body] == CONTIGS
    assert len(table.head) == 2, "the total is the second <thead> row"
    assert table.head[1][0].text == "all chromosomes"
    assert table.foot == []
    assert table.loose == []


@pytest.mark.parametrize(("resource_id", "heading"), PER_CHROMOSOME_TABLES)
def test_no_total_row_cell_is_announced_as_a_column_header(
    pages: dict[str, str],
    resource_id: str,
    heading: str,
) -> None:
    """The pinned row is data in the header, not a second header row.

    Its cells are ``<td>``: a ``<th>`` there would be announced as a
    column header for the rows beneath it, and would be eligible for
    the ``aria-sort`` the sorter sets -- which it clears by querying
    ``thead th[aria-sort]``, so a ``<th>`` total could take the marker
    off the column that is actually sorted.
    """
    table = table_after(pages[resource_id], heading)

    assert all(cell.tag == "td" for cell in table.head[1]), (
        f"{heading} pins its total with <th> cells")


@pytest.mark.parametrize(("resource_id", "heading"), PER_CHROMOSOME_TABLES)
def test_no_total_is_marked_with_the_dead_coverage_global_class(
    pages: dict[str, str],
    resource_id: str,
    heading: str,
) -> None:
    """``<tfoot>`` is the selector for a total now, so the class goes.

    It never had a CSS rule behind it, and it marked the Coverage and
    Fragments totals but not the Alleles one -- so anyone grepping for
    "the totals rows" found two of the three.
    """
    assert "coverage-global" not in pages[resource_id], heading


def test_a_section_that_rendered_no_table_is_refused(
    pages: dict[str, str],
) -> None:
    """The helper every other test here leans on must not silently drift.

    An allele score's Indel lengths subsection renders images or a
    sentence, never a table.  If ``table_after`` scanned past the end of
    the section it would return the Files table at the foot of the page,
    and an assertion about a table that was never rendered would pass
    against that one instead.
    """
    with pytest.raises(AssertionError, match="rendered no table"):
        table_after(pages["scores/alleles"], "<h3>Indel lengths</h3>")


@pytest.mark.parametrize(("resource_id", "heading"), SORTABLE_TABLES)
def test_every_sortable_header_declares_how_its_column_compares(
    pages: dict[str, str],
    resource_id: str,
    heading: str,
) -> None:
    """Every column of an opted-in table sorts, and knows its comparison.

    Asserted over whatever headers the table actually rendered rather
    than a fixed list: Coverage's ``Covered %`` and ``Segments`` columns
    appear only when the statistic carries them, and a column that
    quietly arrived without a ``data-sort`` would be a dead header.
    """
    table = table_after(pages[resource_id], heading)

    assert table.head[0], "the table rendered no headers at all"
    for cell in table.head[0]:
        assert cell.attrs.get("data-sort") == SORT_KINDS[cell.text], cell


@pytest.mark.parametrize(("resource_id", "heading"), SORTABLE_TABLES)
def test_sorting_the_chromosome_keys_reproduces_natural_order(
    pages: dict[str, str],
    resource_id: str,
    heading: str,
) -> None:
    """The browser's plain ``<`` over the keys must yield chr1, chr2, chr10.

    This models what the sorter actually does -- order the rows by the
    key strings -- rather than asserting the key equals what
    ``natural_chromosome_key`` returns, which would restate
    iossifovlab/gain#983's own test.  The second assertion is why the key has to
    exist at all: sorting the visible names instead puts chr10 second.
    """
    cells = table_after(pages[resource_id], heading).column("Chromosome")

    names = [cell.text for cell in cells]
    keys = sort_keys(cells)
    assert names == CONTIGS
    by_key = [name for _, name in sorted(zip(keys, names, strict=True))]
    assert by_key == CONTIGS
    assert sorted(names) != CONTIGS


def test_a_count_column_carries_the_number_not_the_rendered_text(
    pages: dict[str, str],
) -> None:
    """``9`` sorts before ``10``, which is not what comparing text does.

    The header says ``data-sort="number"`` so the sorter parses the key
    with ``parseFloat``; this pins the other half of that contract, that
    the key is there to be parsed.  Sorting the rendered text instead
    would put 10 first, and the page would look plausibly sorted.
    """
    cells = table_after(
        pages["scores/coverage"], "<h2>Coverage</h2>",
    ).column("Covered positions")

    assert [cell.text for cell in cells] == [str(n) for n in COVERED_POSITIONS]
    keys = sort_keys(cells)
    assert sorted(float(key) for key in keys) == sorted(COVERED_POSITIONS)
    assert sorted(keys) != [str(n) for n in sorted(COVERED_POSITIONS)]


def test_a_class_share_sorts_on_the_fraction_not_the_rendered_share(
    pages: dict[str, str],
) -> None:
    """The share column's key is the number, never the text it shows.

    The rendered strings do not order as their values do:
    :func:`percentage_of` writes a floor of ``<0.01%`` and a ceiling of
    ``>99.99%``, and ``<`` and ``>`` sort either side of the digits, so
    a column comparing text would put the two rarest and commonest
    classes in the wrong places specifically.
    """
    cells = table_after(
        pages["scores/alleles"], "<h2>Alleles</h2>").column("substitution %")

    assert [cell.text for cell in cells] == ["100.00%"] * 3
    assert [float(key) for key in sort_keys(cells)] == [1.0] * 3


def test_a_class_share_titles_itself_with_its_exact_count(
    pages: dict[str, str],
) -> None:
    """The share rounds; the title does not.

    This is what replaced the "Allele classes" table's Alleles column
    (gain#1118), and it is strictly more: that column carried one
    resource-wide count per class, while these carry one per class per
    chromosome, which is the number the share on the same cell rounds
    off.
    """
    table = table_after(pages["scores/alleles"], "<h2>Alleles</h2>")

    assert [
        cell.attrs.get("title") for cell in table.column("substitution %")
    ] == ["1 alleles"] * 3
    assert [
        cell.attrs.get("title") for cell in table.column("insertion %")
    ] == ["0 alleles"] * 3


def test_covered_percent_sorts_by_the_fraction_it_renders(
    pages: dict[str, str],
) -> None:
    """Display formatting stays out of the comparator.

    ``9.00%`` and ``20.00%`` order one way as text and the other way as
    numbers, so this is the column where keeping the key separate from
    the rendering earns its place.
    """
    cells = table_after(
        pages["scores/coverage"], "<h2>Coverage</h2>").column("Covered %")

    shown = [cell.text for cell in cells[:2]]
    assert shown == ["9.00%", "20.00%"]
    keys = [cell.sort_value for cell in cells[:2]]
    assert [float(key) for key in keys if key is not None] == pytest.approx(
        [9 / 100, 10 / 50])
    by_key = [name for _, name in sorted(zip(keys, shown, strict=True))]
    assert by_key == ["9.00%", "20.00%"]
    assert sorted(shown) == ["20.00%", "9.00%"]


def test_a_row_with_no_denominator_carries_no_covered_percent_key(
    pages: dict[str, str],
) -> None:
    """An absent attribute, not an empty one.

    chr10 resolves no length from the labelled genome, so its fraction
    is None.  The sorter reads absent and empty alike as "no value", so
    what this pins is the markup saying so: a cell with nothing to sort
    on carries no key, rather than a key that happens to be blank.
    """
    cells = table_after(
        pages["scores/coverage"], "<h2>Coverage</h2>").column("Covered %")

    assert cells[2].text == ""
    assert "data-sort-value" not in cells[2].attrs


#: Tables that deliberately do not sort.  The substitution matrix's row
#: headers are ``<th>``, not data, so a reorder would strand them; the
#: Allele classes table is four rows; Fragments is opted out for now and
#: gets the structure only.
#: gain#1118 removed the "Allele classes" table -- its shares became the
#: Alleles table's pinned total row and its counts the hover titles --
#: so the tables that render without opting into sorting are now two.
OPTED_OUT_TABLES = [
    ("scores/alleles", "<h3>Substitution matrix</h3>"),
    ("scores/fragments", "<h2>Fragments</h2>"),
]


@pytest.mark.parametrize(("resource_id", "heading"), OPTED_OUT_TABLES)
def test_an_opted_out_table_carries_neither_sort_attribute(
    pages: dict[str, str],
    resource_id: str,
    heading: str,
) -> None:
    """Opting out is the absence of both attributes, and stays that way.

    The sorter is inert without them, so this is what keeps "not
    sortable" a decision rather than an accident of nobody having got
    round to it -- and it is the assertion that fails if a later edit
    copies a sortable ``<th>`` into one of these tables.
    """
    table = table_after(pages[resource_id], heading)

    for row in table.head + table.body + table.foot + table.loose:
        for cell in row:
            assert "data-sort" not in cell.attrs, cell
            assert "data-sort-value" not in cell.attrs, cell


def test_the_untouched_contig_rollup_sits_in_the_tfoot(
    tmp_path: pathlib.Path,
) -> None:
    """The roll-up stays in ``<tfoot>``, and is now alone there.

    It did NOT follow the total into the sticky header (gain#1118),
    and the distinction is the point: a total summarises the rows
    above it, while this describes what is *not* in the table at all.
    Pinning it would also make the sticky header three rows deep and
    eat the 500px the data scrolls in.

    No fixture above emits one -- every one of their genomes is a
    subset of what its score covers -- so this builds its own: a chr1
    score against a genome that also has chr2.

    In ``<tbody>`` the row would be reordered with the data, and it
    carries no ``data-sort-value`` on any cell, so ``compare`` would
    sink it to the bottom on every sort: a summary line floating among
    the contigs.
    """
    repo = (
        a_grr()
        .with_resource(
            "scores/rollup",
            a_position_score()
            .with_score("score", "float")
            .with_data(
                """
                chrom  pos_begin  pos_end  score
                chr1   1          9        0.1
                """)
            .with_tabix()
            .with_labels(reference_genome="genomes/g1041r"))
        .with_resource(
            "genomes/g1041r",
            a_reference_genome()
            .with_chromosome("chr1", "A" * 100)
            .with_chromosome("chr2", "C" * 300))
        .build_repo(tmp_path)
    )

    table = table_after(
        built_page(repo, "scores/rollup"), "<h2>Coverage</h2>")

    assert [row[0].text for row in table.body] == ["chr1"]
    assert [row[0].text for row in table.foot] == [
        "1 contig with no values (300 bp)"]
    assert table.head[1][0].text == "all chromosomes", (
        "the total moved to the sticky header; only the roll-up is left "
        "in the foot")
    assert table.loose == []
