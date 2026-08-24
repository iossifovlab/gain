# pylint: disable=C0114,C0116,W0212,W0621
import pytest
from gain.genomic_resources.statistics.coverage import (
    CoverageStatistics,
    RegionCoverage,
)


def test_container_folds_regions_by_chromosome() -> None:
    stats = CoverageStatistics()
    chr1_left = RegionCoverage("chr1", 1, 10)
    chr1_left.add_interval(4, 10, (0.5,))
    chr1_right = RegionCoverage("chr1", 11, 20)
    chr1_right.add_interval(11, 12, (0.5,))
    chr2 = RegionCoverage("chr2", 1, 10)
    chr2.add_interval(2, 4, (0.1,))

    stats.fold_region(chr1_left)
    stats.fold_region(chr1_right)
    stats.fold_region(chr2)

    assert stats.covered_by_chromosome() == {"chr1": 9, "chr2": 3}
    assert stats.covered_global() == 12


def test_container_serialization_round_trips_the_counts() -> None:
    stats = CoverageStatistics()
    region = RegionCoverage("chr1", 1, 10)
    region.add_interval(4, 10, (0.5,))
    stats.fold_region(region)

    restored = CoverageStatistics.deserialize(stats.serialize())

    assert restored.covered_by_chromosome() == {"chr1": 7}
    assert restored.covered_global() == 7


def test_disjoint_intervals_sum_their_lengths() -> None:
    cov = RegionCoverage("chr1", 1, 100)

    cov.add_interval(10, 12, (0.1,))
    cov.add_interval(20, 20, (0.2,))

    assert cov.covered == 4


def test_overlapping_and_nested_intervals_count_once() -> None:
    cov = RegionCoverage("chr1", 1, 100)

    cov.add_interval(10, 20, (0.1,))
    cov.add_interval(12, 15, (0.2,))
    cov.add_interval(18, 25, (0.3,))

    assert cov.covered == 16


def test_adjacent_equal_valued_rows_form_one_segment() -> None:
    cov = RegionCoverage("chr1", 1, 100)

    cov.add_interval(10, 12, (0.5,))
    cov.add_interval(13, 20, (0.5,))

    assert cov.segment_count == 1


def test_adjacent_rows_with_different_values_form_two_segments() -> None:
    cov = RegionCoverage("chr1", 1, 100)

    cov.add_interval(10, 12, (0.5,))
    cov.add_interval(13, 20, (0.7,))

    assert cov.segment_count == 2


def test_a_gap_breaks_a_segment_even_with_equal_values() -> None:
    cov = RegionCoverage("chr1", 1, 100)

    cov.add_interval(10, 12, (0.5,))
    cov.add_interval(14, 20, (0.5,))

    assert cov.segment_count == 2


def test_merge_of_adjacent_regions_adds_covered_positions() -> None:
    left = RegionCoverage("chr1", 1, 10)
    left.add_interval(4, 8, (0.5,))
    right = RegionCoverage("chr1", 11, 20)
    right.add_interval(15, 16, (0.5,))

    left.merge(right)

    assert left.covered == 7


def test_a_segment_split_by_the_boundary_stitches_into_one() -> None:
    left = RegionCoverage("chr1", 1, 10)
    left.add_interval(4, 10, (0.5,))
    right = RegionCoverage("chr1", 11, 20)
    right.add_interval(11, 16, (0.5,))

    left.merge(right)

    assert left.segment_count == 1


def test_touching_runs_with_different_values_do_not_stitch() -> None:
    left = RegionCoverage("chr1", 1, 10)
    left.add_interval(4, 10, (0.5,))
    right = RegionCoverage("chr1", 11, 20)
    right.add_interval(11, 16, (0.7,))

    left.merge(right)

    assert left.segment_count == 2


def test_a_gap_at_the_boundary_keeps_two_segments() -> None:
    left = RegionCoverage("chr1", 1, 10)
    left.add_interval(4, 9, (0.5,))
    right = RegionCoverage("chr1", 11, 20)
    right.add_interval(11, 16, (0.5,))

    left.merge(right)

    assert left.segment_count == 2


