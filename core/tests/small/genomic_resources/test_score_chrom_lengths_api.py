"""A score answers its contigs' lengths itself (gain#1577).

``open()`` loads the ``statistics/chrom_lengths.json`` a repair stored
when the file describes the resource as it is now, and the four reads
answer from it -- per source, or the best-ranked -- which is how the
genome's length reaches a caller holding a score and nothing else.
Without a current file the reads go live through the table alone, as an
unrepaired score always has, and the score resolves no genome itself.
"""

import logging
import pathlib
from collections.abc import Callable

import pytest
import pytest_mock
import yaml
from gain.genomic_resources.genomic_position_table import ChromLengthSource
from gain.genomic_resources.genomic_scores import (
    GenomicScore,
    build_score_from_resource,
)
from gain.genomic_resources.genomic_scores.chrom_lengths import (
    CHROM_LENGTHS_FILE,
)
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GenomicResourceProtocolRepo,
)
from gain.genomic_resources.testing import (
    build_filesystem_test_protocol,
    build_filesystem_test_repository,
)
from gain.genomic_resources.testing.builders import (
    a_bigwig_score,
    a_grr,
    a_position_score,
    a_reference_genome,
)

from .conftest import captured_warnings
from .test_cli_stats_chrom_lengths import resource_stats
from .test_genomic_scores_impl_chrom_lengths import (
    CHR1_GENOME_LENGTH,
    CHR1_PROBE_BOUND,
    CHRM_PROBE_BOUND,
    a_labelled_tabix_score_grr,
    patch_tabix_probe,
    set_label,
)

GENOME = ChromLengthSource.REFERENCE_GENOME
ESTIMATE = ChromLengthSource.TABIX_ESTIMATE
BIGWIG = ChromLengthSource.BIGWIG

_NEEDS_A_REPAIR = r"no stored chromosome lengths .* repair the resource"


def _the_score(tmp_path: pathlib.Path) -> GenomicScore:
    """``score`` over a fresh view of the repository on disk, so nothing
    read before a repair is remembered across it."""
    repo = build_filesystem_test_repository(tmp_path)
    return build_score_from_resource(repo.get_resource("score"))


def _a_repaired_labelled_tabix_score(tmp_path: pathlib.Path) -> GenomicScore:
    """The labelled tabix score, repaired: its lengths file is on disk
    and current.  The genome lists chr1 (not chrM)."""
    a_labelled_tabix_score_grr().build_repo(tmp_path)
    resource_stats(tmp_path, "score")
    return _the_score(tmp_path)


def test_a_repaired_score_answers_every_source_without_the_probe(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
) -> None:
    """The genome's length by default, the table's on request, and a
    contig the genome does not list falls to the table's -- all from the
    file: the expensive rung ran once, at repair."""
    score = _a_repaired_labelled_tabix_score(tmp_path)
    probe = patch_tabix_probe(mocker)

    score.open()

    assert score.get_chrom_length("chr1") == CHR1_GENOME_LENGTH
    assert score.get_chrom_length_source("chr1") is GENOME
    assert score.get_chrom_length("chr1", source="tabix_estimate") == \
        CHR1_PROBE_BOUND
    assert score.get_chrom_length("chrM") == CHRM_PROBE_BOUND
    assert score.get_chrom_length_source("chrM") is ESTIMATE
    assert score.chrom_length_sources == [GENOME, ESTIMATE]
    probe.assert_not_called()


def test_all_lengths_are_the_best_answers_in_table_order_or_one_sources(
    tmp_path: pathlib.Path,
) -> None:
    """Per source, a contig that source did not answer is left out."""
    score = _a_repaired_labelled_tabix_score(tmp_path).open()

    assert score.get_all_chrom_lengths() == {
        "chr1": CHR1_GENOME_LENGTH, "chrM": CHRM_PROBE_BOUND}
    assert score.get_all_chrom_lengths(source=GENOME) == {
        "chr1": CHR1_GENOME_LENGTH}
    assert score.get_all_chrom_lengths(source="tabix_estimate") == {
        "chr1": CHR1_PROBE_BOUND, "chrM": CHRM_PROBE_BOUND}


