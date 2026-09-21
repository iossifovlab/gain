"""Chromosome lengths resolved on the implementation (gain#1448).

``GenomicScoreImplementation.get_chrom_lengths(grr)`` is where a caller
that holds a GRR asks for the ladder's answers per contig of the score: the
genome the ``reference_genome`` label names, resolved through that GRR,
and whatever the table can say, both kept in the record with the genome's
ranked best (gain#1574).
"""

import logging
import pathlib
from collections.abc import Callable
from typing import Any
from unittest import mock

import pytest
import pytest_mock
import yaml
from gain.genomic_resources.genomic_position_table import (
    ChromLengthSource,
    ContigExtent,
)
from gain.genomic_resources.genomic_scores.chrom_lengths import (
    ChromLength,
    ChromLengthAnswer,
)
from gain.genomic_resources.implementations.genomic_scores_impl import (
    GenomicScoreImplementation,
    build_score_implementation_from_resource,
)
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GenomicResourceRepo,
)
from gain.genomic_resources.testing import build_filesystem_test_repository
from gain.genomic_resources.testing.builders import (
    BigWigScoreBuilder,
    GRRBuilder,
    PositionScoreBuilder,
    a_bigwig_score,
    a_grr,
    a_position_score,
    a_reference_genome,
)

from .conftest import (
    UNUSABLE_RESOURCE_ID_LABELS,
    label_warnings,
    overlap_warnings,
)

#: The genome's exact length for chr1, past every row of the score.
CHR1_GENOME_LENGTH = 3000
#: The other genome's, distinct so a re-pointed label is seen to answer.
OTHER_GENOME_CHR1_LENGTH = 3500
#: The tabix probe's bounds for chr1's rows at 10 and 2500 and for a lone
#: chrM row at 40: measured, since the probe brackets a length on its own
#: geometric ladder and then bisects (gain#509).  The region-split pin in
#: test_genomic_scores_impl reads the same chrM bound off the same rows.
CHR1_PROBE_BOUND = 3052
CHRM_PROBE_BOUND = 48


def a_labelled_tabix_score_grr(*, genome_id: Any = "genome") -> GRRBuilder:
    """A tabix score ``score`` labelled with ``genome``, which lists chr1
    but not chrM, and a second genome ``other_genome`` for the label to be
    re-pointed at.  ``genome_id`` labels the score with something else --
    a genome the repository lacks, or a value that is no id at all."""
    return (
        a_grr()
        .with_resource(
            "genome",
            a_reference_genome()
            .with_chromosome("chr1", "A" * CHR1_GENOME_LENGTH))
        .with_resource(
            "other_genome",
            a_reference_genome()
            .with_chromosome("chr1", "A" * OTHER_GENOME_CHR1_LENGTH))
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
            .with_labels(reference_genome=genome_id))
    )


#: The bigWig header's lengths and the genome's for chr1 -- two exact
#: sources that disagree, so which one answered shows.
BIGWIG_HEADER_LENGTHS = {"chr1": 100, "chr2": 200}
BIGWIG_GENOME_CHR1_LENGTH = 90


def a_labelled_bigwig_score_grr() -> GRRBuilder:
    """A bigWig score ``score`` whose header lists chr1 and chr2,
    labelled with a genome listing chr1 only."""
    return (
        a_grr()
        .with_resource(
            "genome",
            a_reference_genome()
            .with_chromosome("chr1", "A" * BIGWIG_GENOME_CHR1_LENGTH))
        .with_resource(
            "score",
            a_bigwig_score()
            .with_data("""
                chr1  10  20  0.1
                chr2  10  20  0.2
            """)
            .with_chrom_lens(BIGWIG_HEADER_LENGTHS)
            .with_labels(reference_genome="genome"))
    )


def an_empty_mapped_contig_score() -> PositionScoreBuilder:
    """'kept' maps onto a file contig with rows, 'empty' onto one with
    none.  Only the in-memory backend, holding the whole file, can PROVE
    a listed contig empty (gain#509)."""
    return (
        a_position_score()
        .with_score("score", "float")
        .with_data("""
            chrom  pos_begin  score
            chr1   10         0.1
        """)
        .with_chrom_mapping_file(kept="chr1", empty="chr99")
    )


