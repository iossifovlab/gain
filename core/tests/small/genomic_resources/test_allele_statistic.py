# pylint: disable=C0114,C0116,W0212,W0621
import json

import pytest
from gain.genomic_resources.statistics.alleles import (
    AlleleStatistics,
    RegionAlleles,
    build_allele_display,
)


def _region(
    chrom: str = "chr1",
    start: int | None = 1,
    end: int | None = 100,
) -> RegionAlleles:
    return RegionAlleles(chrom, start, end)


def test_a_row_outside_the_region_is_not_counted() -> None:
    region = _region(start=10, end=20)

    region.add_allele(9, "A", "G")
    region.add_allele(21, "A", "G")
    region.add_allele(15, "A", "G")

    assert region.counts().allele_count == 1


def test_rows_at_one_position_count_one_covered_position() -> None:
    region = _region()

    region.add_allele(10, "A", "G")
    region.add_allele(10, "A", "C")
    region.add_allele(11, "A", "G")

    counts = region.counts()
    assert (counts.allele_count, counts.covered_positions) == (3, 2)


def test_substitution_rows_land_in_their_matrix_cells() -> None:
    region = _region()

    region.add_allele(10, "A", "G")
    region.add_allele(11, "A", "G")
    region.add_allele(12, "C", "T")

    matrix = region.counts().substitution_matrix
    assert matrix is not None
    assert matrix["A", "G"] == 2
    assert matrix["C", "T"] == 1
    assert sum(matrix.values()) == 3


def test_soft_masked_and_identity_pairs_land_in_their_cells() -> None:
    region = _region()

    region.add_allele(10, "a", "g")
    region.add_allele(11, "A", "G")
    region.add_allele(12, "T", "T")

    matrix = region.counts().substitution_matrix
    assert matrix is not None
    assert matrix["A", "G"] == 2
    assert matrix["T", "T"] == 1
    assert sum(matrix.values()) == 3
    assert region.counts().class_counts["substitution"] == 3


def test_rows_that_are_not_substitutions_land_in_no_cell() -> None:
    region = _region()

    region.add_allele(10, "N", "A")     # other: N is not a base
    region.add_allele(11, "A", "AT")    # insertion
    region.add_allele(12, "CT", "C")    # deletion
    region.add_allele(13, "AC", "GT")   # complex
    region.add_allele(14, None, None)   # other: missing alleles
    region.add_allele(15, "", "A")      # other: empty string

    matrix = region.counts().substitution_matrix
    assert matrix is not None
    assert sum(matrix.values()) == 0
    assert region.counts().allele_count == 6


def test_merge_adds_the_counts_of_the_adjacent_region() -> None:
    left = _region(start=1, end=10)
    left.add_allele(10, "A", "G")
    right = _region(start=11, end=20)
    right.add_allele(11, "A", "AT")

    left.merge(right)

    counts = left.counts()
    assert (counts.allele_count, counts.covered_positions) == (2, 2)
    assert counts.class_counts["substitution"] == 1
    assert counts.class_counts["insertion"] == 1


def test_merge_adds_the_matrices_of_the_adjacent_regions() -> None:
    left = _region(start=1, end=10)
    left.add_allele(5, "A", "G")
    right = _region(start=11, end=20)
    right.add_allele(11, "A", "G")
    right.add_allele(12, "C", "A")

    left.merge(right)

    matrix = left.counts().substitution_matrix
    assert matrix is not None
    assert matrix["A", "G"] == 2
    assert matrix["C", "A"] == 1
    assert sum(matrix.values()) == 3


def test_merge_refuses_a_pair_from_two_chromosomes() -> None:
    left = _region("chr1", 1, 10)
    right = _region("chr2", 11, 20)

    with pytest.raises(ValueError, match="chromosome boundaries"):
        left.merge(right)


def test_merge_refuses_regions_that_are_not_adjacent() -> None:
    left = _region(start=1, end=10)
    right = _region(start=15, end=20)

    with pytest.raises(ValueError, match="adjacent-and-in-order"):
        left.merge(right)


def test_serialized_counts_round_trip() -> None:
    statistics = AlleleStatistics()
    region = _region()
    region.add_allele(10, "A", "G")
    region.add_allele(10, "CT", "C")
    statistics.fold_region(region)

    restored = AlleleStatistics.deserialize(statistics.serialize())

    assert restored.by_chromosome() == statistics.by_chromosome()
    assert restored.global_counts() == statistics.global_counts()


