# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""The chromosome-length resolver of a genomic score (gain#1413).

``derive_chrom_length`` / ``derive_chrom_lengths`` keep the tri-state answer
the statistics region splitter needs (gain#509) -- a length, or the
``ContigExtent`` reason there is none -- and the source of each length is
whatever the backend declares its lengths to be.  A caller holding a
``ReferenceGenome`` hands it to the resolver, which answers every contig the
genome lists from it first, exactly, and per contig (gain#1418).  The caller
that resolves the genome from the score's label is the implementation, pinned
in test_genomic_scores_impl_chrom_lengths.
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
from gain.genomic_resources.reference_genome import (
    ReferenceGenome,
    build_reference_genome_from_resource,
)
from gain.genomic_resources.testing.builders import (
    a_bigwig_score,
    a_position_score,
    a_reference_genome,
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

    resolved = derive_chrom_length(score, "chr1")

    # The table's own probe answers an upper bound, never the exact length;
    # the resolver passes that bound through and says so.
    assert resolved.length == score.table.find_chromosome_length("chr1")
    assert resolved.length is not None
    assert resolved.length >= 2500
    assert resolved.source is not None
    assert not resolved.source.is_exact


def test_bigwig_score_answers_the_header_length_exactly(
    tmp_path: pathlib.Path,
) -> None:
    score = _a_bigwig_score(tmp_path).open()

    resolved = derive_chrom_length(score, "chr1")

    # The header's 1000, not the rows' 20: a bigWig header carries the exact
    # size of every contig it lists, which is why the source is exact.
    assert resolved.length == 1000
    assert resolved.source is not None
    assert resolved.source.is_exact


def test_inmemory_score_answers_the_extent_of_its_rows(
    tmp_path: pathlib.Path,
) -> None:
    score = _an_inmemory_score(tmp_path).open()

    resolved = derive_chrom_length(score, "chr1")

    # ``max(pos_end) + 1``: how far the rows reach, which says nothing about
    # how long the contig is -- so the source is not exact.
    assert resolved.length == 46
    assert resolved.source is not None
    assert not resolved.source.is_exact


@pytest.mark.parametrize("read", [
    pytest.param(
        lambda score: derive_chrom_length(score, "chr1"),
        id="derive_chrom_length"),
    pytest.param(derive_chrom_lengths, id="derive_chrom_lengths"),
])
def test_a_closed_score_refuses_every_length_read(
    read: Callable[[GenomicScore], object], tmp_path: pathlib.Path,
) -> None:
    score = _a_tabix_score(tmp_path)

    with pytest.raises(ValueError, match="not open"):
        read(score)


def test_a_contig_the_score_does_not_carry_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    score = _a_tabix_score(tmp_path).open()

    # A bad question, as opposed to an absent answer: refused, in the
    # table's words, rather than answered with an extent.
    with pytest.raises(ValueError, match="chrX"):
        derive_chrom_length(score, "chrX")


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


def _a_genome_listing(
    tmp_path: pathlib.Path, **lengths: int,
) -> ReferenceGenome:
    """A genome carrying exactly the named contigs at the given lengths."""
    builder = a_reference_genome()
    for chrom, length in lengths.items():
        builder = builder.with_chromosome(chrom, "A" * length)
    return build_reference_genome_from_resource(
        builder.build_resource(tmp_path / "genome"))


def test_derive_chrom_lengths_answers_from_the_genome_where_it_lists_the_contig(
    tmp_path: pathlib.Path,
) -> None:
    """The genome rung is exact and comes first, per contig (gain#1418).

    chr1 is answered by the genome -- its true 3000, not the probe's bound
    past 2500 -- and says so.  chrM is carried by the score but not by the
    genome, and falls through to the table for that contig alone: the
    genome does not veto a contig it merely does not know.
    """
    score = _a_tabix_score(tmp_path / "score", rows="""
        chrom  pos_begin  score
        chr1   10         0.1
        chr1   2500       0.2
        chrM   40         0.3
    """).open()
    genome = _a_genome_listing(tmp_path, chr1=3000)

    resolved = derive_chrom_lengths(score, genome)

    assert resolved["chr1"] == ChromLength(
        length=3000, source=ChromLengthSource.REFERENCE_GENOME, extent=None)
    # 48 is the probe's bound for a lone row at 40, as the region-split pin
    # in test_genomic_scores_impl measures for the same rows.
    assert resolved["chrM"] == ChromLength(
        length=48, source=ChromLengthSource.TABIX_ESTIMATE, extent=None)


def test_the_genome_widens_no_contig_universe(
    tmp_path: pathlib.Path,
) -> None:
    # A genome usually lists far more contigs than a score carries, in its
    # own order.  The answer is keyed by the SCORE's contigs, in the
    # TABLE's order -- chr3 is not added, chr2 is not moved ahead of chr1
    # -- because a whole-reference denominator is coverage's business
    # (gain#1041) and a consumer splitting regions walks the table.
    score = _a_tabix_score(tmp_path / "score", rows="""
        chrom  pos_begin  score
        chr1   10         0.1
        chr2   40         0.3
    """).open()
    genome = _a_genome_listing(tmp_path, chr3=100, chr2=500, chr1=3000)

    resolved = derive_chrom_lengths(score, genome)

    assert list(resolved) == ["chr1", "chr2"]


def test_a_contig_the_genome_lacks_still_reports_why_it_has_no_length(
    tmp_path: pathlib.Path,
) -> None:
    # The fallthrough is per contig and keeps the table's tri-state answer:
    # 'kept' is the genome's, 'empty' -- unknown to the genome, proven
    # empty by the table -- is still EMPTY, not an error and not a length.
    score = _a_score_with_an_empty_mapped_contig(tmp_path / "score").open()
    genome = _a_genome_listing(tmp_path, kept=200)

    resolved = derive_chrom_lengths(score, genome)

    assert resolved["kept"] == ChromLength(
        length=200, source=ChromLengthSource.REFERENCE_GENOME, extent=None)
    assert resolved["empty"] == ChromLength(
        length=None, source=None, extent=ContigExtent.EMPTY)


def test_the_genome_answers_no_question_the_table_would_refuse(
    tmp_path: pathlib.Path,
) -> None:
    """A bad question is refused in the table's words, genome or not.

    The genome lists chr2 and the score does not carry it; the genome
    lists chr1 and the score is closed.  Both would be answerable off the
    genome alone, and neither may be: the resolver's refusals are the
    table's, and a genome must not turn a contig the score has no data
    for into a length.
    """
    score = _a_tabix_score(tmp_path / "score")
    genome = _a_genome_listing(tmp_path, chr1=3000, chr2=500)

    with pytest.raises(ValueError, match="not open"):
        derive_chrom_length(score, "chr1", genome)
    score.open()
    with pytest.raises(ValueError, match="chr2"):
        derive_chrom_length(score, "chr2", genome)


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
    what coverage's ``resolve_chrom_lengths`` trusts a denominator on
    (gain#1414) -- a member that came to read as exact would silently
    turn a probe's upper bound into a percentage.
    """
    score = build(tmp_path).open()

    source = derive_chrom_length(score, "chr1").source

    assert source is expected_source
    assert source.is_exact is expected_exact


def test_a_reference_genome_length_is_exact() -> None:
    # Asserted on the member directly: it is the one source that beats a
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
