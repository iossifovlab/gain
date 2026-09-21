"""The stored chromosome lengths price the Coverage section (gain#1578).

The second rung of the denominator ladder -- the score's own records --
reads ``statistics/chrom_lengths.json`` first, through the freshness
gate with the render repository, so a render never opens a table for a
length, and a page built with no repository, or after the genome the
label names has left it, is still priced from what the repair stored.
"""

import logging
import pathlib
import shutil

import pytest
import pytest_mock
from gain.genomic_resources.implementations.genomic_scores_impl import (
    PositionScoreImplementation,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing.builders import (
    a_grr,
    a_position_score,
    a_reference_genome,
)

from .conftest import captured_warnings
from .test_cli_stats_chrom_lengths import resource_stats
from .test_genomic_scores_impl_derived_files import resynced

SCORE = "scores/one"
GENOME = "genomes/g1578"
#: 5..9 and 30..33 of the chr1 score below.
COVERED = 9


def _a_labelled_tabix_repo(where: pathlib.Path) -> GenomicResourceRepo:
    """A chr1-only tabix score labelled with a genome of chr1 (100 bp)
    and chr2 (300 bp) -- one contig the score never touches."""
    return (
        a_grr()
        .with_resource(
            SCORE,
            a_position_score()
            .with_score("score", "float")
            .with_data(
                """
                chrom  pos_begin  pos_end  score
                chr1   5          9        0.1
                chr1   30         33       0.2
                """)
            .with_tabix()
            .with_labels(reference_genome=GENOME))
        .with_resource(
            GENOME,
            a_reference_genome()
            .with_chromosome("chr1", "A" * 100)
            .with_chromosome("chr2", "A" * 300))
        .build_repo(where)
    )


def _repaired(
    where: pathlib.Path,
) -> tuple[PositionScoreImplementation, GenomicResourceRepo]:
    """The score repaired -- statistics and stored lengths -- and a
    fresh view of the repository as it is on disk now."""
    resource_stats(where, SCORE)
    _, repo = resynced(where, SCORE)
    return PositionScoreImplementation(repo.get_resource(SCORE)), repo


def test_a_repaired_tabix_score_is_priced_with_no_repository(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
) -> None:
    """No repository, so the genome rung resolves nothing; the stored
    genome answer prices chr1 -- the score's contig, not the genome's
    two -- and the table is never opened."""
    _a_labelled_tabix_repo(tmp_path)
    impl, _ = _repaired(tmp_path)
    opened = mocker.spy(impl.score, "open")

    page = impl.get_info()

    assert f">{COVERED}<" in page
    assert page.count(">9.00%<") == 2  # the chr1 row and the global row
    assert "contig with no values" not in page
    opened.assert_not_called()


def test_a_repaired_tabix_score_with_its_genome_at_hand_is_unchanged(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
) -> None:
    """A regression pin: with the repository the genome rung resolves
    first and prices the page over the WHOLE genome, roll-up included,
    as it did before the file existed -- and the table stays closed."""
    _a_labelled_tabix_repo(tmp_path)
    impl, repo = _repaired(tmp_path)
    opened = mocker.spy(impl.score, "open")

    page = impl.get_info(repo=repo)

    assert page.count(">9.00%<") == 1  # the chr1 row: 9 of 100
    assert ">2.25%<" in page  # the global row: 9 of the genome's 400
    assert "1 contig with no values (300 bp)" in page
    opened.assert_not_called()


def _without_the_genome(where: pathlib.Path) -> GenomicResourceRepo:
    """The repository with the genome resource removed since the repair."""
    shutil.rmtree(where / GENOME)
    _, repo = resynced(where, SCORE)
    return repo


def test_a_genome_gone_since_the_repair_still_prices_the_scores_own_contigs(
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockerFixture,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The record is the record: the stored genome answers price chr1,
    the universe is the score's genome-listed contigs -- so no roll-up
    of the genome's untouched chr2 -- and the label dangling is said
    once, as it is today."""
    _a_labelled_tabix_repo(tmp_path)
    impl, _ = _repaired(tmp_path)
    repo = _without_the_genome(tmp_path)
    opened = mocker.spy(impl.score, "open")

    with caplog.at_level(logging.WARNING):
        page = impl.get_info(repo=repo)

    assert page.count(">9.00%<") == 2  # the chr1 row and the global row
    assert "contig with no values" not in page
    opened.assert_not_called()
    assert captured_warnings(caplog).count(
        f"Couldn't find reference genome {GENOME}") == 1


def test_an_unrepaired_tabix_score_whose_genome_is_gone_renders_raw_counts(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
) -> None:
    """Nothing stored, nothing exact a tabix table can say live: raw
    counts, and the table is still never opened for a length."""
    _a_labelled_tabix_repo(tmp_path)
    impl, _ = _repaired(tmp_path)
    (tmp_path / SCORE / "statistics" / "chrom_lengths.json").unlink()
    repo = _without_the_genome(tmp_path)
    opened = mocker.spy(impl.score, "open")

    page = impl.get_info(repo=repo)

    assert f">{COVERED}<" in page
    assert "Covered %" not in page
    opened.assert_not_called()
