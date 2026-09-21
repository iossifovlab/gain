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
    scan,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing.builders import (
    a_bigwig_score,
    a_grr,
    a_position_score,
    a_reference_genome,
)

from .conftest import captured_warnings, leave_as_a_pointer
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
    return _repaired_view(where)


def _repaired_view(
    where: pathlib.Path,
) -> tuple[PositionScoreImplementation, GenomicResourceRepo]:
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


def _a_bigwig_repo(
    where: pathlib.Path, *, labelled: bool = True,
) -> GenomicResourceRepo:
    """The same chr1 rows as a bigWig whose header lists chr1 alone,
    with the genome of chr1 and chr2 beside it, labelled or not."""
    score = (
        a_bigwig_score()
        .with_data(
            """
            chr1  4   9   0.1
            chr1  29  33  0.2
            """)
        .with_chrom_lens({"chr1": 100}))
    if labelled:
        score = score.with_labels(reference_genome=GENOME)
    return (
        a_grr()
        .with_resource(SCORE, score)
        .with_resource(
            GENOME,
            a_reference_genome()
            .with_chromosome("chr1", "A" * 100)
            .with_chromosome("chr2", "A" * 300))
        .build_repo(where)
    )


def test_a_repaired_bigwig_score_with_its_genome_at_hand_is_unchanged(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
) -> None:
    """The genome rung still comes first: the whole genome, roll-up
    included, and no header read."""
    _a_bigwig_repo(tmp_path)
    impl, repo = _repaired(tmp_path)
    opened = mocker.spy(impl.score, "open")

    page = impl.get_info(repo=repo)

    assert page.count(">9.00%<") == 1  # the chr1 row: 9 of 100
    assert ">2.25%<" in page  # the global row: 9 of the genome's 400
    assert "1 contig with no values (300 bp)" in page
    opened.assert_not_called()


def test_a_bigwig_score_whose_genome_is_gone_is_priced_from_the_file(
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockerFixture,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Over the score's own contig, so no roll-up of the genome's chr2;
    the header is not read for what the file already says."""
    _a_bigwig_repo(tmp_path)
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


def test_an_unrepaired_unlabelled_bigwig_score_opens_once_for_its_header(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
) -> None:
    """Nothing stored: the header is the one exact thing a live render
    reads, as before (gain#1448) -- and the page is priced by it."""
    repo = _a_bigwig_repo(tmp_path, labelled=False)
    resource = repo.get_resource(SCORE)
    scan.do_noregion_histograms(resource)
    impl = PositionScoreImplementation(resource)
    opened = mocker.spy(impl.score, "open")

    page = impl.get_info(repo=repo)

    assert page.count(">9.00%<") == 2  # the chr1 row and the global row
    opened.assert_called_once()


def test_a_repaired_unlabelled_bigwig_score_is_priced_without_opening(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
) -> None:
    """The header's sizes, as the repair stored them."""
    _a_bigwig_repo(tmp_path, labelled=False)
    impl, repo = _repaired(tmp_path)
    opened = mocker.spy(impl.score, "open")

    page = impl.get_info(repo=repo)

    assert page.count(">9.00%<") == 2  # the chr1 row and the global row
    opened.assert_not_called()


def test_a_pointer_only_bigwig_with_a_current_file_is_priced_from_it(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
) -> None:
    """An unpulled DVC checkout: the sidecar vouches for the key, the
    file answers, and there is nothing to open."""
    _a_bigwig_repo(tmp_path, labelled=False)
    _repaired(tmp_path)
    leave_as_a_pointer(tmp_path / SCORE / "data.bw")
    impl, _ = _repaired_view(tmp_path)
    opened = mocker.spy(impl.score, "open")

    page = impl.get_info()

    assert page.count(">9.00%<") == 2  # the chr1 row and the global row
    opened.assert_not_called()


def test_a_pointer_only_bigwig_with_no_file_renders_raw_counts(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
) -> None:
    """Nothing stored and no header to read: raw counts, not a failed
    page -- the render is not what reports an unpulled payload."""
    _a_bigwig_repo(tmp_path, labelled=False)
    _repaired(tmp_path)
    (tmp_path / SCORE / "statistics" / "chrom_lengths.json").unlink()
    leave_as_a_pointer(tmp_path / SCORE / "data.bw")
    impl, _ = _repaired_view(tmp_path)
    opened = mocker.spy(impl.score, "open")

    page = impl.get_info()

    assert f">{COVERED}<" in page
    assert "Covered %" not in page
    opened.assert_not_called()
