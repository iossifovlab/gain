# pylint: disable=C0114,C0116
"""How the reference-genome page writes a share of a whole (gain#1086).

The page's three share tables -- the global nucleotides, each
chromosome's nucleotides and the global di-nucleotides -- go through the
one rule every other info-page table uses (gain#1057): a nonzero share
too small for two decimals is ``<0.01%`` and a share short of the whole
that rounds up to it is ``>99.99%``, decided on the counts.  Every test
here runs the real statistics build and reads the rendered page.
"""
import pathlib

import yaml
from gain.genomic_resources.implementations.reference_genome_impl import (
    ChromosomeStatistic,
    ReferenceGenomeImplementation,
)
from gain.genomic_resources.testing.builders import a_reference_genome
from gain.genomic_resources.testing.statistics import build_statistics

from tests.small.genomic_resources.info_page_html import table_after

NUCLEOTIDES = ["A", "T", "C", "G", "N"]


def render_page(root: pathlib.Path, fasta: str) -> str:
    resource = a_reference_genome().with_fasta(fasta).build_resource(root)
    build_statistics(resource)
    return ReferenceGenomeImplementation(resource).get_info()


def table_rows(page: str, heading: str) -> dict[str, list[str]]:
    """The table under ``heading``, its rows keyed by their first cell."""
    return {
        row[0]: row[1:] for row in table_after(page, heading).text
    }


def nucleotide_cells(page: str, row: str) -> dict[str, str]:
    """The nucleotide share cells of a chromosomes-table row."""
    shares = table_rows(page, "<h3>Chromosomes (")[row][1:]  # past length
    return dict(zip(NUCLEOTIDES, shares, strict=True))


def pair_cells(page: str) -> dict[str, str]:
    """Every di-nucleotide share cell, keyed by the pair."""
    rows = table_rows(page, "<h3>Bi-Nucleotide distribution:</h3>")
    return {
        first + second: rows[first][index]
        for first in NUCLEOTIDES
        for index, second in enumerate(NUCLEOTIDES)
    }


def test_a_single_rare_pair_is_not_written_as_an_absent_one(
    tmp_path: pathlib.Path,
) -> None:
    # 30,000 pairs, exactly one of them CG; GG never occurs.
    page = render_page(tmp_path, ">chr1\n" + "AC" * 15_000 + "G\n")

    pairs = pair_cells(page)

    assert pairs["CG"] == "<0.01%"
    assert pairs["GG"] == "0.00%"


def test_a_nucleotide_short_of_the_whole_is_not_written_as_all_of_it(
    tmp_path: pathlib.Path,
) -> None:
    # 20,001 of 20,002 bases are A: 99.995% would round up to 100.00%.
    page = render_page(tmp_path, ">chr1\n" + "A" * 20_001 + "C\n")

    global_row = nucleotide_cells(page, "Global")

    assert global_row["A"] == ">99.99%"
    assert global_row["C"] == "<0.01%"
    assert global_row["T"] == "0.00%"


def test_a_nucleotide_that_is_the_whole_genome_is_written_as_all_of_it(
    tmp_path: pathlib.Path,
) -> None:
    page = render_page(tmp_path, ">chr1\nAAAAAAAAAA\n")

    assert nucleotide_cells(page, "Global")["A"] == "100.00%"
    assert nucleotide_cells(page, "chr1")["A"] == "100.00%"
    assert pair_cells(page)["AA"] == "100.00%"


def test_a_pair_short_of_the_whole_is_not_written_as_all_of_it(
    tmp_path: pathlib.Path,
) -> None:
    # 20,001 of 20,002 pairs are AA: 99.995% would round up to 100.00%.
    page = render_page(tmp_path, ">chr1\n" + "A" * 20_002 + "C\n")

    pairs = pair_cells(page)

    assert pairs["AA"] == ">99.99%"
    assert pairs["AC"] == "<0.01%"


def test_each_chromosome_row_is_written_by_the_same_rule(
    tmp_path: pathlib.Path,
) -> None:
    # chr1 alone is all but one A; with chr2 beside it the genome is not.
    page = render_page(
        tmp_path,
        ">chr1\n" + "A" * 20_001 + "C\n>chr2\n" + "C" * 20_002 + "\n")

    chr1_row = nucleotide_cells(page, "chr1")

    assert chr1_row["A"] == ">99.99%"
    assert chr1_row["C"] == "<0.01%"
    assert chr1_row["G"] == "0.00%"


def test_a_genome_without_a_single_pair_leaves_the_pair_cells_empty(
    tmp_path: pathlib.Path,
) -> None:
    # One base is a nucleotide but no pair: there is nothing to share.
    page = render_page(tmp_path, ">chr1\nA\n")

    assert set(pair_cells(page).values()) == {""}
    assert nucleotide_cells(page, "Global")["A"] == "100.00%"


def test_the_stored_statistics_keep_their_format(
    tmp_path: pathlib.Path,
) -> None:
    # The shares are derived when the page is rendered, never stored.
    render_page(tmp_path, ">chr1\nACGTN\n")

    stored_global = yaml.safe_load(
        (tmp_path / "statistics/reference_genome_statistic.yaml").read_text())
    stored_chrom = yaml.safe_load(
        (tmp_path / "statistics/chr1_statistic.yaml").read_text())

    assert set(stored_global) == {
        "chromosomes", "length",
        "nucleotide_distribution", "bi_nucleotide_distribution",
    }
    assert set(stored_chrom) == {
        "chrom", "length", "nucleotide_counts", "nucleotide_pair_counts",
    }


def test_a_stored_chromosome_of_length_zero_loads() -> None:
    # No FASTA record the build reads is empty, but a stored statistic
    # can be; loading one must not divide by its zero length.
    stored = ChromosomeStatistic("chrZ").serialize()

    loaded = ChromosomeStatistic.deserialize(stored)

    assert set(loaded.nucleotide_distribution.values()) == {0.0}
