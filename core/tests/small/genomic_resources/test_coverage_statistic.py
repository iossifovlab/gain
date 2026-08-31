# pylint: disable=C0114,C0116,W0212,W0621
import json

import numpy as np
import pytest
from gain.genomic_resources.statistics.coverage import (
    CoverageStatistics,
    RegionCoverage,
)
from gain.genomic_resources.statistics.length_histogram import (
    LENGTH_HISTOGRAM_BIN_COUNT,
    length_histogram_bin_index,
)


def test_the_log2_bins_cover_one_basepair_to_beyond_a_gigabase() -> None:
    assert length_histogram_bin_index(1) == 0
    assert length_histogram_bin_index(2) == 1
    assert length_histogram_bin_index(3) == 1
    assert length_histogram_bin_index(10 ** 9) == 29
    # Beyond the last edge everything clamps into the open-ended bin.
    assert length_histogram_bin_index(2 ** 31) == 31
    assert length_histogram_bin_index(2 ** 40) == 31
    with pytest.raises(ValueError, match="positive"):
        length_histogram_bin_index(0)


def test_serialization_round_trips_segments_and_histograms() -> None:
    stats = CoverageStatistics()
    chr1 = RegionCoverage("chr1", 1, 100)
    chr1.add_interval(10, 12, (0.5,))
    chr1.add_interval(13, 20, (0.7,))
    chr2 = RegionCoverage("chr2", 1, 100)
    chr2.add_interval(5, 5, (0.1,))
    stats.fold_region(chr1)
    stats.fold_region(chr2)

    restored = CoverageStatistics.deserialize(stats.serialize())

    assert restored.segments_by_chromosome() == {"chr1": 2, "chr2": 1}
    assert restored.segments_global() == 3
    lengths = restored.segment_lengths_by_chromosome()
    assert sum(lengths["chr1"]) == 2
    assert lengths["chr1"][1] == 1
    assert lengths["chr1"][3] == 1
    assert lengths["chr2"][0] == 1


def test_the_global_histogram_is_the_binwise_sum_of_the_chromosomes(
) -> None:
    stats = CoverageStatistics()
    chr1 = RegionCoverage("chr1", 1, 100)
    chr1.add_interval(10, 12, (0.5,))
    chr2 = RegionCoverage("chr2", 1, 100)
    chr2.add_interval(5, 7, (0.1,))
    chr2.add_interval(20, 40, (0.2,))
    stats.fold_region(chr1)
    stats.fold_region(chr2)

    per_chrom = stats.segment_lengths_by_chromosome()
    merged = [
        sum(counts) for counts in
        zip(*per_chrom.values(), strict=True)
    ]

    assert stats.segment_lengths_global() == merged
    assert sum(merged) == stats.segments_global() == 3


def test_untracked_segments_have_no_summary_and_merge_stays_untracked(
) -> None:
    # A region whose rows overlap (fragment rows) still counts coverage
    # but publishes no segments -- not wanted, ADR 0020 as amended by
    # gain#926.
    left = RegionCoverage("chr1", 1, 10, rows_are_disjoint=False)
    left.add_interval(4, 10, (0.5,))
    right = RegionCoverage("chr1", 11, 20, rows_are_disjoint=False)
    right.add_interval(11, 16, (0.5,))

    left.merge(right)

    assert left.covered == 13
    assert left.segment_summary() is None


@pytest.mark.parametrize("feed", ["row-by-row", "batch"])
def test_an_overlapping_kind_opens_no_run_at_all(feed: str) -> None:
    # Segments are not wanted for an overlapping kind (ADR 0020 as
    # amended by gain#926), so the run algebra is not merely unpublished
    # -- it is never executed.  The rows below carry three DIFFERENT
    # value tuples and touch nowhere on the second one, so the ungated
    # code would close runs and open new ones; here nothing opens.
    region = RegionCoverage("chr1", 1, 100, rows_are_disjoint=False)
    spans = [(10, 40), (20, 30), (60, 70)]
    values = [(0.1,), (0.2,), (0.3,)]

    if feed == "row-by-row":
        for (begin, end), value in zip(spans, values, strict=True):
            region.add_interval(begin, end, value)
    else:
        region.add_interval_batch(
            np.array([begin for begin, _ in spans]),
            np.array([end for _, end in spans]),
            [np.array([value for value, in values])])

    # 10-40 unioned with the nested 20-30, plus 60-70.
    assert region.covered == 31 + 11
    assert region._run is None
    assert region._first_run is None
    assert region._closed_segments == 0
    assert region._interior_bins == [0] * LENGTH_HISTOGRAM_BIN_COUNT
    # That emptiness is readable only from the inside: the gate says
    # unknown, and since gain#1043 the count and histogram refuse
    # rather than reporting the zeros above as a scanned result.
    assert region.segment_summary() is None


class _ExplodingColumn:
    """A value column that fails if anything so much as looks at it."""

    def __getitem__(self, index: object) -> object:
        raise AssertionError("the value columns were read")

    @property
    def dtype(self) -> object:
        raise AssertionError("the value columns were read")


