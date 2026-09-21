# pylint: disable=C0114,C0116,W0212,W0621
import json

import numpy as np
import pytest
from gain.genomic_resources.statistics.coverage import (
    CoverageStatistics,
    RegionCoverage,
    SegmentSummary,
)
from gain.genomic_resources.statistics.exact_lengths import (
    ExactLengths,
    LengthArrayTally,
    length_ladder,
)
from gain.genomic_resources.statistics.length_histogram import (
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


def test_serialization_round_trips_segments_and_their_lengths() -> None:
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
    assert lengths["chr1"] == ExactLengths({3: 1, 8: 1}, 2, 11, 3, 8)
    assert lengths["chr2"] == ExactLengths({1: 1}, 1, 1, 1, 1)


def test_reading_the_file_builds_no_array_tally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The array tally is the SCAN's: one clamp-sized counter block per
    # region, so the interior runs of a batch fold at a cost bounded by
    # the clamp.  A region restored from the file never closes a run, so
    # it has no use for one -- and at 64 KB a piece, a draft assembly
    # with 100k scaffolds would need gigabytes just to render its info
    # page (gain#1565).  Pinned as a count of constructions rather than
    # a memory bound, which would be a flaky pin.
    stats = CoverageStatistics()
    chr1 = RegionCoverage("chr1", 1, 100)
    chr1.add_interval(10, 12, (0.5,))
    chr1.add_interval(13, 20, (0.7,))
    chr2 = RegionCoverage("chr2", 1, 100)
    chr2.add_interval(5, 5, (0.1,))
    stats.fold_region(chr1)
    stats.fold_region(chr2)
    content = stats.serialize()
    constructions: list[LengthArrayTally] = []
    build = LengthArrayTally.__init__

    def spy(self: LengthArrayTally) -> None:
        constructions.append(self)
        build(self)
    monkeypatch.setattr(LengthArrayTally, "__init__", spy)

    restored = CoverageStatistics.deserialize(content)
    global_lengths = restored.segment_lengths_global()

    assert not constructions, \
        f"the reader built {len(constructions)} array tallies"
    assert global_lengths == ExactLengths({1: 1, 3: 1, 8: 1}, 3, 12, 1, 8)
    assert restored.segment_lengths_by_chromosome() == {
        "chr1": ExactLengths({3: 1, 8: 1}, 2, 11, 3, 8),
        "chr2": ExactLengths({1: 1}, 1, 1, 1, 1),
    }


def test_the_global_record_is_the_fold_of_the_chromosomes() -> None:
    stats = CoverageStatistics()
    chr1 = RegionCoverage("chr1", 1, 100)
    chr1.add_interval(10, 12, (0.5,))
    chr2 = RegionCoverage("chr2", 1, 100)
    chr2.add_interval(5, 7, (0.1,))
    chr2.add_interval(20, 40, (0.2,))
    stats.fold_region(chr1)
    stats.fold_region(chr2)

    # Lengths 3 on chr1, 3 and 21 on chr2: the map adds where the
    # chromosomes share a length, and the scalars fold exactly.
    assert stats.segment_lengths_global() \
        == ExactLengths({3: 2, 21: 1}, 3, 27, 3, 21)
    assert stats.segments_global() == 3


@pytest.mark.parametrize("feed", ["row-by-row", "batch"])
def test_a_region_publishing_no_segments_refuses_a_span(feed: str) -> None:
    # Since gain#1175 every scanned region publishes segments: the only
    # non-publishing region is one restored from a statistics file that
    # carried none, and that never accumulates.  Feeding one a span is
    # a wiring error, refused rather than unioned into a count nobody
    # can pair with a segmentation.
    region = RegionCoverage.frozen("chr1", 0, None)
    if feed == "row-by-row":
        def feed_span() -> None:
            region.add_interval(10, 20, (0.5,))
    else:
        def feed_span() -> None:
            region.add_interval_batch(
                np.array([10]), np.array([20]), [np.array([0.5])])

    with pytest.raises(ValueError, match="publishes no segment statistics"):
        feed_span()

    assert region.covered == 0


def test_a_batch_folds_its_interior_runs_with_one_array_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Decision 4 of gain#1541: the cost of tallying a batch is bounded
    by the clamp, not by the run count.  Of a batch's runs, the first
    may still stitch onto the region's open run and the last stays
    open, so only the ones between -- which close INSIDE the batch, at
    their own length -- can be folded together; they go in as one
    ``add_batch``, never one call per run.  Same record as feeding the
    rows one at a time, which is what makes the shortcut a shortcut."""
    batches: list[list[int]] = []
    fold = LengthArrayTally.add_batch

    def spy(self: LengthArrayTally, lengths: np.ndarray) -> None:
        batches.append(lengths.tolist())
        fold(self, lengths)
    monkeypatch.setattr(LengthArrayTally, "add_batch", spy)
    # Six rows, six different values: six runs of lengths 3, 5, 1, 2,
    # 4, 7 -- the region's open run before the batch is stitched onto
    # by the first (equal value, touching).
    left = np.array([10, 13, 18, 19, 21, 25])
    right = np.array([12, 17, 18, 20, 24, 31])
    values = np.array([0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
    batched = RegionCoverage("chr1", 1, 100)
    batched.add_interval(5, 9, (0.5,))
    row_by_row = RegionCoverage("chr1", 1, 100)
    row_by_row.add_interval(5, 9, (0.5,))
    for begin, end, value in zip(left, right, values, strict=True):
        row_by_row.add_interval(int(begin), int(end), (float(value),))

    batched.add_interval_batch(left, right, [values])

    assert batches == [[5, 1, 2, 4]]
    assert batched.segment_count == row_by_row.segment_count == 6
    assert batched.covered == row_by_row.covered == 27
    assert batched.segment_lengths() == row_by_row.segment_lengths() \
        == ExactLengths({1: 1, 2: 1, 4: 1, 5: 1, 7: 1, 8: 1}, 6, 27, 1, 8)


def test_merging_in_a_region_without_segments_loses_them() -> None:
    # The flag survives a merge only if both sides carry it: a region
    # that publishes no segments has no runs to stitch, so the merged
    # region cannot answer for the whole span either.  Built empty,
    # because a non-publishing region refuses spans.
    left = RegionCoverage("chr1", 1, 10)
    left.add_interval(4, 10, (0.5,))
    right = RegionCoverage("chr1", 11, 20, publishes_segments=False)

    left.merge(right)

    assert left.covered == 7
    assert not left.publishes_segments
    assert left.segment_summary() is None


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
        region.segment_lengths()


def test_a_version_1_file_reads_its_counts_and_not_its_ladder() -> None:
    """The gain#1543 rollout (ADR 0020, decision 5 of gain#1541): a file
    that stored the segment count and the log2 ladder still yields its
    count -- the Coverage table keeps its Segments column -- while the
    lengths read as unknown.  The ladder is not read at all: it can
    publish no exact sum, min or max, and one reader is the rule.  Not
    a crash, and not "no segments" either, which is a different claim."""
    version_1 = json.dumps({
        "format_version": 1,
        "chromosomes": {"chr1": {
            "covered_positions": 7,
            "segment_count": 2,
            "segment_length_histogram": [1, 1] + [0] * 30,
        }},
        "global": {"covered_positions": 7},
    })

    restored = CoverageStatistics.deserialize(version_1)

    assert restored.covered_by_chromosome() == {"chr1": 7}
    assert restored.segments_by_chromosome() == {"chr1": 2}
    assert restored.segments_global() == 2
    assert not restored.segment_lengths_by_chromosome()
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


def test_a_frozen_region_refuses_to_fold_onto_a_held_one() -> None:
    # A region restored from a file holds its record as read and has no
    # merge of its own: it carries no extents, so the adjacency rule
    # refuses it before any merge arithmetic runs.  That refusal is
    # what lets a frozen region hold a record rather than a mergeable
    # tally (gain#1565), so it is pinned here rather than assumed.
    stats = CoverageStatistics()
    stats.fold_region(RegionCoverage.frozen(
        "chr1", 3, SegmentSummary(1, ExactLengths({3: 1}, 1, 3, 3, 3))))

    with pytest.raises(ValueError, match="not adjacent-and-in-order"):
        stats.fold_region(RegionCoverage.frozen(
            "chr1", 8, SegmentSummary(1, ExactLengths({8: 1}, 1, 8, 8, 8))))

    assert stats.segment_lengths_global() == ExactLengths({3: 1}, 1, 3, 3, 3)


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
    # which the length record consumes: one segment 4-25, length 22.
    assert first._run == (4, 25, (0.5,))
    assert first.segment_lengths() == ExactLengths({22: 1}, 1, 22, 22, 22)


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
    assert seq_acc.segment_lengths() == pairwise[0].segment_lengths() \
        == ExactLengths({3: 1, 5: 1, 9: 1, 17: 1, 20: 1}, 5, 54, 3, 20)


def test_the_chart_ladder_derived_from_the_record_is_the_stored_one(
) -> None:
    """The one place the ladder survives gain#1543 is the chart, drawn
    from the record rather than from a stored histogram.  For that to
    change no pixel, the derived ladder must equal what the scan used
    to bin: here four segments in three bins, two of them past the clamp
    -- folded to one key in the map, yet the same bin as before, because
    the clamp is the bin the plot already sums everything above into."""
    cov = RegionCoverage("chr1", 1, 20_000)
    cov.add_interval(1, 3, (0.1,))
    cov.add_interval(4, 103, (0.4,))
    cov.add_interval(110, 9109, (0.2,))
    cov.add_interval(9120, 18619, (0.3,))
    stored_ladder = [0] * 32
    for length in (3, 100, 9000, 9500):
        stored_ladder[length_histogram_bin_index(length)] += 1

    lengths = cov.segment_lengths()

    assert lengths is not None
    assert lengths.lengths == {3: 1, 100: 1, 8192: 2}
    assert sum(map(bool, stored_ladder)) == 3
    assert length_ladder(lengths) == stored_ladder


def test_a_single_segment_is_recorded_at_its_exact_length() -> None:
    cov = RegionCoverage("chr1", 1, 100)

    cov.add_interval(10, 12, (0.5,))

    assert cov.segment_lengths() == ExactLengths({3: 1}, 1, 3, 3, 3)


def test_the_record_counts_every_segment_at_its_length() -> None:
    cov = RegionCoverage("chr1", 1, 100)

    cov.add_interval(10, 12, (0.5,))
    cov.add_interval(13, 20, (0.7,))
    cov.add_interval(30, 30, (0.9,))

    # Lengths 3, 8 and 1; the record's total is the segment count.
    lengths = cov.segment_lengths()
    assert cov.segment_count == 3
    assert lengths == ExactLengths({1: 1, 3: 1, 8: 1}, 3, 12, 1, 8)


def test_a_stitched_merge_records_the_combined_length_once() -> None:
    left = RegionCoverage("chr1", 1, 10)
    left.add_interval(4, 10, (0.5,))
    right = RegionCoverage("chr1", 11, 20)
    right.add_interval(11, 12, (0.5,))
    right.add_interval(13, 15, (0.7,))
    right.add_interval(16, 20, (0.9,))

    left.merge(right)

    # Segments: stitched 4-12 (length 9), 13-15 (3), 16-20 (5).
    assert left.segment_count == 3
    assert left.segment_lengths() \
        == ExactLengths({3: 1, 5: 1, 9: 1}, 3, 17, 3, 9)


def test_an_unstitched_merge_records_both_boundary_runs() -> None:
    left = RegionCoverage("chr1", 1, 10)
    left.add_interval(4, 9, (0.5,))
    right = RegionCoverage("chr1", 11, 20)
    right.add_interval(11, 13, (0.5,))
    right.add_interval(14, 20, (0.7,))

    left.merge(right)

    # Segments: 4-9 (length 6), 11-13 (3), 14-20 (7); the gap at the
    # boundary keeps 4-9 and 11-13 apart despite equal values.
    assert left.segment_count == 3
    assert left.segment_lengths() \
        == ExactLengths({3: 1, 6: 1, 7: 1}, 3, 16, 3, 7)


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
    # 5..100 is 96bp; taking the other end would have recorded 5..15's 11.
    assert left.segment_lengths() == ExactLengths({96: 1}, 1, 96, 96, 96)


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
    # The stitched run is 5..100, and 30..40 closes behind it.
    assert left.segment_lengths() \
        == ExactLengths({11: 1, 96: 1}, 2, 107, 11, 96)
