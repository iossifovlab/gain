"""Score-level chromosome lengths read from the stored file (gain#1419).

A repaired score carries ``statistics/chrom_lengths.json``; the first
length read of an open loads it when it describes the resource as it is
now, and the three length methods answer from it -- which is how
``REFERENCE_GENOME`` reaches the score level at all, and what takes the
tabix probe out of the read path.  Absent or stale, the file is ignored
and the methods resolve live through the table, as an unrepaired score
always has.
"""

import logging
import pathlib
from typing import Any

import pytest
import pytest_mock
from gain.genomic_resources.genomic_position_table import ChromLengthSource
from gain.genomic_resources.genomic_scores import (
    GenomicScore,
    build_score_from_resource,
)
from gain.genomic_resources.testing import (
    build_filesystem_test_repository,
)
from gain.genomic_resources.testing.builders import (
    GRRBuilder,
    a_grr,
    a_position_score,
)

from .conftest import (
    CHR1_GENOME_LENGTH,
    CHRM_PROBE_BOUND,
    UNUSABLE_RESOURCE_ID_LABELS,
    a_labelled_tabix_score_grr,
    label_warnings,
    patch_tabix_probe,
    resource_stats,
    set_label,
    truncate,
)


def _repaired_score(tmp_path: pathlib.Path, grr: GRRBuilder) -> GenomicScore:
    """Realize ``grr``, repair its ``score``, and hand that score back
    through a fresh repository object -- so nothing read before the
    repair is remembered across it."""
    grr.build_repo(tmp_path)
    resource_stats(tmp_path, "score")
    return _the_score(tmp_path)


def _the_score(tmp_path: pathlib.Path) -> GenomicScore:
    return build_score_from_resource(
        build_filesystem_test_repository(tmp_path).get_resource("score"))


def _a_repaired_labelled_tabix_score(tmp_path: pathlib.Path) -> GenomicScore:
    return _repaired_score(tmp_path, a_labelled_tabix_score_grr())


