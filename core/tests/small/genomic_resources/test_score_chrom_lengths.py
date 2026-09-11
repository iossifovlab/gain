# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Score-level chromosome lengths, resolved live through the table (gain#1413).

The three ``GenomicScore`` methods mirror ``ReferenceGenome``'s
``get_chrom_length`` / ``get_all_chrom_lengths``: an ``int`` or a
``ValueError``.  Underneath them, ``derive_chrom_lengths`` keeps the
tri-state answer the statistics region splitter needs (gain#509) -- a length,
or the ``ContigExtent`` reason there is none.
"""

import pathlib
from collections.abc import Callable

import pytest
import pytest_mock
from gain.genomic_resources.genomic_position_table import ContigExtent
from gain.genomic_resources.genomic_scores import (
    GenomicScore,
    build_score_from_resource,
)
from gain.genomic_resources.genomic_scores.chrom_lengths import (
    ChromLength,
    ChromLengthSource,
    derive_chrom_lengths,
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
    a_vcf_info_score,
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
    score = _a_bigwig_score(tmp_path).open()

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


def test_derive_chrom_lengths_keeps_the_reason_a_contig_has_no_length(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockFixture,
) -> None:
    """The resolver's record is tri-state where the score's int view is not.

    A region splitter treats EMPTY and UNDETERMINED oppositely -- skip the
    first, read the second whole (gain#509) -- so the resolver hands both
    through as the extent rather than collapsing them into a raise.
    """
    builder = (
        a_position_score()
        .with_score("score", "float")
        .with_data("""
            chrom  pos_begin  score
            chr2   40         0.3
            chr1   10         0.1
        """)
        .with_tabix()
    )
    repo = a_grr().with_resource("pos", builder).build_repo(tmp_path)
    score = build_score_from_resource(repo.get_resource("pos")).open()
    # The probe fails for chr1 only.
    mocker.patch(
        "gain.genomic_resources.genomic_position_table.table_tabix"
        ".get_chromosome_length_tabix",
        side_effect=lambda _file, chrom, _step: (
            None if chrom == "chr1" else 100))

    resolved = derive_chrom_lengths(score)

    # One record per contig, in the table's order -- which is NOT sorted.
    assert list(resolved) == score.get_all_chromosomes() == ["chr2", "chr1"]
    assert resolved["chr2"] == ChromLength(
        length=100, source=ChromLengthSource.TABIX_ESTIMATE, extent=None)
    assert resolved["chr1"] == ChromLength(
        length=None, source=None, extent=ContigExtent.UNDETERMINED)


def test_derive_chrom_lengths_reports_a_proven_empty_contig() -> None:
    score = _a_score_with_an_empty_mapped_contig().open()

    resolved = derive_chrom_lengths(score)

    assert list(resolved) == ["kept", "empty"]
    assert resolved["kept"].source is ChromLengthSource.TABLE_EXTENT
    assert resolved["empty"] == ChromLength(
        length=None, source=None, extent=ContigExtent.EMPTY)


def test_get_all_chrom_lengths_holds_resolved_contigs_in_table_order(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockFixture,
) -> None:
    builder = (
        a_position_score()
        .with_score("score", "float")
        .with_data("""
            chrom  pos_begin  score
            chr3   40         0.3
            chr1   10         0.1
            chr2   20         0.2
        """)
        .with_tabix()
    )
    repo = a_grr().with_resource("pos", builder).build_repo(tmp_path)
    score = build_score_from_resource(repo.get_resource("pos")).open()
    mocker.patch(
        "gain.genomic_resources.genomic_position_table.table_tabix"
        ".get_chromosome_length_tabix",
        side_effect=lambda _file, chrom, _step: (
            None if chrom == "chr1" else 100))

    lengths = score.get_all_chrom_lengths()

    # chr1, unresolved, is simply absent -- the int view has nothing to say
    # for it and does not raise, unlike get_chrom_length asked directly.
    # The rest keep the table's order, which is not sorted.
    assert list(lengths.items()) == [("chr3", 100), ("chr2", 100)]


def test_get_all_chrom_lengths_refuses_a_closed_score(
    tmp_path: pathlib.Path,
) -> None:
    score = _a_tabix_score(tmp_path)

    with pytest.raises(ValueError, match="is not open"):
        score.get_all_chrom_lengths()


def _an_inmemory_score(tmp_path: pathlib.Path) -> GenomicScore:
    builder = (
        a_position_score()
        .with_score("score", "float")
        .with_data("""
            chrom  pos_begin  score
            chr1   10         0.1
        """)
    )
    repo = a_grr().with_resource("pos", builder).build_repo(tmp_path)
    return build_score_from_resource(repo.get_resource("pos"))


def _a_vcf_score(tmp_path: pathlib.Path) -> GenomicScore:
    builder = a_vcf_info_score().with_data("""
##fileformat=VCFv4.1
##INFO=<ID=scoreA,Number=1,Type=Float,Description="score A">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   10  .  A   T   .    .      scoreA=0.1
""")
    repo = a_grr().with_resource("vcf", builder).build_repo(tmp_path)
    return build_score_from_resource(repo.get_resource("vcf"))


def _a_bigwig_score(tmp_path: pathlib.Path) -> GenomicScore:
    builder = (
        a_bigwig_score()
        .with_score("bw", "float")
        .with_data("""
            chr1  0   10  0.11
        """)
        .with_chrom_lens({"chr1": 1000})
    )
    repo = a_grr().with_resource("bw", builder).build_repo(tmp_path)
    return build_score_from_resource(repo.get_resource("bw"))


@pytest.mark.parametrize("build", [
    pytest.param(_an_inmemory_score, id="inmemory"),
    pytest.param(_a_tabix_score, id="tabix"),
    pytest.param(_a_vcf_score, id="vcf"),
    pytest.param(_a_bigwig_score, id="bigwig"),
])
def test_a_table_sources_exactness_is_its_backends(
    build: Callable[[pathlib.Path], GenomicScore], tmp_path: pathlib.Path,
) -> None:
    """``is_exact`` and ``chrom_lengths_are_exact`` must classify alike.

    Coverage's ``resolve_chrom_lengths`` trusts a table's lengths as a
    denominator on the backend flag today; gain#1414 moves it onto the
    source's ``is_exact``.  That is only behaviour-preserving while the two
    agree for every backend, so the agreement is pinned here for each one.
    """
    score = build(tmp_path).open()

    source = score.get_chrom_length_source("chr1")

    assert source.is_exact == score.table.chrom_lengths_are_exact


def test_a_reference_genome_length_is_exact() -> None:
    # Not producible by a score in this slice (gain#1418 adds the rung), so
    # the member is asserted directly: it is the one source that beats a
    # bigWig header, and a caller filtering on exactness must keep it.
    assert ChromLengthSource.REFERENCE_GENOME.is_exact