def rewrite_config(
    tmp_path: pathlib.Path, resource_id: str,
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    """Edit a realized resource's config as YAML, as the curator would.

    Through the YAML rather than a text replace, so any value -- an id,
    an int, a list -- lands as they would have written it.
    """
    config = tmp_path / resource_id / GR_CONF_FILE_NAME
    document = yaml.safe_load(config.read_text())
    mutate(document)
    config.write_text(yaml.safe_dump(document))


def set_label(
    tmp_path: pathlib.Path, resource_id: str, label: str, value: Any,
) -> None:
    """Rewrite one ``meta.labels`` entry of a realized resource."""
    rewrite_config(
        tmp_path, resource_id,
        lambda document: document["meta"]["labels"].__setitem__(
            label, value))


def patch_tabix_probe(
    mocker: pytest_mock.MockerFixture,
    side_effect: Callable[..., Any] | None = None,
) -> mock.MagicMock:
    """Replace the tabix contig-length probe where it lives (gain#509).

    One spelling of the dotted path for every test that asserts the
    probe ran, or did not: a move of the probe fails them all loudly
    here rather than turning an ``assert_not_called`` vacuous.
    """
    return mocker.patch(
        "gain.genomic_resources.genomic_position_table.table_tabix"
        ".get_chromosome_length_tabix",
        side_effect=side_effect)


def _the_impl(
    tmp_path: pathlib.Path, grr: GRRBuilder, resource_id: str = "score",
) -> tuple[GenomicScoreImplementation, GenomicResourceRepo]:
    repo = grr.build_repo(tmp_path)
    return build_score_implementation_from_resource(
        repo.get_resource(resource_id)), repo


def test_a_labelled_tabix_score_answers_the_genome_and_the_probe(
    tmp_path: pathlib.Path,
) -> None:
    """chr1 carries the genome's exact length AND the probe's bound, and
    the genome's is best; chrM, which the genome does not list, carries
    the probe's bound alone -- in table order (gain#1574)."""
    impl, repo = _the_impl(tmp_path, a_labelled_tabix_score_grr())

    lengths = impl.get_chrom_lengths(repo)

    assert list(lengths) == ["chr1", "chrM"]
    assert lengths["chr1"] == ChromLength(
        answers={
            ChromLengthSource.REFERENCE_GENOME: CHR1_GENOME_LENGTH,
            ChromLengthSource.TABIX_ESTIMATE: CHR1_PROBE_BOUND,
        },
        extent=None)
    assert lengths["chr1"].best == ChromLengthAnswer(
        CHR1_GENOME_LENGTH, ChromLengthSource.REFERENCE_GENOME)
    assert lengths["chrM"] == ChromLength(
        answers={ChromLengthSource.TABIX_ESTIMATE: CHRM_PROBE_BOUND},
        extent=None)


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

    assert list(lengths["chr1"].answers) == [ChromLengthSource.TABIX_ESTIMATE]
    assert lengths["chr1"].best is not None
    assert lengths["chr1"].best.length >= 2500


def test_a_bigwig_score_answers_its_header(
    tmp_path: pathlib.Path,
) -> None:
    # The builder's default header lists chr1 at 1000, well past its rows.
    impl, repo = _the_impl(
        tmp_path, _an_unlabelled_score_grr(a_bigwig_score()))

    lengths = impl.get_chrom_lengths(repo)

    assert lengths["chr1"] == ChromLength(
        answers={ChromLengthSource.BIGWIG: 1000}, extent=None)


# The region split reads EMPTY and UNDETERMINED oppositely (gain#509), so
# both come through as the record's extent, not as a missing entry.


def test_a_contig_proven_empty_keeps_the_reason(
    tmp_path: pathlib.Path,
) -> None:
    impl, repo = _the_impl(tmp_path, _an_unlabelled_score_grr(
        an_empty_mapped_contig_score()))

    lengths = impl.get_chrom_lengths(repo)

    assert list(lengths) == ["kept", "empty"]
    assert list(lengths["kept"].answers) == [ChromLengthSource.TABLE_EXTENT]
    assert lengths["empty"] == ChromLength(
        answers={}, extent=ContigExtent.EMPTY)


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
    assert lengths["chr1"].best == ChromLengthAnswer(
        100, ChromLengthSource.TABIX_ESTIMATE)
    assert lengths["chr2"] == ChromLength(
        answers={}, extent=ContigExtent.UNDETERMINED)


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

    assert lengths["chr1"].best == ChromLengthAnswer(
        OTHER_GENOME_CHR1_LENGTH, ChromLengthSource.REFERENCE_GENOME)


def test_a_label_naming_no_resource_falls_through_to_the_table(
    tmp_path: pathlib.Path,
) -> None:
    impl, repo = _the_impl(
        tmp_path, a_labelled_tabix_score_grr(genome_id="no/such/genome"))

    lengths = impl.get_chrom_lengths(repo)

    assert list(lengths["chr1"].answers) == [ChromLengthSource.TABIX_ESTIMATE]


def test_a_label_naming_a_non_genome_resource_falls_through_and_says_so(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """One policy for a label that names something that is not a genome,
    wherever the label is read (gain#1414): the coverage page has always
    degraded to raw counts over it rather than failing the build, and a
    statistics build now falls through to the table the same way rather
    than aborting the resource.  Warned, since the label is wrong."""
    # The score labelled with ITSELF: a resource id, a real resource,
    # not a genome.
    impl, repo = _the_impl(
        tmp_path, a_labelled_tabix_score_grr(genome_id="score"))

    with caplog.at_level(logging.WARNING):
        lengths = impl.get_chrom_lengths(repo)

    assert list(lengths["chr1"].answers) == [ChromLengthSource.TABIX_ESTIMATE]
    assert label_warnings(caplog) == [(
        "meta.labels.reference_genome of score names 'score', "
        "which is not a genome resource; ignoring it"
    )]


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

    assert list(lengths["chr1"].answers) == [ChromLengthSource.TABIX_ESTIMATE]
    warnings = label_warnings(caplog)
    assert len(warnings) == 1
    assert "score" in warnings[0]
    assert "reference_genome" in warnings[0]
    assert reported_as in warnings[0]


# -- the score's contigs against the genome's (gain#1575) ------------------

#: The genome every overlap test labels its score with.
GENOME_CONTIGS = ("chr1", "chr2")


def a_score_over(
    *chroms: str, tabix: bool = True,
    renamed: dict[str, str] | None = None,
) -> GRRBuilder:
    """A score ``score`` with one row per contig of ``chroms``, in that
    order, labelled with ``genome``, which lists ``GENOME_CONTIGS``.
    Tabix unless told otherwise -- a tabix file with no rows at all is
    not a thing the backend opens, so the empty score is in-memory.
    ``renamed`` ships a ``chrom_mapping`` file, score name -> file contig,
    for a contig name the data file itself could not carry."""
    genome = a_reference_genome()
    for chrom in GENOME_CONTIGS:
        genome = genome.with_chromosome(chrom, "A" * CHR1_GENOME_LENGTH)
    rows = "\n".join(f"{chrom}  10  0.1" for chrom in chroms)
    score = (
        a_position_score()
        .with_score("score", "float")
        .with_data(f"chrom  pos_begin  score\n{rows}\n")
        .with_labels(reference_genome="genome")
    )
    if tabix:
        score = score.with_tabix()
    if renamed is not None:
        score = score.with_chrom_mapping_file(**renamed)
    return (
        a_grr()
        .with_resource("genome", genome)
        .with_resource("score", score)
    )


def test_a_contig_the_genome_does_not_list_is_warned_about_once(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """Partial overlap: the resource repairs, the unlisted contig falls to
    the table's own source, and one WARNING says which and how many."""
    impl, repo = _the_impl(tmp_path, a_score_over("chr1", "chr2", "chrUn_x"))

    with caplog.at_level(logging.WARNING):
        lengths = impl.get_chrom_lengths(repo)

    best = lengths["chrUn_x"].best
    assert best is not None
    assert best.source is ChromLengthSource.TABIX_ESTIMATE
    assert overlap_warnings(caplog) == [(
        "reference_genome genome of score does not list 1 of the score's "
        "3 contigs (chrUn_x); their lengths fall to the table's own source"
    )]


def test_a_genome_listing_none_of_the_contigs_fails_the_score(
    tmp_path: pathlib.Path,
) -> None:
    """Zero overlap is a mis-authored label -- typically a chrom_mapping
    that does not produce the genome's names -- and the resource's
    statistics fail rather than quietly repair off the table's estimates.
    ``ValueError`` is what ``repo-repair`` reports as a one-line resource
    failure and carries on from."""
    impl, repo = _the_impl(tmp_path, a_score_over("1", "2"))

    with pytest.raises(ValueError, match=(
        r"^reference_genome genome of score lists none of the score's "
        r"contigs \(1, 2\); a chrom_mapping that does not produce the "
        r"genome's contig names is the usual cause$"
    )):
        impl.get_chrom_lengths(repo)


def test_a_genome_listing_every_contig_is_not_warned_about(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    impl, repo = _the_impl(tmp_path, a_score_over(*GENOME_CONTIGS))

    with caplog.at_level(logging.WARNING):
        impl.get_chrom_lengths(repo)

    assert overlap_warnings(caplog) == []


@pytest.mark.parametrize("genome_id", [None, "no/such/genome", "score"])
def test_a_score_without_a_resolved_genome_is_not_checked_for_overlap(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
    genome_id: str | None,
) -> None:
    """The check is against a genome that RESOLVED: no label, a label
    naming a missing resource, or one that is not a genome, keeps
    today's label warning and nothing more -- and never raises, since
    the score's contigs cannot be compared with anything."""
    impl, repo = _the_impl(
        tmp_path, a_labelled_tabix_score_grr(genome_id=genome_id))

    with caplog.at_level(logging.WARNING):
        impl.get_chrom_lengths(repo)

    assert overlap_warnings(caplog) == []


def test_the_warning_samples_the_first_five_unlisted_contigs(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """A mapping that covers hg38's alts leaves hundreds unlisted: the
    count is exact, the names are a bounded sample in table order."""
    # Authored against lexicographic order, so a sorted sample is seen.
    unlisted = tuple(f"chrUn_{index}" for index in range(6, -1, -1))
    impl, repo = _the_impl(tmp_path, a_score_over("chr1", *unlisted))

    with caplog.at_level(logging.WARNING):
        impl.get_chrom_lengths(repo)

    assert overlap_warnings(caplog) == [(
        "reference_genome genome of score does not list 7 of the score's "
        "8 contigs (chrUn_6, chrUn_5, chrUn_4, chrUn_3, chrUn_2, ...); "
        "their lengths fall to the table's own source"
    )]


def test_a_labelled_score_with_no_contigs_is_not_a_mismatch(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """Nothing to compare: an empty table is not a chrom_mapping that
    missed the genome, and is left to whatever an empty score does."""
    impl, repo = _the_impl(tmp_path, a_score_over(tabix=False))

    with caplog.at_level(logging.WARNING):
        lengths = impl.get_chrom_lengths(repo)

    assert lengths == {}
    assert overlap_warnings(caplog) == []


def test_a_contig_name_that_could_forge_a_log_line_is_escaped(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """The names come from the table or its chrom_mapping -- repository
    content -- and a control character in one would end the line and
    start a forged record; the warning and the failure both show it
    escaped (the gain#642 policy)."""
    impl, repo = _the_impl(tmp_path, a_score_over(
        "chr1", "chr2", renamed={"chr1": "chr1", "chrUn\x1b[31m": "chr2"}))

    with caplog.at_level(logging.WARNING):
        impl.get_chrom_lengths(repo)

    assert overlap_warnings(caplog) == [(
        "reference_genome genome of score does not list 1 of the score's "
        "2 contigs (chrUn\\x1b[31m); their lengths fall to the table's "
        "own source"
    )]

    impl, repo = _the_impl(tmp_path / "none", a_score_over(
        "chr1", "chr2", renamed={"1\x1b": "chr1", "2\x1b": "chr2"}))

    with pytest.raises(ValueError, match=r"contigs \(1\\x1b, 2\\x1b\)"):
        impl.get_chrom_lengths(repo)
