# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Score-level chromosome lengths, resolved live through the table (gain#1413).

The three ``GenomicScore`` methods mirror ``ReferenceGenome``'s
``get_chrom_length`` / ``get_all_chrom_lengths``: an ``int`` or a
``ValueError``.  Underneath them, ``derive_chrom_lengths`` keeps the
tri-state answer the statistics region splitter needs (gain#509) -- a length,
or the ``ContigExtent`` reason there is none.
"""

import pathlib

import pytest
import pytest_mock
from gain.genomic_resources.genomic_scores import (
    GenomicScore,
    build_score_from_resource,
)
from gain.genomic_resources.genomic_scores.chrom_lengths import (
    ChromLengthSource,
)
from gain.genomic_resources.repository import GR_CONF_FILE_NAME
from gain.genomic_resources.testing import (
    build_inmemory_test_resource,
    convert_to_tab_separated,
)
from gain.genomic_resources.testing.builders import (
    a_bigwig_score,
    a_grr,
    a_position_score,
)


def _a_tabix_score(tmp_path: pathlib.Path) -> GenomicScore:
    builder = (
        a_position_score()
        .with_score("score", "float")
        .with_data("""
            chrom  pos_begin  score
            chr1   10         0.1
            chr1   2500       0.2
        """)
        .with_tabix()
    )
    repo = a_grr().with_resource("pos", builder).build_repo(tmp_path)
    return build_score_from_resource(repo.get_resource("pos"))


def _a_score_with_an_empty_mapped_contig() -> GenomicScore:
    # 'kept' maps onto a file contig with rows, 'empty' onto one with none.
    # Only the in-memory backend, holding the whole file, can PROVE a listed
    # contig empty (gain#509).
    res = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
                chrom_mapping:
                    filename: chrom_map.txt
            scores:
                - id: score
                  name: score
                  type: float
        """,
        "data.mem": convert_to_tab_separated("""
            chrom pos_begin score
            chr1  10        0.1
        """),
        "chrom_map.txt": convert_to_tab_separated("""
            chrom   file_chrom
            kept    chr1
            empty   chr99
        """),
    })
    return build_score_from_resource(res)


def test_tabix_score_answers_the_probes_bound_as_an_estimate(
    tmp_path: pathlib.Path,
) -> None:
    score = _a_tabix_score(tmp_path).open()

    length = score.get_chrom_length("chr1")

    # The table's own probe answers an upper bound, never the exact length;
    # the score passes that bound through and says what it is.
    assert length == score.table.find_chromosome_length("chr1")
    assert length >= 2500
    assert score.get_chrom_length_source("chr1") \
        is ChromLengthSource.TABIX_ESTIMATE


def test_bigwig_score_answers_the_header_length_exactly(
    tmp_path: pathlib.Path,
) -> None:
    builder = (
        a_bigwig_score()
        .with_score("bw", "float")
        .with_data("""
            chr1  0   10  0.11
        """)
        .with_chrom_lens({"chr1": 1000})
    )
    repo = a_grr().with_resource("bw", builder).build_repo(tmp_path)
    score = build_score_from_resource(repo.get_resource("bw")).open()

    # The header's 1000, not the rows' 10: a bigWig header carries the exact
    # size of every contig it lists, which is why the source is exact.
    assert score.get_chrom_length("chr1") == 1000
    assert score.get_chrom_length_source("chr1") is ChromLengthSource.BIGWIG
    assert score.get_chrom_length_source("chr1").is_exact


def test_inmemory_score_answers_the_extent_of_its_rows(
    tmp_path: pathlib.Path,
) -> None:
    builder = (
        a_position_score()
        .with_score("score", "float")
        .with_data("""
            chrom  pos_begin  pos_end  score
            chr1   10         20       0.1
            chr1   30         45       0.2
        """)
    )
    repo = a_grr().with_resource("pos", builder).build_repo(tmp_path)
    score = build_score_from_resource(repo.get_resource("pos")).open()

    # ``max(pos_end) + 1``: how far the rows reach, which says nothing about
    # how long the contig is -- so the source is named for what it is and
    # is not exact.
    assert score.get_chrom_length("chr1") == 46
    source = score.get_chrom_length_source("chr1")
    assert source is ChromLengthSource.TABLE_EXTENT
    assert not source.is_exact


# Both the length and its source refuse the same questions, in the same
# words: the source is the length's provenance and has none when there is no
# length.
_LENGTH_READS = ["get_chrom_length", "get_chrom_length_source"]


@pytest.mark.parametrize("read", _LENGTH_READS)
def test_a_closed_score_refuses_a_length(
    read: str, tmp_path: pathlib.Path,
) -> None:
    score = _a_tabix_score(tmp_path)

    with pytest.raises(ValueError, match="is not open"):
        getattr(score, read)("chr1")


@pytest.mark.parametrize("read", _LENGTH_READS)
def test_a_contig_the_score_does_not_carry_is_refused(
    read: str, tmp_path: pathlib.Path,
) -> None:
    score = _a_tabix_score(tmp_path).open()

    with pytest.raises(
            ValueError, match="chrX is not among the available chromosomes"):
        getattr(score, read)("chrX")


@pytest.mark.parametrize("read", _LENGTH_READS)
def test_a_contig_proven_empty_is_refused_as_such(read: str) -> None:
    score = _a_score_with_an_empty_mapped_contig().open()

    # A ValueError, not the extent: the int view has nothing to answer, and
    # the message says WHICH kind of nothing, since an operator acts on an
    # empty contig (a chrom_mapping naming a contig the file lacks) and an
    # undetermined one differently.
    with pytest.raises(ValueError, match="contig empty has no records"):
        getattr(score, read)("empty")


@pytest.mark.parametrize("read", _LENGTH_READS)
def test_a_contig_of_undeterminable_length_is_refused_as_such(
    read: str, tmp_path: pathlib.Path, mocker: pytest_mock.MockFixture,
) -> None:
    score = _a_tabix_score(tmp_path).open()
    # Patched where the probe lives (gain#509): the one branch that can fail
    # for a contig that demonstrably HAS records.
    mocker.patch(
        "gain.genomic_resources.genomic_position_table.table_tabix"
        ".get_chromosome_length_tabix",
        return_value=None)

    with pytest.raises(
            ValueError,
            match="could not determine the length of contig chr1"):
        getattr(score, read)("chr1")
