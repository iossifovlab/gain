"""The Chromosome lengths section of a genomic score's page (gain#1579).

What the last repair stored in ``statistics/chrom_lengths.json``, laid
out per contig with one column per source, so a reader can put a
tabix score's probed bound beside its genome's true length and see
which of the score's contigs that genome does not list.  Read behind
the same gate the Coverage section reads (gain#1578): a stored file
that is not ``CURRENT`` is never shown, and the render opens no table.
"""

from __future__ import annotations

import logging
import pathlib

import pytest
import pytest_mock
from gain.genomic_resources.implementations.genomic_scores_impl import (
    GenomicScoreImplementation,
    build_score_implementation_from_resource,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing.info_page_fixtures import (
    COVERAGE_RESOURCE_ID,
    a_coverage_repo,
)
from gain.utils.chromosome_order import natural_chromosome_key

from .info_page_html import section_after, sort_keys, table_after
from .test_cli_stats_chrom_lengths import resource_stats
from .test_genomic_scores_impl_chrom_lengths import set_label
from .test_genomic_scores_impl_derived_files import resynced

HEADING = "<h2>Chromosome lengths</h2>"


def _repaired(
    where: pathlib.Path, resource_id: str,
) -> tuple[GenomicScoreImplementation, GenomicResourceRepo]:
    """The resource repaired -- statistics and stored lengths -- and a
    fresh view of the repository as it is on disk now."""
    resource_stats(where, resource_id)
    return resynced(where, resource_id)


def test_a_repaired_labelled_tabix_score_lists_every_source_per_contig(
    tmp_path: pathlib.Path,
) -> None:
    """The coverage fixture: a tabix score over chr1, chr2 and chr10
    labelled with a genome that lists chr1 (100) and chr2 (50).  Its
    file holds the genome's lengths and the probe's bounds; the table
    shows both, contigs in table order, sources best first."""
    a_coverage_repo(tmp_path)
    impl, repo = _repaired(tmp_path, COVERAGE_RESOURCE_ID)

    table = table_after(impl.get_info(repo=repo), HEADING)

    # Own text: the contig cell of an unlisted contig nests its marker,
    # which the next test is about.
    rows = table.head + table.rows
    assert [[cell.own_text for cell in row] for row in rows] == [
        ["Chromosome", "reference_genome", "tabix_estimate"],
        ["chr1", "100", "12"],
        ["chr2", "50", "12"],
        ["chr10", "", "3"],
    ]


def test_a_contig_the_genome_does_not_list_is_marked(
    tmp_path: pathlib.Path,
) -> None:
    """chr10 is the score's and not the genome's: its row carries the
    marker, its genome cell is empty and has nothing to sort on -- no
    key at all, so the sorter puts it last whichever way the column
    points -- while the listed contigs carry no marker."""
    a_coverage_repo(tmp_path)
    impl, repo = _repaired(tmp_path, COVERAGE_RESOURCE_ID)

    page = impl.get_info(repo=repo)

    table = table_after(page, HEADING)
    assert [row[0].own_text for row in table.rows] == ["chr1", "chr2", "chr10"]
    assert [
        "not in genome" in row[0].text for row in table.rows
    ] == [False, False, True]
    genome_cells = table.column("reference_genome")
    assert [cell.sort_value for cell in genome_cells] == ["100", "50", None]
    assert section_after(page, HEADING).count('class="unlisted-contig"') == 1


def test_every_header_declares_how_its_column_compares(
    tmp_path: pathlib.Path,
) -> None:
    """The table opts into the sorter: the contig column on its
    natural-order key, every source column on the number -- a length
    column that compared as text would put 12 before 3."""
    a_coverage_repo(tmp_path)
    impl, repo = _repaired(tmp_path, COVERAGE_RESOURCE_ID)

    table = table_after(impl.get_info(repo=repo), HEADING)

    assert [
        (cell.text, cell.attrs.get("data-sort")) for cell in table.head[0]
    ] == [
        ("Chromosome", "text"),
        ("reference_genome", "number"),
        ("tabix_estimate", "number"),
    ]
    assert sort_keys(table.column("tabix_estimate")) == ["12", "12", "3"]


def test_sorting_the_chromosome_keys_reproduces_natural_order(
    tmp_path: pathlib.Path,
) -> None:
    """The browser's plain ``<`` over the keys yields chr1, chr2, chr10
    -- which sorting the names would not -- and the marker nested in
    chr10's cell does not leak into its key."""
    a_coverage_repo(tmp_path)
    impl, repo = _repaired(tmp_path, COVERAGE_RESOURCE_ID)

    cells = table_after(impl.get_info(repo=repo), HEADING).column("Chromosome")

    names = [cell.own_text for cell in cells]
    keys = sort_keys(cells)
    assert names == ["chr1", "chr2", "chr10"]
    assert [n for _, n in sorted(zip(keys, names, strict=True))] == names
    assert sorted(names) != names
    assert keys == [natural_chromosome_key(name) for name in names]


def _lengths_log_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Every INFO-or-worse record about the stored lengths."""
    return [
        record.getMessage() for record in caplog.records
        if record.levelno >= logging.INFO
        and "chromosome lengths" in record.getMessage()
    ]


def test_an_unrepaired_score_reads_not_computed_and_opens_no_table(
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockerFixture,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No stored file: the heading stays, under it "not computed" and
    no table -- a repair would fill it -- and the render neither opens
    the table to fill it live nor reports the missing file, which is
    ``repo-repair``'s to report."""
    repo = a_coverage_repo(tmp_path)
    impl = build_score_implementation_from_resource(
        repo.get_resource(COVERAGE_RESOURCE_ID))
    opened = mocker.spy(impl.score, "open")

    with caplog.at_level(logging.INFO):
        page = impl.get_info(repo=repo)

    section = section_after(page, HEADING)
    assert "<p>not computed</p>" in section
    assert "<table>" not in section
    opened.assert_not_called()
    assert _lengths_log_lines(caplog) == []


def test_a_stale_file_is_not_shown(
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockerFixture,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The label re-pointed since the repair: the file on disk still
    holds the old genome's lengths, and showing them would say the
    wrong genome's numbers under the new label.  Not computed, as if
    there were no file -- and, as then, no table opened and nothing
    said."""
    a_coverage_repo(tmp_path)
    _repaired(tmp_path, COVERAGE_RESOURCE_ID)
    set_label(tmp_path, COVERAGE_RESOURCE_ID, "reference_genome", "other")
    impl, repo = resynced(tmp_path, COVERAGE_RESOURCE_ID)
    opened = mocker.spy(impl.score, "open")

    with caplog.at_level(logging.INFO):
        page = impl.get_info(repo=repo)

    section = section_after(page, HEADING)
    assert "<p>not computed</p>" in section
    assert "<table>" not in section
    opened.assert_not_called()
    assert _lengths_log_lines(caplog) == []