def test_a_segment_spanning_three_chunks_is_one_segment() -> None:
    # The first amendment's non-vacuousness case: the middle chunk is
    # covered end to end, so its head and tail are the SAME run, and a
    # merge treating them as two open runs double-counts the segment.
    first = RegionCoverage("chr1", 1, 10)
    first.add_interval(4, 10, (0.5,))
    middle = RegionCoverage("chr1", 11, 20)
    middle.add_interval(11, 20, (0.5,))
    last = RegionCoverage("chr1", 21, 30)
    last.add_interval(21, 25, (0.5,))

    first.merge(middle)
    first.merge(last)

    assert first.segment_count == 1
    assert first.covered == 22
    # The stitched run's SPAN is the load-bearing part: a merge that
    # treats a fully-covered middle chunk's head and tail as two open
    # runs keeps the count right here but loses the run's true begin,
    # which is what the segment-length statistics (gain#775) consume.
    assert first._run == (4, 25, (0.5,))


def test_merge_refuses_regions_out_of_order() -> None:
    left = RegionCoverage("chr1", 1, 10)
    right = RegionCoverage("chr1", 11, 20)

    with pytest.raises(ValueError, match="adjacent"):
        right.merge(left)


def test_merge_refuses_regions_with_a_hole_between_them() -> None:
    left = RegionCoverage("chr1", 1, 10)
    beyond = RegionCoverage("chr1", 12, 20)

    with pytest.raises(ValueError, match="adjacent"):
        left.merge(beyond)


def test_merge_refuses_different_chromosomes() -> None:
    left = RegionCoverage("chr1", 1, 10)
    other = RegionCoverage("chr2", 11, 20)

    with pytest.raises(ValueError, match="chromosome"):
        left.merge(other)


def test_an_empty_region_between_two_runs_prevents_stitching() -> None:
    first = RegionCoverage("chr1", 1, 10)
    first.add_interval(4, 10, (0.5,))
    empty = RegionCoverage("chr1", 11, 20)
    last = RegionCoverage("chr1", 21, 30)
    last.add_interval(21, 25, (0.5,))

    first.merge(empty)
    first.merge(last)

    # The gap 11-20 is real: the run ending at 10 and the one starting
    # at 21 are separate segments even though their values match.
    assert first.segment_count == 2
    assert first.covered == 12


def test_sequential_and_pairwise_folds_agree() -> None:
    # Multi-valued rows across six windows: segments break on value
    # changes inside chunks, at boundaries, and across a fully-covered
    # middle chunk.
    def build() -> list[RegionCoverage]:
        rows = [
            (2, 5, (0.1,)), (6, 10, (0.1,)), (11, 14, (0.2,)),
            (15, 20, (0.2,)), (21, 30, (0.2,)), (33, 35, (0.3,)),
            (36, 40, (0.4,)), (44, 50, (0.4,)), (51, 60, (0.4,)),
        ]
        regions = []
        for start in range(1, 61, 10):
            end = start + 9
            region = RegionCoverage("chr1", start, end)
            for begin, stop, values in rows:
                if stop < start or begin > end:
                    continue
                region.add_interval(
                    max(begin, start), min(stop, end), values)
            regions.append(region)
        return regions

    sequential = build()
    seq_acc = sequential[0]
    for region in sequential[1:]:
        seq_acc.merge(region)

    pairwise = build()
    while len(pairwise) > 1:
        merged = []
        for index in range(0, len(pairwise) - 1, 2):
            pairwise[index].merge(pairwise[index + 1])
            merged.append(pairwise[index])
        if len(pairwise) % 2:
            merged.append(pairwise[-1])
        pairwise = merged

    assert seq_acc.covered == pairwise[0].covered == 54
    assert seq_acc.segment_count == pairwise[0].segment_count == 5


def test_na_values_compare_equal_when_extending_a_segment() -> None:
    cov = RegionCoverage("chr1", 1, 100)

    cov.add_interval(10, 12, (None, 0.5))
    cov.add_interval(13, 20, (None, 0.5))

    assert cov.segment_count == 1