def test_an_overlapping_kind_never_reads_the_value_columns() -> None:
    # The batch path reads ``cells`` only for a kind that publishes
    # segments: for one whose rows overlap the per-column equality and
    # the per-run value gather are SKIPPED, not computed and discarded
    # (gain#926).  A column that raises when touched is the only way to
    # see that -- the covered count is the same union either way, so no
    # assertion on an output can tell the two apart.
    region = RegionCoverage("chr1", 1, 100, rows_are_disjoint=False)

    region.add_interval_batch(
        np.array([10, 20, 60]), np.array([40, 30, 70]),
        [_ExplodingColumn()])  # type: ignore[list-item]

    assert region.covered == 42


def test_a_fragment_region_refuses_the_segment_count() -> None:
    # Zero segments of zero length is the answer that must not come
    # back: the region was never segmented, and gain#926 settled that
    # it never will be.  Reported as a number it reads as a scanned,
    # empty result; only segment_summary()'s None says "not wanted".
    cov = RegionCoverage("chr1", 1, 100, rows_are_disjoint=False)
    cov.add_interval(10, 12, (0.5,))
    cov.add_interval(20, 25, (0.5,))
    cov.add_interval(30, 33, (0.5,))

    with pytest.raises(ValueError, match="publishes no segment statistics"):
        _ = cov.segment_count


def test_a_fragment_region_refuses_the_segment_length_histogram() -> None:
    # The other half of the same gate.  Before gain#926 stopped the
    # fragment path building runs, these two disagreed outright --
    # three runs counted, two of them binned -- which is what gain#1043
    # was filed for; the uniform zero that replaced it is quieter and
    # no more true.
    cov = RegionCoverage("chr1", 1, 100, rows_are_disjoint=False)
    cov.add_interval(10, 12, (0.5,))
    cov.add_interval(20, 25, (0.5,))
    cov.add_interval(30, 33, (0.5,))

    with pytest.raises(ValueError, match="publishes no segment statistics"):
        cov.segment_length_histogram()


def test_a_region_read_without_segments_refuses_both_accessors() -> None:
    # The other way a region ends up with no segments: read from a
    # statistics file that predates them.  Same zero, same lie -- and
    # here the rows may well BE disjoint, the file simply carried no
    # segment data.
    region = RegionCoverage.frozen("chr1", 7, None)

    assert region.segment_summary() is None
    with pytest.raises(ValueError, match="publishes no segment statistics"):
        _ = region.segment_count
    with pytest.raises(ValueError, match="publishes no segment statistics"):
        region.segment_length_histogram()


def test_deserializing_a_histogram_of_foreign_length_drops_it(
) -> None:
    # A histogram binned on different edges cannot be merged or
    # rendered against this code's fixed bins; reading it degrades to
    # segments-unknown rather than crashing downstream.
    foreign = json.dumps({
        "format_version": 1,
        "chromosomes": {"chr1": {
            "covered_positions": 7,
            "segment_count": 2,
            "segment_length_histogram": [1, 1],
        }},
        "global": {"covered_positions": 7},
    })

    restored = CoverageStatistics.deserialize(foreign)

    assert restored.covered_by_chromosome() == {"chr1": 7}
    assert restored.segments_by_chromosome() == {}
    assert restored.segment_lengths_global() is None


def test_deserializing_a_file_without_segments_leaves_them_unknown(
) -> None:
    old = json.dumps({
        "format_version": 1,
        "chromosomes": {"chr1": {"covered_positions": 7}},
        "global": {"covered_positions": 7},
    })

    restored = CoverageStatistics.deserialize(old)

    assert restored.covered_by_chromosome() == {"chr1": 7}
    assert restored.segments_by_chromosome() == {}
    assert restored.segments_global() is None
    assert restored.segment_lengths_by_chromosome() == {}
    assert restored.segment_lengths_global() is None


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
    # which the length histogram consumes: one segment 4-25, length 22,
    # in the [16, 32) bin.
    assert first._run == (4, 25, (0.5,))
    hist = first.segment_length_histogram()
    assert sum(hist) == 1
    assert hist[4] == 1


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
    assert seq_acc.segment_length_histogram() \
        == pairwise[0].segment_length_histogram()
    assert sum(seq_acc.segment_length_histogram()) == 5


def test_a_single_segment_length_lands_in_its_log2_bin() -> None:
    cov = RegionCoverage("chr1", 1, 100)

    cov.add_interval(10, 12, (0.5,))

    # Length 3 falls in the [2, 4) bin -- index 1 of the log2 binning.
    hist = cov.segment_length_histogram()
    assert sum(hist) == 1
    assert hist[1] == 1


def test_histogram_totals_match_the_segment_count() -> None:
    cov = RegionCoverage("chr1", 1, 100)

    cov.add_interval(10, 12, (0.5,))
    cov.add_interval(13, 20, (0.7,))
    cov.add_interval(30, 30, (0.9,))

    # Lengths 3, 8 and 1 -> bins [2, 4), [8, 16) and [1, 2).
    hist = cov.segment_length_histogram()
    assert cov.segment_count == 3
    assert sum(hist) == 3
    assert hist[0] == 1
    assert hist[1] == 1
    assert hist[3] == 1


