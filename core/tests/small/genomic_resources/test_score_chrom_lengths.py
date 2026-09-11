# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Score-level chromosome lengths, resolved live through the table (gain#1413).

The three ``GenomicScore`` methods mirror ``ReferenceGenome``'s
``get_chrom_length`` / ``get_all_chrom_lengths``: an ``int`` or a
``ValueError``.  Underneath them, ``derive_chrom_lengths`` keeps the
tri-state answer the statistics region splitter needs (gain#509) -- a length,
or the ``ContigExtent`` reason there is none -- and the source of each length
is whatever the backend declares its lengths to be.
"""

import pathlib
from collections.abc import Callable
from types import SimpleNamespace

import pytest
import pytest_mock
from gain.genomic_resources.genomic_position_table import (
    ChromLengthSource,
    ContigExtent,
)
from gain.genomic_resources.genomic_scores import (
    GenomicScore,
    build_score_from_resource,
)
from gain.genomic_resources.genomic_scores.chrom_lengths import (
    ChromLength,
    derive_chrom_length,
    derive_chrom_lengths,
)
from gain.genomic_resources.testing.builders import (
    a_bigwig_score,
    a_position_score,
    a_vcf_info_score,
)

from .genomic_position_table.test_genomic_position_table import (
    _OnlyFindsLengths,
)
from .genomic_position_table.test_table_lifetime import (
    _concrete_backends_in_the_tree,
)

# One score per backend, each carrying ``chr1``.  The tabix rows are far
# enough apart (10 and 2500) that the probe's bound is visibly a bound.
_TABIX_ROWS = """
    chrom  pos_begin  score
    chr1   10         0.1
    chr1   2500       0.2
"""


def _an_inmemory_score(tmp_path: pathlib.Path) -> GenomicScore:
    return build_score_from_resource(
        a_position_score()
        .with_data("""
            chrom  pos_begin  pos_end  score
            chr1   10         20       0.1
            chr1   30         45       0.2
        """)
        .build_resource(tmp_path))


def _a_tabix_score(
    tmp_path: pathlib.Path, rows: str = _TABIX_ROWS,
) -> GenomicScore:
    return build_score_from_resource(
        a_position_score().with_data(rows).with_tabix()
        .build_resource(tmp_path))


def _a_vcf_score(tmp_path: pathlib.Path) -> GenomicScore:
    return build_score_from_resource(
        a_vcf_info_score().build_resource(tmp_path))


def _a_bigwig_score(tmp_path: pathlib.Path) -> GenomicScore:
    # The builder's default header lists chr1 at 1000, well past its rows.
    return build_score_from_resource(
        a_bigwig_score().build_resource(tmp_path))


def _a_score_with_an_empty_mapped_contig(
    tmp_path: pathlib.Path,
) -> GenomicScore:
    # 'kept' maps onto a file contig with rows, 'empty' onto one with none.
    # Only the in-memory backend, holding the whole file, can PROVE a listed
    # contig empty (gain#509).
    return build_score_from_resource(
        a_position_score()
        .with_data("""
            chrom  pos_begin  score
            chr1   10         0.1
        """)
        .with_chrom_mapping_file(kept="chr1", empty="chr99")
        .build_resource(tmp_path))


def _the_probe_fails_for(
    mocker: pytest_mock.MockFixture, chrom: str,
) -> None:
    """Make the tabix probe answer nothing for ``chrom``, and 100 otherwise.

    Patched where the probe lives (gain#509): the one branch that can fail
    to determine a length for a contig that demonstrably HAS records.
    """
    mocker.patch(
        "gain.genomic_resources.genomic_position_table.table_tabix"
        ".get_chromosome_length_tabix",
        side_effect=lambda _file, fchrom, _step: (
            None if fchrom == chrom else 100))


def test_tabix_score_answers_the_probes_bound_as_an_estimate(
    tmp_path: pathlib.Path,
) -> None:
    score = _a_tabix_score(tmp_path).open()

    length = score.get_chrom_length("chr1")

    # The table's own probe answers an upper bound, never the exact length;
    # the score passes that bound through and says so.
    assert length == score.table.find_chromosome_length("chr1")
    assert length >= 2500
    assert not score.get_chrom_length_source("chr1").is_exact


def test_bigwig_score_answers_the_header_length_exactly(
    tmp_path: pathlib.Path,
) -> None:
    score = _a_bigwig_score(tmp_path).open()

    # The header's 1000, not the rows' 20: a bigWig header carries the exact
    # size of every contig it lists, which is why the source is exact.
    assert score.get_chrom_length("chr1") == 1000
    assert score.get_chrom_length_source("chr1").is_exact


def test_inmemory_score_answers_the_extent_of_its_rows(
    tmp_path: pathlib.Path,
) -> None:
    score = _an_inmemory_score(tmp_path).open()

    # ``max(pos_end) + 1``: how far the rows reach, which says nothing about
    # how long the contig is -- so the source is not exact.
    assert score.get_chrom_length("chr1") == 46
    assert not score.get_chrom_length_source("chr1").is_exact


# Both the length and its source refuse the same questions, in the same
# words: the source is the length's provenance and has none when there is no
# length.
_LENGTH_READS = ["get_chrom_length", "get_chrom_length_source"]


@pytest.mark.parametrize("read", [
    pytest.param(
        lambda score: score.get_chrom_length("chr1"), id="get_chrom_length"),
    pytest.param(
        lambda score: score.get_chrom_length_source("chr1"),
        id="get_chrom_length_source"),
    pytest.param(
        lambda score: score.get_all_chrom_lengths(),
        id="get_all_chrom_lengths"),
    pytest.param(derive_chrom_lengths, id="derive_chrom_lengths"),
])
def test_a_closed_score_refuses_every_length_read(
    read: Callable[[GenomicScore], object], tmp_path: pathlib.Path,
) -> None:
    score = _a_tabix_score(tmp_path)

    with pytest.raises(ValueError, match="is not open"):
        read(score)


@pytest.mark.parametrize("read", _LENGTH_READS)
def test_a_contig_the_score_does_not_carry_is_refused(
    read: str, tmp_path: pathlib.Path,
) -> None:
    score = _a_tabix_score(tmp_path).open()

    with pytest.raises(
            ValueError, match="chrX is not among the available chromosomes"):
        getattr(score, read)("chrX")


@pytest.mark.parametrize("read", _LENGTH_READS)
def test_a_contig_proven_empty_is_refused_as_such(
    read: str, tmp_path: pathlib.Path,
) -> None:
    score = _a_score_with_an_empty_mapped_contig(tmp_path).open()

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
    _the_probe_fails_for(mocker, "chr1")

    with pytest.raises(
            ValueError,
            match="could not determine the length of contig chr1"):
        getattr(score, read)("chr1")


def test_the_score_refuses_a_lengthless_contig_in_the_tables_words(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockFixture,
) -> None:
    """The two refusals are one message, not two copies of it.

    The issue asked for the wording ``get_chromosome_length`` already uses;
    this pins that the score's read and the table's raising view say the
    same thing for the same contig, so the two cannot drift apart.
    """
    score = _a_tabix_score(tmp_path).open()
    _the_probe_fails_for(mocker, "chr1")

    with pytest.raises(ValueError) as from_the_table:
        score.table.get_chromosome_length("chr1")
    with pytest.raises(ValueError) as from_the_score:
        score.get_chrom_length("chr1")

    assert str(from_the_score.value) == str(from_the_table.value)


def test_derive_chrom_lengths_keeps_the_reason_a_contig_has_no_length(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockFixture,
) -> None:
    """The resolver's record is tri-state where the score's int view is not.

    A region splitter treats EMPTY and UNDETERMINED oppositely -- skip the
    first, read the second whole (gain#509) -- so the resolver hands both
    through as the extent rather than collapsing them into a raise.
    """
    score = _a_tabix_score(tmp_path, rows="""
        chrom  pos_begin  score
        chr2   40         0.3
        chr1   10         0.1
    """).open()
    _the_probe_fails_for(mocker, "chr1")

    resolved = derive_chrom_lengths(score)

    # One record per contig, in the table's order -- which is NOT sorted.
    assert list(resolved) == score.get_all_chromosomes() == ["chr2", "chr1"]
    assert resolved["chr2"] == ChromLength(
        length=100, source=ChromLengthSource.TABIX_ESTIMATE, extent=None)
    assert resolved["chr1"] == ChromLength(
        length=None, source=None, extent=ContigExtent.UNDETERMINED)


def test_derive_chrom_lengths_reports_a_proven_empty_contig(
    tmp_path: pathlib.Path,
) -> None:
    score = _a_score_with_an_empty_mapped_contig(tmp_path).open()

    resolved = derive_chrom_lengths(score)

    assert list(resolved) == ["kept", "empty"]
    assert resolved["kept"].source is ChromLengthSource.TABLE_EXTENT
    assert resolved["empty"] == ChromLength(
        length=None, source=None, extent=ContigExtent.EMPTY)


def test_get_all_chrom_lengths_holds_resolved_contigs_in_table_order(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockFixture,
) -> None:
    score = _a_tabix_score(tmp_path, rows="""
        chrom  pos_begin  score
        chr3   40         0.3
        chr1   10         0.1
        chr2   20         0.2
    """).open()
    _the_probe_fails_for(mocker, "chr1")

    lengths = score.get_all_chrom_lengths()

    # chr1, unresolved, is simply absent -- the int view has nothing to say
    # for it and does not raise, unlike get_chrom_length asked directly.
    # The rest keep the table's order, which is not sorted.
    assert list(lengths.items()) == [("chr3", 100), ("chr2", 100)]


@pytest.mark.parametrize(("build", "expected_source", "expected_exact"), [
    pytest.param(
        _an_inmemory_score, ChromLengthSource.TABLE_EXTENT, False,
        id="inmemory"),
    pytest.param(
        _a_tabix_score, ChromLengthSource.TABIX_ESTIMATE, False,
        id="tabix"),
    pytest.param(
        _a_vcf_score, ChromLengthSource.TABIX_ESTIMATE, False,
        id="vcf"),
    pytest.param(
        _a_bigwig_score, ChromLengthSource.BIGWIG, True,
        id="bigwig"),
])
def test_each_backends_source_and_its_exactness(
    build: Callable[[pathlib.Path], GenomicScore],
    expected_source: ChromLengthSource,
    expected_exact: bool,
    tmp_path: pathlib.Path,
) -> None:
    """What each backend declares its lengths to be, and whether to trust them.

    The member is pinned per backend because the exactness alone cannot
    tell a VCF labelled ``TABLE_EXTENT`` from one labelled
    ``TABIX_ESTIMATE``; the exactness is pinned as a literal because it is
    what coverage's ``resolve_chrom_lengths`` -- through the table's
    ``chrom_lengths_are_exact``, now derived from the same declaration --
    trusts a denominator on, and gain#1414 moves it onto ``is_exact``.
    """
    score = build(tmp_path).open()

    source = score.get_chrom_length_source("chr1")

    assert source is expected_source
    assert source.is_exact is expected_exact
    assert score.table.chrom_lengths_are_exact is expected_exact


def test_a_reference_genome_length_is_exact() -> None:
    # Not producible by a score in this slice (gain#1418 adds the rung), so
    # the member is asserted directly: it is the one source that beats a
    # bigWig header, and a caller filtering on exactness must keep it.
    assert ChromLengthSource.REFERENCE_GENOME.is_exact


def test_every_backend_in_the_tree_declares_its_chrom_length_source() -> None:
    """The declaration is an obligation on every concrete backend.

    Swept from the backend package rather than listed by name, so a fifth
    backend is held to it the moment it exists (the sweep's own vacuity
    guard is ``test_the_backend_sweep_walks_the_backend_package``).
    """
    undeclared = [
        klass.__name__
        for klass in _concrete_backends_in_the_tree()
        if not isinstance(
            getattr(klass, "chrom_length_source", None), ChromLengthSource)
    ]

    assert undeclared == [], (
        f"backend(s) {undeclared} do not declare chrom_length_source; say "
        f"what find_chromosome_length measures on that format")


def test_a_backend_that_has_not_declared_its_source_is_refused() -> None:
    """No default: a silent inherited label would carry a trust level too."""
    score = SimpleNamespace(table=_OnlyFindsLengths(10))

    with pytest.raises(AttributeError, match="chrom_length_source"):
        derive_chrom_length(score, "chr1")  # type: ignore[arg-type]