def test_deserialize_ignores_keys_it_does_not_know() -> None:
    # Forward compatibility, as the coverage statistic has it: a file
    # carrying fields a later slice added still reads here.
    content = json.dumps({
        "format_version": 1,
        "chromosomes": {
            "chr1": {
                "allele_count": 2,
                "covered_positions": 1,
                "class_counts": {"substitution": 2},
                "ts_tv": 1.0,
            },
        },
        "global": {"allele_count": 2, "covered_positions": 1},
        "unknown_section": [1, 2, 3],
    })

    statistics = AlleleStatistics.deserialize(content)

    counts = statistics.global_counts()
    assert (counts.allele_count, counts.covered_positions) == (2, 1)
    assert counts.class_counts["substitution"] == 2
    assert counts.class_counts["other"] == 0


def test_a_file_without_the_matrix_reads_as_matrix_unknown() -> None:
    # Files written between gain#777 and this slice carry counts and
    # class totals but no matrix.  Unknown must stay distinguishable
    # from a genuine matrix of zeros -- and must not resurface as one
    # on the next write.
    content = json.dumps({
        "format_version": 1,
        "chromosomes": {
            "chr1": {
                "allele_count": 2,
                "covered_positions": 1,
                "class_counts": {"substitution": 2},
            },
        },
    })

    statistics = AlleleStatistics.deserialize(content)

    assert statistics.global_counts().substitution_matrix is None
    assert "substitution_matrix" not in statistics.serialize()


def test_the_global_matrix_is_unknown_when_any_chromosome_lacks_it() -> None:
    statistics = AlleleStatistics()
    scanned = _region("chr1")
    scanned.add_allele(10, "A", "G")
    statistics.fold_region(scanned)
    statistics.fold_region(RegionAlleles.frozen(
        "chr2", 1, 1, {"substitution": 1}))

    counts = statistics.global_counts()

    assert counts.substitution_matrix is None
    assert counts.class_counts["substitution"] == 2


def _statistics_of(region: RegionAlleles) -> AlleleStatistics:
    statistics = AlleleStatistics()
    statistics.fold_region(region)
    return statistics


def test_ts_tv_splits_the_off_diagonal_and_skips_the_diagonal() -> None:
    region = _region()
    region.add_allele(10, "A", "G")   # transition
    region.add_allele(11, "G", "A")   # transition
    region.add_allele(12, "C", "T")   # transition
    region.add_allele(13, "A", "C")   # transversion
    region.add_allele(14, "A", "A")   # identity: neither

    display = build_allele_display(_statistics_of(region))

    assert display.transitions == 3
    assert display.transversions == 1
    assert display.ts_tv == 3.0


def test_ts_tv_is_not_applicable_without_transversions() -> None:
    # All transitions -- the ratio has a zero denominator, and identity
    # rows must not sneak into it as transversions.
    region = _region()
    region.add_allele(10, "A", "G")
    region.add_allele(11, "T", "T")

    display = build_allele_display(_statistics_of(region))

    assert display.has_matrix
    assert display.ts_tv is None


def test_display_over_a_matrixless_file_has_no_matrix() -> None:
    display = build_allele_display(_statistics_of(
        RegionAlleles.frozen("chr1", 1, 1, {"substitution": 1})))

    assert not display.has_matrix
    assert display.ts_tv is None


def test_display_rows_follow_nucleotide_order() -> None:
    region = _region()
    region.add_allele(10, "T", "A")

    display = build_allele_display(_statistics_of(region))

    assert [ref for ref, _ in display.matrix_rows()] == ["A", "C", "G", "T"]
    assert display.matrix_rows()[3][1] == [1, 0, 0, 0]


def test_the_statistic_refuses_a_bare_value() -> None:
    with pytest.raises(TypeError, match="use fold_region"):
        AlleleStatistics().add_value(1)


def test_merging_two_statistics_of_one_chromosome_needs_adjacency() -> None:
    # Two DESERIALIZED statistics carry no extents, so their regions
    # refuse to merge -- the same guard the coverage statistic has.
    left = AlleleStatistics()
    left.fold_region(RegionAlleles.frozen("chr1", 1, 1, {"other": 1}))
    right = AlleleStatistics()
    right.fold_region(RegionAlleles.frozen("chr1", 1, 1, {"other": 1}))

    with pytest.raises(ValueError, match="adjacent-and-in-order"):
        left.merge(right)
