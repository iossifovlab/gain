"""Score-level chromosome lengths read from the stored file (gain#1419).

A repaired score carries ``statistics/chrom_lengths.json``; ``open()``
loads it when it describes the resource as it is now, and the three
length methods answer from it -- which is how ``REFERENCE_GENOME`` reaches
the score level at all, and what takes the tabix probe out of the read
path.  Absent or stale, the file is ignored and the methods resolve live
through the table, as an unrepaired score always has.
"""

import logging
import pathlib

import pytest
import pytest_mock
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.genomic_position_table import ChromLengthSource
from gain.genomic_resources.genomic_scores import (
    GenomicScore,
    build_score_from_resource,
)
from gain.genomic_resources.repository import GR_CONF_FILE_NAME
from gain.genomic_resources.testing import (
    build_filesystem_test_repository,
)
from gain.genomic_resources.testing.builders import (
    a_grr,
    a_position_score,
    a_reference_genome,
)

_CHR1_GENOME_LENGTH = 3000


def _a_repaired_labelled_tabix_score(tmp_path: pathlib.Path) -> GenomicScore:
    """A tabix score labelled with a genome listing chr1 (not chrM),
    repaired -- so its lengths file is on disk and current."""
    (
        a_grr()
        .with_resource(
            "genome",
            a_reference_genome()
            .with_chromosome("chr1", "A" * _CHR1_GENOME_LENGTH))
        .with_resource(
            "score",
            a_position_score()
            .with_score("score", "float")
            .with_data("""
                chrom  pos_begin  score
                chr1   10         0.1
                chr1   2500       0.2
                chrM   40         0.3
            """)
            .with_tabix()
            .with_labels(reference_genome="genome"))
        .build_repo(tmp_path)
    )
    cli_manage([
        "resource-stats", "-r", "score", "-R", str(tmp_path), "-j", "1"])
    # A fresh repository object, so nothing read before the repair is
    # remembered across it.
    repo = build_filesystem_test_repository(tmp_path)
    return build_score_from_resource(repo.get_resource("score"))


def _the_tabix_probe(mocker: pytest_mock.MockerFixture) -> pytest_mock.MockType:
    return mocker.patch(
        "gain.genomic_resources.genomic_position_table.table_tabix"
        ".get_chromosome_length_tabix")


def test_a_repaired_score_answers_the_genomes_length_and_says_so(
    tmp_path: pathlib.Path,
) -> None:
    score = _a_repaired_labelled_tabix_score(tmp_path).open()

    assert score.get_chrom_length("chr1") == _CHR1_GENOME_LENGTH
    assert score.get_chrom_length_source("chr1") is \
        ChromLengthSource.REFERENCE_GENOME


def test_a_repaired_score_never_runs_the_probe_to_answer(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
) -> None:
    """The point of storing: the expensive rung ran once, at repair."""
    score = _a_repaired_labelled_tabix_score(tmp_path)
    probe = _the_tabix_probe(mocker)

    score.open()
    score.get_chrom_length("chrM")
    score.get_all_chrom_lengths()
    score.get_chrom_length_source("chr1")

    probe.assert_not_called()


def test_closing_drops_the_stored_lengths(
    tmp_path: pathlib.Path,
) -> None:
    """Reloaded per open(), not retained: what the file says between one
    open and the next is what the next answers from."""
    score = _a_repaired_labelled_tabix_score(tmp_path).open()
    score.close()
    (tmp_path / "score" / "statistics" / "chrom_lengths.json").unlink()

    score.open()

    assert score.get_chrom_length_source("chr1") is \
        ChromLengthSource.TABIX_ESTIMATE


def test_without_the_file_the_score_resolves_live_and_says_so_once(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """An unrepaired resource is a normal state: INFO, and one line."""
    score = _a_repaired_labelled_tabix_score(tmp_path)
    (tmp_path / "score" / "statistics" / "chrom_lengths.json").unlink()

    with caplog.at_level(logging.INFO):
        score.open()
        score.get_chrom_length("chr1")
        score.get_chrom_length("chrM")

    absent = [
        record for record in caplog.records
        if "no stored chromosome lengths" in record.getMessage()]
    assert [record.levelno for record in absent] == [logging.INFO]
    assert score.get_chrom_length_source("chr1") is \
        ChromLengthSource.TABIX_ESTIMATE


def _a_repaired_score_with_an_empty_mapped_contig(
    tmp_path: pathlib.Path,
) -> GenomicScore:
    """'kept' maps onto a file contig with rows, 'empty' onto one with
    none -- so the stored file carries an extent, not a length, for it."""
    (
        a_grr()
        .with_resource(
            "score",
            a_position_score()
            .with_score("score", "float")
            .with_data("""
                chrom  pos_begin  score
                chr1   10         0.1
            """)
            .with_chrom_mapping_file(kept="chr1", empty="chr99"))
        .build_repo(tmp_path)
    )
    cli_manage([
        "resource-stats", "-r", "score", "-R", str(tmp_path), "-j", "1"])
    repo = build_filesystem_test_repository(tmp_path)
    return build_score_from_resource(repo.get_resource("score"))


def test_a_stored_extent_is_refused_in_the_live_paths_words(
    tmp_path: pathlib.Path,
) -> None:
    score = _a_repaired_score_with_an_empty_mapped_contig(tmp_path).open()

    with pytest.raises(ValueError, match="contig empty has no records"):
        score.get_chrom_length("empty")


def test_all_lengths_omit_a_stored_extent(
    tmp_path: pathlib.Path,
) -> None:
    score = _a_repaired_score_with_an_empty_mapped_contig(tmp_path).open()

    assert list(score.get_all_chrom_lengths()) == ["kept"]


def test_a_file_derived_under_another_label_is_not_trusted(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """Re-pointed and not yet repaired, the file would answer the OLD
    genome's lengths; the score treats it as absent instead."""
    score = _a_repaired_labelled_tabix_score(tmp_path)
    config = tmp_path / "score" / GR_CONF_FILE_NAME
    config.write_text(config.read_text().replace(
        "reference_genome: genome", "reference_genome: other_genome"))
    score = build_score_from_resource(
        build_filesystem_test_repository(tmp_path).get_resource("score"))

    with caplog.at_level(logging.INFO):
        score.open()

    assert score.get_chrom_length_source("chr1") is \
        ChromLengthSource.TABIX_ESTIMATE
    assert sum(
        "stale" in record.getMessage() for record in caplog.records) == 1