def _an_unrepaired_labelled_tabix_score(
    tmp_path: pathlib.Path,
) -> GenomicScore:
    """The same score with no lengths file: the table alone answers."""
    a_labelled_tabix_score_grr().build_repo(tmp_path)
    return _the_score(tmp_path)


def test_without_the_file_the_table_answers_live_and_the_genome_is_refused(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
) -> None:
    """Nothing is remembered between reads: every one runs the probe."""
    score = _an_unrepaired_labelled_tabix_score(tmp_path).open()
    probe = patch_tabix_probe(mocker, side_effect=lambda *_: CHR1_PROBE_BOUND)

    assert score.get_chrom_length("chr1") == CHR1_PROBE_BOUND
    assert score.get_chrom_length_source("chr1") is ESTIMATE
    assert score.chrom_length_sources == [ESTIMATE]
    with pytest.raises(ValueError, match=_NEEDS_A_REPAIR):
        score.get_chrom_length("chr1", source=GENOME)
    with pytest.raises(ValueError, match=_NEEDS_A_REPAIR):
        score.get_all_chrom_lengths(source=GENOME)
    assert probe.call_count == 2


def _repoint_the_label(tmp_path: pathlib.Path) -> None:
    set_label(tmp_path, "score", "reference_genome", "other_genome")


def _replace_the_index(tmp_path: pathlib.Path) -> None:
    index = tmp_path / "score" / "data.txt.gz.tbi"
    index.write_bytes(index.read_bytes() + b"\0")


def _remap_the_contigs(tmp_path: pathlib.Path) -> None:
    """A ``chrom_mapping`` changed under the file: no table file changes,
    but the contigs the table lists are no longer the stored ones."""
    config = tmp_path / "score" / GR_CONF_FILE_NAME
    document = yaml.safe_load(config.read_text())
    document["table"]["chrom_mapping"] = {"add_prefix": "x"}
    config.write_text(yaml.safe_dump(document))


def _a_genome_turns_up(tmp_path: pathlib.Path) -> None:
    """Repaired under a label naming a genome the repository lacks, so
    the key says no genome; the label now names one that is there."""
    set_label(tmp_path, "score", "reference_genome", "genome")


@pytest.mark.parametrize(("make_it_stale", "reason"), [
    pytest.param(_repoint_the_label, "stale", id="label-repointed"),
    pytest.param(_a_genome_turns_up, "stale", id="genome-turns-up"),
    pytest.param(_replace_the_index, "stale", id="table-file-replaced"),
    pytest.param(_remap_the_contigs, "other contigs", id="contigs-remapped"),
])
def test_a_file_that_no_longer_describes_the_score_is_ignored_with_an_info(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
    make_it_stale: Callable[[pathlib.Path], None], reason: str,
) -> None:
    """Trusted, a re-pointed label's file would answer the OLD genome's
    lengths; every mismatch reads as no file, and says so once, as
    INFO -- an unrepaired resource is a normal state."""
    a_labelled_tabix_score_grr(
        genome_id="nowhere" if make_it_stale is _a_genome_turns_up
        else "genome").build_repo(tmp_path)
    resource_stats(tmp_path, "score")
    make_it_stale(tmp_path)
    score = _the_score(tmp_path)
    caplog.clear()  # the repair's own lines

    with caplog.at_level(logging.INFO):
        score.open()

    assert score.chrom_length_sources == [ESTIMATE]
    assert [
        record.levelno for record in caplog.records
        if reason in record.getMessage()
    ] == [logging.INFO]
    assert not captured_warnings(caplog)


