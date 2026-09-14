"""Chromosome lengths resolved on the implementation (gain#1448).

``GenomicScoreImplementation.get_chrom_lengths(grr)`` is where a caller
that holds a GRR -- the statistics region split, the coverage page --
asks for the ladder's answer per contig of the score: the genome the
``reference_genome`` label names first, then whatever the table can say.
Nothing is stored; the ladder runs where it is asked.
"""

import logging
import pathlib
from typing import Any

import pytest
import pytest_mock
from gain.genomic_resources.genomic_position_table import (
    ChromLengthSource,
    ContigExtent,
)
from gain.genomic_resources.genomic_scores.chrom_lengths import ChromLength
from gain.genomic_resources.implementations.genomic_scores_impl import (
    GenomicScoreImplementation,
    build_score_implementation_from_resource,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing import build_filesystem_test_repository
from gain.genomic_resources.testing.builders import (
    BigWigScoreBuilder,
    GRRBuilder,
    PositionScoreBuilder,
    a_bigwig_score,
    a_grr,
    a_position_score,
)

from .conftest import (
    CHR1_GENOME_LENGTH,
    CHRM_PROBE_BOUND,
    OTHER_GENOME_CHR1_LENGTH,
    UNUSABLE_RESOURCE_ID_LABELS,
    a_labelled_tabix_score_grr,
    label_warnings,
    patch_tabix_probe,
    set_label,
)


def _the_impl(
    tmp_path: pathlib.Path, grr: GRRBuilder, resource_id: str = "score",
) -> tuple[GenomicScoreImplementation, GenomicResourceRepo]:
    repo = grr.build_repo(tmp_path)
    return build_score_implementation_from_resource(
        repo.get_resource(resource_id)), repo


def test_a_labelled_tabix_score_answers_the_genome_then_the_probe(
    tmp_path: pathlib.Path,
) -> None:
    """chr1 is the genome's exact length; chrM, which the genome does not
    list, falls through to the probe's bound -- in table order."""
    impl, repo = _the_impl(tmp_path, a_labelled_tabix_score_grr())

    lengths = impl.get_chrom_lengths(repo)

    assert list(lengths) == ["chr1", "chrM"]
    assert (lengths["chr1"].length, lengths["chr1"].source) == (
        CHR1_GENOME_LENGTH, ChromLengthSource.REFERENCE_GENOME)
    assert (lengths["chrM"].length, lengths["chrM"].source) == (
        CHRM_PROBE_BOUND, ChromLengthSource.TABIX_ESTIMATE)


def test_a_closed_score_is_opened_for_the_answer_and_left_closed(
    tmp_path: pathlib.Path,
) -> None:
    impl, repo = _the_impl(tmp_path, a_labelled_tabix_score_grr())
    assert not impl.score.is_open()

    impl.get_chrom_lengths(repo)

    assert not impl.score.is_open()


def test_an_open_score_stays_open_for_its_owner(
    tmp_path: pathlib.Path,
) -> None:
    impl, repo = _the_impl(tmp_path, a_labelled_tabix_score_grr())
    with impl.score.open():
        impl.get_chrom_lengths(repo)

        assert impl.score.is_open()


def _an_unlabelled_score_grr(
    score: PositionScoreBuilder | BigWigScoreBuilder,
) -> GRRBuilder:
    return a_grr().with_resource("score", score)


def test_an_unlabelled_tabix_score_answers_the_probes_bound(
    tmp_path: pathlib.Path,
) -> None:
    impl, repo = _the_impl(tmp_path, _an_unlabelled_score_grr(
        a_position_score()
        .with_score("score", "float")
        .with_data("""
            chrom  pos_begin  score
            chr1   10         0.1
            chr1   2500       0.2
        """)
        .with_tabix()))

    lengths = impl.get_chrom_lengths(repo)

    assert lengths["chr1"].source is ChromLengthSource.TABIX_ESTIMATE
    assert lengths["chr1"].length is not None
    assert lengths["chr1"].length >= 2500


def test_a_bigwig_score_answers_its_header(
    tmp_path: pathlib.Path,
) -> None:
    # The builder's default header lists chr1 at 1000, well past its rows.
    impl, repo = _the_impl(
        tmp_path, _an_unlabelled_score_grr(a_bigwig_score()))

    lengths = impl.get_chrom_lengths(repo)

    assert lengths["chr1"] == ChromLength(
        length=1000, source=ChromLengthSource.BIGWIG, extent=None)


# The region split reads EMPTY and UNDETERMINED oppositely (gain#509), so
# both come through as the record's extent, not as a missing entry.


def test_a_contig_proven_empty_keeps_the_reason(
    tmp_path: pathlib.Path,
) -> None:
    # 'kept' maps onto a file contig with rows, 'empty' onto one with none;
    # only the in-memory backend, holding the whole file, can prove that.
    impl, repo = _the_impl(tmp_path, _an_unlabelled_score_grr(
        a_position_score()
        .with_score("score", "float")
        .with_data("""
            chrom  pos_begin  score
            chr1   10         0.1
        """)
        .with_chrom_mapping_file(kept="chr1", empty="chr99")))

    lengths = impl.get_chrom_lengths(repo)

    assert list(lengths) == ["kept", "empty"]
    assert lengths["kept"].source is ChromLengthSource.TABLE_EXTENT
    assert lengths["empty"] == ChromLength(
        length=None, source=None, extent=ContigExtent.EMPTY)


def test_a_contig_of_undeterminable_length_keeps_the_reason(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
) -> None:
    impl, repo = _the_impl(tmp_path, _an_unlabelled_score_grr(
        a_position_score()
        .with_score("score", "float")
        .with_data("""
            chrom  pos_begin  score
            chr1   10         0.1
            chr2   40         0.3
        """)
        .with_tabix()))
    patch_tabix_probe(mocker, side_effect=lambda _file, chrom, _step: (
        None if chrom == "chr2" else 100))

    lengths = impl.get_chrom_lengths(repo)

    assert list(lengths) == ["chr1", "chr2"]
    assert lengths["chr1"].length == 100
    assert lengths["chr2"] == ChromLength(
        length=None, source=None, extent=ContigExtent.UNDETERMINED)


def test_the_label_as_written_now_is_the_genome_that_answers(
    tmp_path: pathlib.Path,
) -> None:
    """Nothing is remembered from an earlier read: re-point the label and
    the next call resolves through the genome it names now."""
    impl, repo = _the_impl(tmp_path, a_labelled_tabix_score_grr())
    impl.get_chrom_lengths(repo)
    set_label(tmp_path, "score", "reference_genome", "other_genome")
    impl = build_score_implementation_from_resource(
        build_filesystem_test_repository(tmp_path).get_resource("score"))

    lengths = impl.get_chrom_lengths(repo)

    assert lengths["chr1"] == ChromLength(
        length=OTHER_GENOME_CHR1_LENGTH,
        source=ChromLengthSource.REFERENCE_GENOME, extent=None)


def test_a_label_naming_no_resource_falls_through_to_the_table(
    tmp_path: pathlib.Path,
) -> None:
    impl, repo = _the_impl(
        tmp_path, a_labelled_tabix_score_grr(genome_id="no/such/genome"))

    lengths = impl.get_chrom_lengths(repo)

    assert lengths["chr1"].source is ChromLengthSource.TABIX_ESTIMATE


@pytest.mark.parametrize(
    ("value", "reported_as"), UNUSABLE_RESOURCE_ID_LABELS)
def test_an_unusable_label_falls_through_to_the_table_and_says_so_once(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
    value: Any, reported_as: str,
) -> None:
    """A label that is not a resource id is read as absent (gain#1053):
    the table answers, and the one warning names the resource, the
    label and what was found there."""
    impl, repo = _the_impl(
        tmp_path, a_labelled_tabix_score_grr(genome_id=value))

    with caplog.at_level(logging.WARNING):
        lengths = impl.get_chrom_lengths(repo)

    assert lengths["chr1"].source is ChromLengthSource.TABIX_ESTIMATE
    warnings = label_warnings(caplog)
    assert len(warnings) == 1
    assert "score" in warnings[0]
    assert "reference_genome" in warnings[0]
    assert reported_as in warnings[0]