def _the_lengths_file(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / "score" / "statistics" / "chrom_lengths.json"


def test_a_repaired_score_answers_the_genomes_length_and_says_so(
    tmp_path: pathlib.Path,
) -> None:
    score = _a_repaired_labelled_tabix_score(tmp_path).open()

    assert score.get_chrom_length("chr1") == CHR1_GENOME_LENGTH
    assert score.get_chrom_length_source("chr1") is \
        ChromLengthSource.REFERENCE_GENOME


def test_all_lengths_come_back_in_table_order_from_the_file(
    tmp_path: pathlib.Path,
) -> None:
    score = _a_repaired_labelled_tabix_score(tmp_path).open()

    assert score.get_all_chrom_lengths() == {
        "chr1": CHR1_GENOME_LENGTH, "chrM": CHRM_PROBE_BOUND}
    assert list(score.get_all_chrom_lengths()) == ["chr1", "chrM"]


def test_a_repaired_score_never_runs_the_probe_to_answer(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
) -> None:
    """The point of storing: the expensive rung ran once, at repair."""
    score = _a_repaired_labelled_tabix_score(tmp_path)
    probe = patch_tabix_probe(mocker)

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
    score.get_chrom_length("chr1")
    score.close()
    _the_lengths_file(tmp_path).unlink()

    score.open()

    assert score.get_chrom_length_source("chr1") is \
        ChromLengthSource.TABIX_ESTIMATE


def test_without_the_file_the_score_resolves_live_and_says_so_once(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """An unrepaired resource is a normal state: INFO, and one line."""
    score = _a_repaired_labelled_tabix_score(tmp_path)
    _the_lengths_file(tmp_path).unlink()
    caplog.clear()  # the setup's own repair says the same words once

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


def _an_empty_mapped_contig_score_grr() -> GRRBuilder:
    """'kept' maps onto a file contig with rows, 'empty' onto one with
    none -- so the stored file carries an extent, not a length, for it."""
    return a_grr().with_resource(
        "score",
        a_position_score()
        .with_score("score", "float")
        .with_data("""
            chrom  pos_begin  score
            chr1   10         0.1
        """)
        .with_chrom_mapping_file(kept="chr1", empty="chr99"))


def test_a_stored_extent_is_refused_in_the_live_paths_words(
    tmp_path: pathlib.Path,
) -> None:
    score = _repaired_score(tmp_path, _an_empty_mapped_contig_score_grr())

    with pytest.raises(ValueError, match="contig empty has no records"):
        score.open().get_chrom_length("empty")


def test_all_lengths_omit_a_stored_extent(
    tmp_path: pathlib.Path,
) -> None:
    score = _repaired_score(tmp_path, _an_empty_mapped_contig_score_grr())

    assert list(score.open().get_all_chrom_lengths()) == ["kept"]


def test_a_file_derived_under_another_label_is_not_trusted(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """Re-pointed and not yet repaired, the file would answer the OLD
    genome's lengths; the score treats it as absent instead."""
    _a_repaired_labelled_tabix_score(tmp_path)
    set_label(tmp_path, "score", "reference_genome", "other_genome")
    score = _the_score(tmp_path)

    with caplog.at_level(logging.INFO):
        source = score.open().get_chrom_length_source("chr1")

    assert source is ChromLengthSource.TABIX_ESTIMATE
    assert sum(
        "stale" in record.getMessage() for record in caplog.records) == 1


def test_a_file_derived_without_a_genome_is_trusted_under_any_label(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
) -> None:
    """The repair records the label the genome was resolved FROM -- none
    when it did not resolve -- while the score sees the label as
    written.  The two differ for a label naming a genome the repository
    lacks, and the file then holds exactly the table-rung answers the
    live fallback would give, so nothing wrong can be answered by
    trusting it; refusing it would cost the probe on every read, for a
    resource the next repair leaves as it is."""
    score = _repaired_score(
        tmp_path, a_labelled_tabix_score_grr(genome_id="no/such/genome"))
    probe = patch_tabix_probe(mocker)

    source = score.open().get_chrom_length_source("chr1")

    assert source is ChromLengthSource.TABIX_ESTIMATE
    probe.assert_not_called()


@pytest.mark.parametrize(
    ("value", "reported_as"), UNUSABLE_RESOURCE_ID_LABELS)
def test_a_label_mangled_after_the_repair_is_compared_quietly(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
    value: Any, reported_as: str,
) -> None:
    """The freshness check compares the label; it does not act on it,
    and the readers that act -- the next repair, the page -- are the
    ones that report the slip (gain#1053 pins that the repair says so
    once).  A file derived FROM a genome under a label that is now
    unusable is stale, and that is all the reads say.
    """
    _a_repaired_labelled_tabix_score(tmp_path)
    set_label(tmp_path, "score", "reference_genome", value)
    score = _the_score(tmp_path)
    caplog.clear()

    with caplog.at_level(logging.INFO):
        source = score.open().get_chrom_length_source("chr1")

    assert source is ChromLengthSource.TABIX_ESTIMATE
    assert label_warnings(caplog) == []


def test_a_truncated_file_is_ignored_with_a_warning(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """A repair killed mid-write must not turn every length read into a
    JSON error: the reads resolve live, and the one line that says why
    is a WARNING -- unlike absence, this one has something to repair."""
    score = _a_repaired_labelled_tabix_score(tmp_path)
    truncate(_the_lengths_file(tmp_path))
    caplog.clear()

    with caplog.at_level(logging.INFO):
        length = score.open().get_chrom_length("chr1")

    assert length == score.table.find_chromosome_length("chr1")
    assert [
        record.levelno for record in caplog.records
        if "chrom_lengths.json" in record.getMessage()
    ] == [logging.WARNING]


def test_opening_an_unrepaired_score_says_nothing_at_info(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """Most opens never ask for a length; the absence is reported by the
    read that wants the file, not by every open (the bigWig
    deprecated-key test pins that an open is silent at INFO)."""
    score = _a_repaired_labelled_tabix_score(tmp_path)
    _the_lengths_file(tmp_path).unlink()
    caplog.clear()  # the setup's own repair and builder lines

    with caplog.at_level(logging.INFO):
        score.open()

    assert [
        record.getMessage() for record in caplog.records
        if record.levelno >= logging.INFO] == []