def test_a_file_that_cannot_be_read_is_ignored_with_the_loaders_warning(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    _a_repaired_labelled_tabix_score(tmp_path)
    (tmp_path / "score" / CHROM_LENGTHS_FILE).write_text("{")
    score = _the_score(tmp_path)
    caplog.clear()  # the repair's own lines

    with caplog.at_level(logging.INFO):
        score.open()

    assert score.chrom_length_sources == [ESTIMATE]
    assert len(captured_warnings(caplog)) == 1


def test_closing_drops_the_stored_lengths_and_reopening_reads_the_disk(
    tmp_path: pathlib.Path,
) -> None:
    """Loaded per open(), not retained: what the file says between one
    open and the next is what the next answers from."""
    score = _a_repaired_labelled_tabix_score(tmp_path).open()
    stored_file = tmp_path / "score" / CHROM_LENGTHS_FILE

    score.close()
    stored_file.unlink()
    score.open()
    without = score.chrom_length_sources
    score.close()
    resource_stats(tmp_path, "score")
    score.open()

    assert without == [ESTIMATE]
    assert score.chrom_length_sources == [GENOME, ESTIMATE]


def test_a_closed_score_refuses_every_read(tmp_path: pathlib.Path) -> None:
    score = _a_repaired_labelled_tabix_score(tmp_path)

    for read in (
        lambda: score.get_chrom_length("chr1"),
        lambda: score.get_chrom_length_source("chr1"),
        lambda: score.get_all_chrom_lengths(),
        lambda: score.get_chrom_length("chr1", source="bogus"),
        lambda: score.chrom_length_sources,
    ):
        with pytest.raises(ValueError, match="score <score> is not open"):
            read()


def test_a_contig_the_score_does_not_carry_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    score = _a_repaired_labelled_tabix_score(tmp_path).open()

    with pytest.raises(
            ValueError, match="chr2 is not among the available chromosomes"):
        score.get_chrom_length("chr2")
    with pytest.raises(
            ValueError, match="chr2 is not among the available chromosomes"):
        score.get_chrom_length_source("chr2")


def test_a_source_with_no_answer_for_the_contig_names_those_that_have_one(
    tmp_path: pathlib.Path,
) -> None:
    """The genome does not list chrM; the file says so by omission."""
    score = _a_repaired_labelled_tabix_score(tmp_path).open()

    with pytest.raises(ValueError) as refusal:
        score.get_chrom_length("chrM", source=GENOME)

    assert str(refusal.value) == (
        "reference_genome has no length for chrM; the sources that answer "
        "it: ['tabix_estimate']")


def test_a_source_that_is_none_of_the_four_is_refused_naming_them(
    tmp_path: pathlib.Path,
) -> None:
    score = _a_repaired_labelled_tabix_score(tmp_path).open()

    with pytest.raises(ValueError) as refusal:
        score.get_all_chrom_lengths(source="genome")

    assert str(refusal.value) == (
        "'genome' is not a chromosome-length source; one of "
        "['reference_genome', 'bigwig', 'tabix_estimate', 'table_extent']")


def _a_score_with_an_empty_mapped_contig(tmp_path: pathlib.Path) -> None:
    """'kept' maps onto a file contig with rows, 'empty' onto one with
    none: the in-memory backend proves the latter holds no records."""
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


@pytest.mark.parametrize("repaired", [
    pytest.param(False, id="live"),
    pytest.param(True, id="stored"),
])
def test_a_contig_the_table_proved_empty_is_refused_in_the_tables_words(
    tmp_path: pathlib.Path, *, repaired: bool,
) -> None:
    """No source answers it, so the default read and the source read
    both refuse, saying WHY -- from the file as from the table."""
    _a_score_with_an_empty_mapped_contig(tmp_path)
    if repaired:
        resource_stats(tmp_path, "score")
    score = _the_score(tmp_path).open()

    with pytest.raises(ValueError, match="contig empty has no records"):
        score.get_chrom_length("empty")
    with pytest.raises(ValueError, match="contig empty has no records"):
        score.get_chrom_length_source("empty")
    with pytest.raises(ValueError, match="contig empty has no records"):
        score.get_chrom_length("empty", source=score.chrom_length_source)
    assert list(score.get_all_chrom_lengths()) == ["kept"]


def test_a_contig_the_probe_could_not_measure_is_refused_in_the_tables_words(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
) -> None:
    score = _an_unrepaired_labelled_tabix_score(tmp_path).open()
    patch_tabix_probe(mocker, side_effect=lambda *_: None)

    with pytest.raises(
            ValueError, match="could not determine the length of contig chr1"):
        score.get_chrom_length("chr1")
    with pytest.raises(
            ValueError, match="could not determine the length of contig chr1"):
        score.get_chrom_length("chr1", source=ESTIMATE)
    assert score.get_all_chrom_lengths() == {}


def _a_labelled_bigwig_score(tmp_path: pathlib.Path) -> None:
    """The header lists chr1 at 100 and chr2 at 200; the genome chr1 at
    90 -- two exact sources that disagree, so which one answered shows."""
    (
        a_grr()
        .with_resource(
            "genome",
            a_reference_genome().with_chromosome("chr1", "A" * 90))
        .with_resource(
            "score",
            a_bigwig_score()
            .with_data("""
                chr1  10  20  0.1
                chr2  10  20  0.2
            """)
            .with_chrom_lens({"chr1": 100, "chr2": 200})
            .with_labels(reference_genome="genome"))
        .build_repo(tmp_path)
    )


def test_a_bigwig_score_answers_its_header_live_and_the_genome_once_repaired(
    tmp_path: pathlib.Path,
) -> None:
    _a_labelled_bigwig_score(tmp_path)
    live = _the_score(tmp_path).open()
    live_answer = (
        live.chrom_length_sources, live.get_chrom_length("chr1"),
        live.get_chrom_length_source("chr1"))
    live.close()
    resource_stats(tmp_path, "score")

    assert live_answer == ([BIGWIG], 100, BIGWIG)
    with _the_score(tmp_path).open() as repaired:
        assert repaired.chrom_length_sources == [GENOME, BIGWIG]
        assert repaired.get_all_chrom_lengths() == {"chr1": 90, "chr2": 200}
        assert repaired.get_all_chrom_lengths(source=BIGWIG) == {
            "chr1": 100, "chr2": 200}


def test_a_resource_with_no_manifest_answers_live_and_builds_none(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """The key compares against the STORED manifest only: a read path
    never builds one (an md5 scan of the whole resource that writes
    state files, and a raise on a read-only mount)."""
    _a_repaired_labelled_tabix_score(tmp_path)
    manifest = tmp_path / "score" / ".MANIFEST"
    manifest.unlink()
    score = build_score_from_resource(
        GenomicResourceProtocolRepo(
            build_filesystem_test_protocol(tmp_path, repair=False),
        ).get_resource("score"))
    caplog.clear()

    with caplog.at_level(logging.INFO):
        score.open()

    assert score.chrom_length_sources == [ESTIMATE]
    assert not manifest.exists()
    assert [
        record.levelno for record in caplog.records
        if "stale" in record.getMessage()
    ] == [logging.INFO]


def test_a_file_the_protocol_cannot_fetch_is_ignored_with_a_warning(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """A transient failure fetching the optional file -- a 5xx a remote
    protocol rebuilds as ``OSError`` -- must not fail an open() that
    would otherwise read fine."""
    score = _a_repaired_labelled_tabix_score(tmp_path)
    mocker.patch.object(
        type(score.resource), "get_file_content",
        side_effect=OSError("HTTP 503"))
    caplog.clear()

    with caplog.at_level(logging.INFO):
        score.open()

    assert score.chrom_length_sources == [ESTIMATE]
    assert len(captured_warnings(caplog)) == 1


def test_an_absent_file_is_said_below_info(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """Until its next repair that is every resource, and a line per open
    of every one of them is noise at the level an operator reads."""
    score = _an_unrepaired_labelled_tabix_score(tmp_path)
    caplog.clear()

    with caplog.at_level(logging.DEBUG):
        score.open()

    assert [
        record.levelno for record in caplog.records
        if "no stored chromosome lengths" in record.getMessage()
    ] == [logging.DEBUG]


def test_table_extent_is_a_legal_source_that_answers_nothing_here(
    tmp_path: pathlib.Path,
) -> None:
    """A tabix score never measures a table extent: no contig has one,
    and the refusal names, best first, the sources that do."""
    score = _a_repaired_labelled_tabix_score(tmp_path).open()

    assert score.get_all_chrom_lengths(source="table_extent") == {}
    with pytest.raises(ValueError) as refusal:
        score.get_chrom_length("chr1", source=ChromLengthSource.TABLE_EXTENT)

    assert str(refusal.value) == (
        "table_extent has no length for chr1; the sources that answer it: "
        "['reference_genome', 'tabix_estimate']")
