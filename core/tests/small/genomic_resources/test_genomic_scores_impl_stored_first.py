"""The stored chromosome lengths are read before the table is (gain#1578).

``GenomicScoreImplementation.get_chrom_lengths(grr)`` answers from
``statistics/chrom_lengths.json`` whenever the freshness gate calls it
``CURRENT`` -- no table opened, no genome resolved -- and runs the live
ladder, as before, in every other state.
"""

import pathlib

import pytest_mock
from gain.genomic_resources.genomic_position_table import ChromLengthSource
from gain.genomic_resources.genomic_scores import GenomicScore
from gain.genomic_resources.genomic_scores.chrom_lengths import (
    ChromLength,
    ChromLengthAnswer,
)

from .test_genomic_scores_impl_chrom_lengths import (
    CHR1_GENOME_LENGTH,
    CHR1_PROBE_BOUND,
    CHRM_PROBE_BOUND,
    OTHER_GENOME_CHR1_LENGTH,
    patch_tabix_probe,
    set_label,
)
from .test_genomic_scores_impl_derived_files import (
    a_repaired_labelled_score,
    resynced,
)


def test_a_current_file_answers_without_the_table(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
) -> None:
    """The records the repair stored ARE the answer: the genome's length
    beside the probe's bound for chr1, the bound alone for chrM."""
    impl, repo = a_repaired_labelled_score(tmp_path)
    opened = mocker.spy(GenomicScore, "open")
    probe = patch_tabix_probe(mocker)

    lengths = impl.get_chrom_lengths(repo)

    assert lengths == {
        "chr1": ChromLength(
            answers={
                ChromLengthSource.REFERENCE_GENOME: CHR1_GENOME_LENGTH,
                ChromLengthSource.TABIX_ESTIMATE: CHR1_PROBE_BOUND,
            },
            extent=None),
        "chrM": ChromLength(
            answers={ChromLengthSource.TABIX_ESTIMATE: CHRM_PROBE_BOUND},
            extent=None),
    }
    opened.assert_not_called()
    probe.assert_not_called()


def _lengths_file(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / "score" / "statistics" / "chrom_lengths.json"


def test_a_stale_file_is_passed_over_for_the_live_ladder(
    tmp_path: pathlib.Path,
) -> None:
    """The label re-pointed since the repair: the genome it names now
    answers, and the file is left as the repair wrote it -- a read is
    not a repair."""
    a_repaired_labelled_score(tmp_path)
    set_label(tmp_path, "score", "reference_genome", "other_genome")
    impl, repo = resynced(tmp_path)
    written = _lengths_file(tmp_path).read_text()

    lengths = impl.get_chrom_lengths(repo)

    assert lengths["chr1"].best == ChromLengthAnswer(
        OTHER_GENOME_CHR1_LENGTH, ChromLengthSource.REFERENCE_GENOME)
    assert _lengths_file(tmp_path).read_text() == written


def test_an_absent_file_is_the_live_ladder_and_stays_absent(
    tmp_path: pathlib.Path,
) -> None:
    """A resource repaired before the file existed: live, and only a
    repair writes the file."""
    a_repaired_labelled_score(tmp_path)
    _lengths_file(tmp_path).unlink()
    impl, repo = resynced(tmp_path)

    lengths = impl.get_chrom_lengths(repo)

    assert lengths["chr1"].best == ChromLengthAnswer(
        CHR1_GENOME_LENGTH, ChromLengthSource.REFERENCE_GENOME)
    assert not _lengths_file(tmp_path).exists()


def test_the_regions_split_by_the_file_are_the_regions_split_live(
    tmp_path: pathlib.Path,
) -> None:
    """A rebuild over a CURRENT file scans the same regions as one
    over none: the stored records are the live ladder's records."""
    impl, repo = a_repaired_labelled_score(tmp_path)
    from_the_file = impl._get_chrom_regions(1000, repo)
    _lengths_file(tmp_path).unlink()
    impl, repo = resynced(tmp_path)

    live = impl._get_chrom_regions(1000, repo)

    assert from_the_file == live
    assert len(live) > 1
