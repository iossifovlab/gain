# pylint: disable=C0114,C0116,W0212,W0621
import json

import pytest
from gain.genomic_resources.statistics.alleles import (
    AlleleStatistics,
    RegionAlleles,
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