def test_a_stitched_merge_bins_the_combined_length_once() -> None:
    left = RegionCoverage("chr1", 1, 10)
    left.add_interval(4, 10, (0.5,))
    right = RegionCoverage("chr1", 11, 20)
    right.add_interval(11, 12, (0.5,))
    right.add_interval(13, 15, (0.7,))
    right.add_interval(16, 20, (0.9,))

    left.merge(right)

    # Segments: stitched 4-12 (length 9), 13-15 (3), 16-20 (5).
    hist = left.segment_length_histogram()
    assert left.segment_count == 3
    assert sum(hist) == 3
    assert hist[3] == 1
    assert hist[1] == 1
    assert hist[2] == 1


def test_an_unstitched_merge_bins_both_boundary_runs() -> None:
    left = RegionCoverage("chr1", 1, 10)
    left.add_interval(4, 9, (0.5,))
    right = RegionCoverage("chr1", 11, 20)
    right.add_interval(11, 13, (0.5,))
    right.add_interval(14, 20, (0.7,))

    left.merge(right)

    # Segments: 4-9 (length 6), 11-13 (3), 14-20 (7); the gap at the
    # boundary keeps 4-9 and 11-13 apart despite equal values.
    hist = left.segment_length_histogram()
    assert left.segment_count == 3
    assert sum(hist) == 3
    assert hist[2] == 2
    assert hist[1] == 1


def test_na_values_compare_equal_when_extending_a_segment() -> None:
    cov = RegionCoverage("chr1", 1, 100)

    cov.add_interval(10, 12, (None, 0.5))
    cov.add_interval(13, 20, (None, 0.5))

    assert cov.segment_count == 1


def test_a_stitch_never_shortens_the_run_it_merges_into() -> None:
    # A region handed FULL spans has runs reaching past its own extent,
    # so the other region's first run can END before this one's open run
    # does.  The stitch must take the wider end -- the same maximum
    # ``add_interval`` takes row by row -- or the merged segment is
    # reported short.  The old boundary-abutting stitch made this
    # unrepresentable; the touching test that replaced it does not.
    left = RegionCoverage("chr1", 1, 10)
    left.add_interval(5, 100, (0.5,))
    right = RegionCoverage("chr1", 11, 20)
    right.add_interval(11, 15, (0.5,))

    left.merge(right)

    assert left.segment_count == 1
    histogram = left.segment_length_histogram()
    # 5..100 is 96bp; taking the other end would have binned 5..15's 11.
    assert histogram[length_histogram_bin_index(96)] == 1
    assert sum(histogram) == 1


def test_a_stitch_that_closes_a_run_also_keeps_the_wider_end() -> None:
    # The other branch of the same decision: the other region carries a
    # closed run, so the stitched run closes here instead of staying
    # open.  It must close at the wider end too.
    left = RegionCoverage("chr1", 1, 10)
    left.add_interval(5, 100, (0.5,))
    right = RegionCoverage("chr1", 11, 20)
    right.add_interval(11, 15, (0.5,))
    right.add_interval(30, 40, (0.9,))

    left.merge(right)

    assert left.segment_count == 2
    histogram = left.segment_length_histogram()
    # The stitched run is 5..100, and 30..40 closes behind it.
    assert histogram[length_histogram_bin_index(96)] == 1
    assert histogram[length_histogram_bin_index(11)] == 1
    assert sum(histogram) == 2


def test_the_batch_binning_agrees_with_the_per_length_one() -> None:
    # Two statements of one ladder -- the per-record scan bins lengths
    # one at a time, the bulk scan a whole array -- so they are pinned
    # against each other across the edges, the ends and the clamp.
    lengths = [
        1, 2, 3, 4, 5, 7, 8, 9, 15, 16, 17, 31, 32, 96, 1000,
        2 ** 30, 2 ** 31 - 1, 2 ** 31, 2 ** 31 + 1, 2 ** 40,
    ]
    per_length = RegionCoverage("chr1", 1, 10, track_fragments=True)
    for length in lengths:
        per_length.add_fragment(length)
    batched = RegionCoverage("chr1", 1, 10, track_fragments=True)

    batched.add_fragment_batch(np.array(lengths, dtype=np.int64))

    summary = batched.fragment_summary()
    assert summary == per_length.fragment_summary()
    assert summary is not None
    count, histogram = summary
    assert count == len(lengths)
    assert sum(histogram) == len(lengths)
    # The clamp: 2**31, 2**31 + 1 and 2**40 all land in the last bin.
    assert histogram[-1] == 3


def test_the_batch_binning_refuses_a_non_positive_length() -> None:
    region = RegionCoverage("chr1", 1, 10, track_fragments=True)
    with pytest.raises(ValueError, match="positive"):
        region.add_fragment_batch(np.array([5, 0], dtype=np.int64))
