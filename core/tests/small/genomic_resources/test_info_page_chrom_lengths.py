"""The Chromosome lengths section of a genomic score's page (gain#1579).

What the last repair stored in ``statistics/chrom_lengths.json``, laid
out per contig with one column per source, so a reader can put a
tabix score's probed bound beside its genome's true length and see
which of the score's contigs that genome does not list.  Read behind
the same gate the Coverage section reads (gain#1578): a stored file
that is not ``CURRENT`` is never shown, and the render opens no table.
"""

from __future__ import annotations

import json
import logging
import pathlib
from collections.abc import Callable
from typing import Any

import pytest
import pytest_mock
from gain.genomic_resources.genomic_scores.chrom_lengths import (
    CHROM_LENGTHS_FILE,
)
from gain.genomic_resources.implementations.genomic_scores_impl import (
    GenomicScoreImplementation,
    build_score_implementation_from_resource,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing.builders import (
    a_bigwig_score,
    a_fragment_score,
    a_grr,
    a_reference_genome,
    an_allele_score,
)
from gain.genomic_resources.testing.info_page_fixtures import (
    COVERAGE_RESOURCE_ID,
    a_coverage_repo,
)
from gain.utils.chromosome_order import natural_chromosome_key

from .info_page_html import section_after, sort_keys, table_after
from .test_cli_stats_chrom_lengths import resource_stats
from .test_coverage_fractions import BIGWIG_DATA
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

    page = impl.get_info(repo=repo)

    # Between the Scores table every kind renders and the kind's own
    # sections: the lengths are about the resource, not its statistic.
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
    _repaired(tmp_path, COVERAGE_RESOURCE_ID)
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
#: extend, not in a kind's slot.
OTHER_KINDS = pytest.mark.parametrize(
    "a_score", [an_allele_score, a_fragment_score],
    ids=["allele", "fragment"])


def _a_repo_of(
    where: pathlib.Path, a_score: Callable[[], Any],
) -> GenomicResourceRepo:
    """An unlabelled two-contig score of that kind."""
    data = {
        an_allele_score: """
            chrom  pos_begin  reference  alternative  score
            chr1   10         A          G            0.1
            chr2   10         A          C            0.2
            """,
        a_fragment_score: """
            chrom  pos_begin  pos_end  score
            chr1   1          5        0.1
            chr2   1          5        0.2
            """,
    }[a_score]
    return (
        a_grr()
        .with_resource(
            "scores/other",
            a_score().with_score("score", "float").with_data(data)
            .with_tabix())
        .build_repo(where)
    )


@OTHER_KINDS
def test_every_kind_renders_the_section_when_repaired(
    tmp_path: pathlib.Path, a_score: Callable[[], Any],
) -> None:
    """Unlabelled, so one column: the table's own source."""
    _a_repo_of(tmp_path, a_score)
    impl, repo = _repaired(tmp_path, "scores/other")

    table = table_after(impl.get_info(repo=repo), HEADING)

    assert table.text[0] == ["Chromosome", "tabix_estimate"]
    assert [row[0].text for row in table.rows] == ["chr1", "chr2"]


@OTHER_KINDS
def test_every_kind_reads_not_computed_when_unrepaired(
    tmp_path: pathlib.Path, a_score: Callable[[], Any],
) -> None:
    """The heading with nothing stored under it, as on the position
    score's page: a repair would fill it, and the heading says so."""
    repo = _a_repo_of(tmp_path, a_score)
    impl = build_score_implementation_from_resource(
        repo.get_resource("scores/other"))

    section = section_after(impl.get_info(repo=repo), HEADING)

    assert "<p>not computed</p>" in section
    assert "<table>" not in section


BIGWIG = "scores/bw"
GENOME = "genomes/g1579"


def _a_bigwig_repo(
    where: pathlib.Path, *, labelled: bool,
) -> GenomicResourceRepo:
    """A bigWig score whose header lists chr1 (100) and chr2 (50),
    beside a genome listing the same two -- labelled with it or not."""
    score = a_bigwig_score().with_data(BIGWIG_DATA).with_chrom_lens(
        {"chr1": 100, "chr2": 50})
    if labelled:
        score = score.with_labels(reference_genome=GENOME)
    return (
        a_grr()
        .with_resource(BIGWIG, score)
        .with_resource(
            GENOME,
            a_reference_genome()
            .with_chromosome("chr1", "A" * 100)
            .with_chromosome("chr2", "A" * 50))
        .build_repo(where)
    )


def test_a_repaired_unlabelled_bigwig_score_has_its_header_column_alone(
    tmp_path: pathlib.Path,
) -> None:
    """One source, the header's exact sizes -- and no genome column, so
    nothing is unlisted and no row is marked."""
    _a_bigwig_repo(tmp_path, labelled=False)
    impl, repo = _repaired(tmp_path, BIGWIG)

    page = impl.get_info(repo=repo)

    table = table_after(page, HEADING)
    assert table.text == [
        ["Chromosome", "bigwig"],
        ["chr1", "100"],
        ["chr2", "50"],
    ]
    assert "unlisted-contig" not in section_after(page, HEADING)


def test_a_labelled_bigwig_score_the_genome_lists_in_full_marks_nothing(
    tmp_path: pathlib.Path,
) -> None:
    """Both columns, every contig in both: the marker is for a gap in
    the genome column, and there is none."""
    _a_bigwig_repo(tmp_path, labelled=True)
    impl, repo = _repaired(tmp_path, BIGWIG)

    page = impl.get_info(repo=repo)

    table = table_after(page, HEADING)
    assert table.text == [
        ["Chromosome", "reference_genome", "bigwig"],
        ["chr1", "100", "100"],
        ["chr2", "50", "50"],
    ]
    assert "unlisted-contig" not in section_after(page, HEADING)
