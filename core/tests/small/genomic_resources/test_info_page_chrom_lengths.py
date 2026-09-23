"""The Chromosome lengths section of a genomic score's page (gain#1579).

What the last repair stored in ``statistics/chrom_lengths.json``, laid
out per contig with one column per source, so a reader can put a
tabix score's probed bound beside its genome's true length and see
which of the score's contigs that genome does not list.  Read behind
the same gate the Coverage section reads (gain#1578): a stored file
that is not ``CURRENT`` is never shown, and the render opens no table.

What is pinned here is the section's own contract.  Its sort contract
-- which headers carry ``data-sort``, that the contig keys reproduce
natural order -- is the sorter's, and lives with every other opted-in
table in ``test_info_page_sortable_tables.py``.
"""

from __future__ import annotations

import json
import logging
import pathlib
from collections.abc import Callable

import pytest
import pytest_mock
from gain.genomic_resources.genomic_scores.chrom_lengths import (
    CHROM_LENGTHS_FILE,
)
from gain.genomic_resources.implementations.genomic_scores_impl import (
    build_score_implementation_from_resource,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing.info_page_fixtures import (
    COVERAGE_RESOURCE_ID,
    a_coverage_repo,
)

from .info_page_html import section_after, table_after
from .test_coverage_stored_first import (
    SCORE as BIGWIG,
)
from .test_coverage_stored_first import (
    a_bigwig_repo_with_genome,
)
from .test_genomic_scores_impl_chrom_lengths import set_label
from .test_genomic_scores_impl_derived_files import repaired, resynced
from .test_info_page_sortable_tables import a_fragment_repo, an_allele_repo

HEADING = "<h2>Chromosome lengths</h2>"


def _repaired_coverage_page(where: pathlib.Path) -> str:
    """The coverage fixture's page after a repair: a tabix score over
    chr1, chr2 and chr10 labelled with a genome that lists chr1 (100)
    and chr2 (50)."""
    a_coverage_repo(where)
    impl, repo = repaired(where, COVERAGE_RESOURCE_ID)
    return impl.get_info(repo=repo)


def test_a_repaired_labelled_tabix_score_lists_every_source_per_contig(
    tmp_path: pathlib.Path,
) -> None:
    """The file holds the genome's lengths and the probe's bounds; the
    table shows both, contigs in table order, sources best first --
    between the Scores table every kind renders and the kind's own
    sections, since the lengths are about the resource, not its
    statistic."""
    page = _repaired_coverage_page(tmp_path)

    assert page.index("<h2>Scores") < page.index(HEADING) < page.index(
        "<h2>Coverage</h2>")
    table = table_after(page, HEADING)
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
    """chr10 is the score's and not the genome's: its contig cell
    carries the marker, its genome cell is empty and has nothing to
    sort on -- no key at all, so the sorter puts it last whichever way
    the column points -- while the listed contigs carry no marker."""
    table = table_after(_repaired_coverage_page(tmp_path), HEADING)

    assert [row[0].own_text for row in table.rows] == ["chr1", "chr2", "chr10"]
    assert [
        "not in genome" in row[0].text for row in table.rows
    ] == [False, False, True]
    genome_cells = table.column("reference_genome")
    assert [cell.sort_value for cell in genome_cells] == ["100", "50", None]


def _assert_not_computed(section: str) -> None:
    """The heading with "not computed" beneath it and no table: a
    repair would fill it, and the heading says so."""
    assert "<p>not computed</p>" in section
    assert "<table>" not in section


def _lengths_log_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Every INFO-or-worse record about the stored lengths."""
    return [
        record.getMessage() for record in caplog.records
        if record.levelno >= logging.INFO
        and "chromosome lengths" in record.getMessage()
    ]


def _never_repaired(where: pathlib.Path) -> None:
    """No stored file: the state of every resource until its next
    repair."""


def _repaired_then_relabelled(where: pathlib.Path) -> None:
    """The label re-pointed since the repair: the file on disk still
    holds the old genome's lengths, and showing them would say the
    wrong genome's numbers under the new label."""
    repaired(where, COVERAGE_RESOURCE_ID)
    set_label(where, COVERAGE_RESOURCE_ID, "reference_genome", "other")


@pytest.mark.parametrize(
    "leave_not_current", [_never_repaired, _repaired_then_relabelled],
    ids=["absent", "stale"])
def test_a_file_that_is_not_current_reads_not_computed_and_opens_no_table(
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockerFixture,
    caplog: pytest.LogCaptureFixture,
    leave_not_current: Callable[[pathlib.Path], None],
) -> None:
    """Not computed, whether there is no file or a stale one -- and the
    render neither opens the table to fill the gap live nor reports the
    state, which is ``repo-repair``'s to report."""
    a_coverage_repo(tmp_path)
    leave_not_current(tmp_path)
    impl, repo = resynced(tmp_path, COVERAGE_RESOURCE_ID)
    opened = mocker.spy(impl.score, "open")

    with caplog.at_level(logging.INFO):
        page = impl.get_info(repo=repo)

    _assert_not_computed(section_after(page, HEADING))
    opened.assert_not_called()
    assert _lengths_log_lines(caplog) == []


def test_a_contig_the_table_could_not_measure_shows_the_reason(
    tmp_path: pathlib.Path,
) -> None:
    """The table's block carries the reason where it had no length; the
    page shows it in the table's column, with nothing to sort on, and
    in no other column.  Written into the stored file directly: the
    probe answers a bound for every contig of this fixture, and the key
    -- the label and the table files' md5s -- is untouched, so the file
    is still CURRENT."""
    a_coverage_repo(tmp_path)
    repaired(tmp_path, COVERAGE_RESOURCE_ID)
    stored = tmp_path / COVERAGE_RESOURCE_ID / CHROM_LENGTHS_FILE
    document = json.loads(stored.read_text())
    document["sources"]["tabix_estimate"]["chr10"] = "undetermined"
    stored.write_text(json.dumps(document))
    impl, repo = resynced(tmp_path, COVERAGE_RESOURCE_ID)

    table = table_after(impl.get_info(repo=repo), HEADING)

    chr10 = table.rows[2]
    assert chr10[0].own_text == "chr10"
    assert [cell.own_text for cell in chr10[1:]] == ["", "undetermined"]
    assert [cell.sort_value for cell in chr10[1:]] == [None, None]


#: The other two kinds: every kind's repair stores the file, so every
#: kind's page has the section -- it sits in the template they all
#: extend, not in a kind's slot.  Unlabelled three-contig tabix scores,
#: the sortable-table suite's own.
OTHER_KINDS = pytest.mark.parametrize(
    ("build_repo", "resource_id"),
    [(an_allele_repo, "scores/alleles"), (a_fragment_repo, "scores/fragments")],
    ids=["allele", "fragment"])


@OTHER_KINDS
def test_every_kind_renders_the_section_when_repaired(
    tmp_path: pathlib.Path,
    build_repo: Callable[[pathlib.Path], GenomicResourceRepo],
    resource_id: str,
) -> None:
    """Unlabelled, so one column: the table's own source."""
    build_repo(tmp_path)
    impl, repo = repaired(tmp_path, resource_id)

    table = table_after(impl.get_info(repo=repo), HEADING)

    assert table.text[0] == ["Chromosome", "tabix_estimate"]
    assert [row[0].text for row in table.rows] == ["chr1", "chr2", "chr10"]


@OTHER_KINDS
def test_every_kind_reads_not_computed_when_unrepaired(
    tmp_path: pathlib.Path,
    build_repo: Callable[[pathlib.Path], GenomicResourceRepo],
    resource_id: str,
) -> None:
    """The heading with nothing stored under it, as on the position
    score's page."""
    repo = build_repo(tmp_path)
    impl = build_score_implementation_from_resource(
        repo.get_resource(resource_id))

    _assert_not_computed(section_after(impl.get_info(repo=repo), HEADING))


@pytest.mark.parametrize(("labelled", "expected"), [
    (False, [["Chromosome", "bigwig"], ["chr1", "100"]]),
    (True, [["Chromosome", "reference_genome", "bigwig"],
            ["chr1", "100", "100"]]),
], ids=["unlabelled", "labelled"])
def test_a_repaired_bigwig_score_shows_its_header_and_marks_nothing(
    tmp_path: pathlib.Path, labelled: bool, expected: list[list[str]],
) -> None:
    """The header's exact sizes under ``bigwig``, and the genome's
    beside them when labelled.  The genome lists every contig of the
    score -- the marker is for a gap in the genome column, and there is
    none -- so no row is marked either way."""
    a_bigwig_repo_with_genome(tmp_path, labelled=labelled)
    impl, repo = repaired(tmp_path, BIGWIG)

    page = impl.get_info(repo=repo)

    assert table_after(page, HEADING).text == expected
    assert "unlisted-marker" not in section_after(page, HEADING)
