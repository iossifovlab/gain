"""``GenomicScore.fetch_region_value_arrays`` and its capability query.

The bulk column-array region read, exposed on the score facade (gain#398).
Before this, the only way to reach it was through ``score.table`` plus an
``isinstance`` chain on the backend class -- including a ``not isinstance(VCF)``
that a caller had to know to write, since the VCF backend *inherits* the tabix
method it cannot honour.
"""
# pylint: disable=C0116,W0212,W0621
import pathlib

import numpy as np
import pytest
from gain.genomic_resources.genomic_scores import AlleleScore, PositionScore
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing.builders import (
    a_bigwig_score,
    a_position_score,
    a_vcf_info_score,
)


def _vcf_score(tmp_path: pathlib.Path) -> GenomicResource:
    return a_vcf_info_score().with_data("""
##fileformat=VCFv4.1
##INFO=<ID=scoreA,Number=1,Type=Float,Description="score A">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   10  .  A   T   .    .      scoreA=0.1
""").build_resource(tmp_path)


def _multiscore_tabix(tmp_path: pathlib.Path) -> GenomicResource:
    return (
        a_position_score()
        .with_score("s1", "float")
        .with_score("s2", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  s1    s2
            chr1   1          3        0.1   0.9
            chr1   4          4        0.5   .
            chr1   5          10       0.95  0.2
            chr1   11         11       .     0.0
            chr1   12         20       1.0   0.5
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )


def test_tabix_score_fetches_value_arrays_keyed_by_score_id(
    tmp_path: pathlib.Path,
) -> None:
    resource = _multiscore_tabix(tmp_path)

    with PositionScore(resource).open() as score:
        batches = list(
            score.fetch_region_value_arrays("chr1", 1, 20, ["s1"]))

    assert len(batches) == 1
    pos_begin, pos_end, cols = batches[0]
    assert np.array_equal(pos_begin, [1, 4, 5, 11, 12])
    assert np.array_equal(pos_end, [3, 4, 10, 11, 20])
    # Keyed by score id -- the caller never sees the payload column index.
    assert set(cols) == {"s1"}
    # Cells are handed back RAW: no na_values handling, no coercion.  The
    # configured NA sentinel "." arrives as the string it is in the file.
    assert list(cols["s1"]) == ["0.1", "0.5", "0.95", ".", "1.0"]


def test_capability_is_answerable_without_opening_the_score(
    tmp_path: pathlib.Path,
) -> None:
    # ``self.table`` is built in ``GenomicScore.__init__``, so the capability
    # is known at construction -- a caller does not have to open a score (and
    # so open its file) merely to find out whether the bulk read is available.
    tabix = PositionScore(_multiscore_tabix(tmp_path / "tabix"))
    vcf = AlleleScore(_vcf_score(tmp_path / "vcf"))

    assert tabix.supports_region_value_arrays() is True
    # A VCF table subclasses the tabix one and so INHERITS the method, but its
    # payload is (variant, allele_index) and its columns are INFO names, not
    # the integer payload indices the arrays contract uses.
    assert vcf.supports_region_value_arrays() is False


def test_vcf_score_refuses_to_fetch_value_arrays(
    tmp_path: pathlib.Path,
) -> None:
    # No silent emulation: unpacking records into arrays keys columns by
    # raw-row payload index, which a VCF record does not have.  Refusing is
    # the honest answer, and it points at the query to ask instead.
    with AlleleScore(_vcf_score(tmp_path)).open() as score, \
            pytest.raises(TypeError, match="supports_region_value_arrays"):
        list(score.fetch_region_value_arrays("chr1", 1, 100, ["scoreA"]))


def test_bigwig_score_value_arrays_match_the_record_read(
    tmp_path: pathlib.Path,
) -> None:
    resource = (
        a_bigwig_score()
        .with_score("bw", "float")
        .with_data("""
            chr1  0  2  0.0
            chr1  2  4  2.5
            chr1  4  6  4.0
        """)
        .with_chrom_lens({"chr1": 100})
        .build_resource(tmp_path)
    )

    with PositionScore(resource).open() as score:
        batches = list(score.fetch_region_value_arrays("chr1", 1, 6, ["bw"]))
        lines = list(score.fetch_lines("chr1", 1, 6))

    spans = [
        (int(begin), int(end))
        for pos_begin, pos_end, _ in batches
        for begin, end in zip(pos_begin, pos_end, strict=True)
    ]
    values = [float(v) for _, _, cols in batches for v in cols["bw"]]

    assert spans == [(line.pos_begin, line.pos_end) for line in lines]
    assert values == [line.get_score("bw") for line in lines]


def test_fetching_value_arrays_from_an_unopened_score_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    score = PositionScore(_multiscore_tabix(tmp_path))
    with pytest.raises(ValueError, match="is not open"):
        list(score.fetch_region_value_arrays("chr1", 1, 20, ["s1"]))


def test_fetching_value_arrays_for_an_unknown_chromosome_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    with PositionScore(_multiscore_tabix(tmp_path)).open() as score, \
            pytest.raises(ValueError, match="not among the available"):
        list(score.fetch_region_value_arrays("chrZZ", 1, 20, ["s1"]))
