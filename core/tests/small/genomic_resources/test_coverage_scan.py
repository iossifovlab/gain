# pylint: disable=C0114,C0116,W0212,W0621
import json
import pathlib

import numpy as np
import pytest
from gain.genomic_resources.histogram import NumberHistogramConfig
from gain.genomic_resources.implementations.genomic_scores_impl import (
    PositionScoreImplementation,
    build_score_implementation_from_resource,
    scan,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.statistics.coverage import (
    COVERAGE_SEGMENT_LENGTHS_IMAGE_FILE,
    COVERAGE_STATISTICS_FILE,
    CoverageStatistics,
    RegionCoverage,
    accumulate_coverage,
    merge_region_coverage,
    save_and_plot_coverage,
)
from gain.genomic_resources.statistics.fragments import (
    FRAGMENT_STATISTICS_FILE,
)
from gain.genomic_resources.statistics.length_histogram import (
    LENGTH_HISTOGRAM_BIN_COUNT,
)
from gain.genomic_resources.testing.builders import (
    a_bigwig_score,
    a_fragment_score,
    a_position_score,
    an_allele_score,
)

_HIST_DICT: dict = {
    "type": "number",
    "view_range": {"min": 0, "max": 1},
    "number_of_bins": 10,
}


def _hist_conf() -> NumberHistogramConfig:
    return NumberHistogramConfig.from_dict({
        **_HIST_DICT,
        "x_log_scale": False,
        "y_log_scale": False,
    })


def _multivalued_tabix(tmp_path: pathlib.Path) -> GenomicResource:
    # Four segments: 5-14 (0.1 across two adjacent rows), 15-20 (0.2,
    # touching but different-valued), 21-22 (NA -- a value of its own,
    # covered but its own segment), 30-33 (0.2 again, after a gap).
    return (
        a_position_score()
        .with_score("score", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  score
            chr1   5          9        0.1
            chr1   10         14       0.1
            chr1   15         20       0.2
            chr1   21         22       .
            chr1   30         33       0.2
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )


COVERED = 22
SEGMENTS = 4
# Segment lengths 10, 6, 2 and 4 on the log2 bins: [2,4) holds one,
# [4,8) holds two, [8,16) holds one.
SEGMENT_LENGTHS = {1: 1, 2: 2, 3: 1}


def _expected_histogram() -> list[int]:
    histogram = [0] * 32
    for index, count in SEGMENT_LENGTHS.items():
        histogram[index] = count
    return histogram


def test_per_record_scan_accumulates_coverage(
    tmp_path: pathlib.Path,
) -> None:
    resource = _multivalued_tabix(tmp_path)
    confs: dict = {"score": _hist_conf()}
    coverage = RegionCoverage("chr1", 1, 100)

    scan.do_histogram(
        resource, confs, "chr1", 1, 100, coverage=coverage)

    assert coverage.covered == COVERED
    assert coverage.segment_count == SEGMENTS


def test_bulk_scan_coverage_matches_per_record(
    tmp_path: pathlib.Path,
) -> None:
    resource = _multivalued_tabix(tmp_path)
    confs: dict = {"score": _hist_conf()}
    per_record = RegionCoverage("chr1", 1, 100)
    bulk = RegionCoverage("chr1", 1, 100)

    scan.do_histogram(
        resource, confs, "chr1", 1, 100, coverage=per_record)
    scan.do_histogram_bulk(
        resource, confs, "chr1", 1, 100, coverage=bulk)

    assert bulk.covered == per_record.covered == COVERED
    assert bulk.segment_count == per_record.segment_count == SEGMENTS
    assert bulk.segment_length_histogram() \
        == per_record.segment_length_histogram() \
        == _expected_histogram()


@pytest.mark.parametrize("batch_size", [1, 2, 3, 100])
def test_bulk_coverage_is_batch_size_invariant(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    batch_size: int,
) -> None:
    # A run spanning a batch boundary must keep extending across it;
    # every fixture fits one default batch, so force tiny ones.
    resource = _multivalued_tabix(tmp_path)
    confs: dict = {"score": _hist_conf()}
    monkeypatch.setattr(
        scan, "_SCAN_BATCH_SIZE", batch_size)
    coverage = RegionCoverage("chr1", 1, 100)

    scan.do_histogram_bulk(
        resource, confs, "chr1", 1, 100, coverage=coverage)

    assert coverage.covered == COVERED
    assert coverage.segment_count == SEGMENTS
    assert coverage.segment_length_histogram() == _expected_histogram()


def test_region_task_carries_coverage_beside_the_histograms(
    tmp_path: pathlib.Path,
) -> None:
    resource = _multivalued_tabix(tmp_path)
    confs: dict = {"score": _hist_conf()}

    result = scan.do_histogram_task(
        resource, confs, "chr1", 1, 100)

    assert set(result.histograms) == {"score"}
    assert result.coverage is not None
    assert result.coverage.covered == COVERED


def test_region_task_collects_no_coverage_for_allele_scores(
    tmp_path: pathlib.Path,
) -> None:
    resource = (
        an_allele_score()
        .with_score("s", "float")
        .with_data(
            """
            chrom  pos_begin  reference  alternative  s
            chr1   10         A          G            0.1
            chr1   14         C          T            0.3
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )
    confs: dict = {"s": _hist_conf()}

    result = scan.do_histogram_task(
        resource, confs, "chr1", 1, 100)

    assert result.coverage is None


def test_noregion_build_writes_the_coverage_file(
    tmp_path: pathlib.Path,
) -> None:
    resource = _multivalued_tabix(tmp_path)

    scan.do_noregion_histograms(resource)

    content = resource.get_file_content("statistics/coverage.json")
    stats = CoverageStatistics.deserialize(content)
    assert stats.covered_by_chromosome() == {"chr1": COVERED}
    assert stats.covered_global() == COVERED
    assert stats.segments_by_chromosome() == {"chr1": SEGMENTS}
    assert stats.segments_global() == SEGMENTS
    assert stats.segment_lengths_global() == _expected_histogram()


def test_build_writes_the_segment_length_image(
    tmp_path: pathlib.Path,
) -> None:
    resource = _multivalued_tabix(tmp_path)

    scan.do_noregion_histograms(resource)

    assert resource.file_exists(
        "statistics/coverage_segment_lengths.png")


def test_info_page_shows_segment_statistics(
    tmp_path: pathlib.Path,
) -> None:
    resource = _multivalued_tabix(tmp_path)
    scan.do_noregion_histograms(resource)

    page = PositionScoreImplementation(resource).get_info()

    assert "Segments" in page
    assert f">{SEGMENTS}<" in page
    assert "statistics/coverage_segment_lengths.png" in page


def test_info_page_with_an_old_coverage_file_omits_segments(
    tmp_path: pathlib.Path,
) -> None:
    # A coverage.json written before segment histograms existed: the
    # coverage table still renders, the segment column and image do not.
    resource = _multivalued_tabix(tmp_path)
    scan.do_noregion_histograms(resource)
    with resource.proto.open_raw_file(
            resource, "statistics/coverage.json", mode="wt") as outfile:
        outfile.write(json.dumps({
            "format_version": 1,
            "chromosomes": {"chr1": {"covered_positions": COVERED}},
            "global": {"covered_positions": COVERED},
        }))

    page = PositionScoreImplementation(resource).get_info()

    assert f">{COVERED}<" in page
    assert "Segments" not in page
    assert "coverage_segment_lengths.png" not in page


def test_noregion_build_keys_coverage_by_chromosome(
    tmp_path: pathlib.Path,
) -> None:
    resource = (
        a_position_score()
        .with_score("score", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  score
            chr1   5          9        0.1
            chr2   1          10       0.2
            chr2   20         21       0.3
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )

    scan.do_noregion_histograms(resource)

    stats = CoverageStatistics.deserialize(
        resource.get_file_content("statistics/coverage.json"))
    assert stats.covered_by_chromosome() == {"chr1": 5, "chr2": 12}
    assert stats.covered_global() == 17


@pytest.mark.parametrize("region_size", [1, 2, 3, 7, 100])
def test_coverage_is_chunk_invariant(
    tmp_path: pathlib.Path,
    region_size: int,
) -> None:
    # Region sizes 1-3 split single-valued stretches across three or
    # more chunks, which is the shape that goes wrong without the
    # one-run bookkeeping (a two-chunk split stitches either way).
    resource = _multivalued_tabix(tmp_path)
    confs: dict = {"score": _hist_conf()}

    results = [
        scan.do_histogram_task(
            resource, confs, "chr1", start,
            min(start + region_size - 1, 60))
        for start in range(1, 61, region_size)
    ]
    stats = merge_region_coverage(
        resource.resource_id, (result.coverage for result in results))

    assert stats is not None
    assert stats.covered_by_chromosome() == {"chr1": COVERED}
    merged = stats._regions["chr1"]
    assert merged.segment_count == SEGMENTS
    assert merged.segment_length_histogram() == _expected_histogram()


def test_statistics_hash_is_untouched_by_the_coverage_build(
    tmp_path: pathlib.Path,
) -> None:
    resource = _multivalued_tabix(tmp_path)
    before = build_score_implementation_from_resource(
        resource).calc_statistics_hash()

    scan.do_noregion_histograms(resource)

    after = build_score_implementation_from_resource(
        resource).calc_statistics_hash()
    assert after == before


def test_info_page_renders_the_coverage_section(
    tmp_path: pathlib.Path,
) -> None:
    resource = _multivalued_tabix(tmp_path)
    scan.do_noregion_histograms(resource)

    page = PositionScoreImplementation(resource).get_info()

    assert "Coverage" in page
    assert "chr1" in page
    assert f">{COVERED}<" in page


def test_info_page_without_the_statistics_file_says_not_computed(
    tmp_path: pathlib.Path,
) -> None:
    # A resource built before this statistic existed: histograms are
    # there, statistics/coverage.json is not.
    resource = _multivalued_tabix(tmp_path)
    scan.do_noregion_histograms(resource)
    resource.proto.delete_resource_file(
        resource, "statistics/coverage.json")

    page = PositionScoreImplementation(resource).get_info()

    assert "Coverage" in page
    assert "not computed" in page


def test_the_union_counts_overlapping_and_nested_rows_once(
    tmp_path: pathlib.Path,
) -> None:
    # Pins RegionCoverage's union algebra DIRECTLY, by handing it a
    # coverage object the scan would not build for this kind: since
    # gain#1127 no coverage-scanned kind has overlapping rows, and a
    # fragment fixture is the only way to feed the union any.  The
    # running-maximum union is still the class's documented contract,
    # and a frozen or merged region can still carry such counts.
    resource = (
        a_fragment_score()
        .with_score("frequency", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  frequency
            chr1   10         100      0.1
            chr1   20         30       0.2
            chr1   90         120      0.3
            chr1   150        160      0.1
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )
    confs: dict = {"frequency": _hist_conf()}
    per_record = RegionCoverage("chr1", 1, 200)
    bulk = RegionCoverage("chr1", 1, 200)

    scan.do_histogram(
        resource, confs, "chr1", 1, 200, coverage=per_record)
    scan.do_histogram_bulk(
        resource, confs, "chr1", 1, 200, coverage=bulk)

    assert per_record.covered == 122
    assert bulk.covered == 122


def test_a_fragment_build_is_not_coverage_scanned_at_all(
    tmp_path: pathlib.Path,
) -> None:
    # A fragment score's rows deliberately overlap, so the union of
    # their spans counts nothing a reader wants: not fragments (that is
    # the fragment statistic, which the kind has) and not completeness
    # in a way that compares across resources.  So the kind is not
    # coverage-scanned, and writes no coverage file at all -- absence,
    # not a file whose numbers mean nothing (gain#1127).
    #
    # ADR 0020 already recorded the adjacent half: no segments either,
    # because merging overlapping rows is not wanted (gain#926).
    resource = (
        a_fragment_score()
        .with_score("frequency", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  frequency
            chr1   10         100      0.1
            chr1   20         30       0.2
            chr1   90         120      0.3
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )

    scan.do_noregion_histograms(resource)

    assert not resource.file_exists(COVERAGE_STATISTICS_FILE)
    assert not resource.file_exists(COVERAGE_SEGMENT_LENGTHS_IMAGE_FILE)
    # The statistic the kind DOES publish is untouched by that -- the
    # point of splitting the two apart before dropping this one.
    assert resource.file_exists(FRAGMENT_STATISTICS_FILE)


def test_a_record_beginning_past_the_region_end_contributes_zero() -> None:
    # The gain#636 edge: a misbehaving backend can answer a region query
    # with a record wholly past it, which a naive clip would turn into a
    # negative length.  Feed the bulk accumulator such a batch directly.
    coverage = RegionCoverage("chr1", 1, 25)
    arrays = (
        np.array([30]), np.array([33]),
        {"score": np.array([0.5])},
    )

    accumulate_coverage(arrays, coverage, ("chr1", 1, 25))

    assert coverage.covered == 0
    assert coverage.segment_count == 0


def _null_histogram_column_tabix(tmp_path: pathlib.Path) -> GenomicResource:
    # aux is histogram: null, so the statistics scan never fetches it:
    # its change at 10 must not break the run, while the scanned score's
    # change at 15 must (the segment tuple is the scanned columns,
    # ADR 0020 / gain#848).
    return (
        a_position_score()
        .with_score("score", "float")
        .with_score("aux", "float")
        .with_histogram(_HIST_DICT, score_id="score")
        .with_histogram({"type": "null"}, score_id="aux")
        .with_data(
            """
            chrom  pos_begin  pos_end  score  aux
            chr1   5          9        0.1    1.0
            chr1   10         14       0.1    2.0
            chr1   15         20       0.2    2.0
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )


NULL_COL_COVERED = 16
NULL_COL_SEGMENTS = 2


@pytest.mark.parametrize(("start", "end"), [(None, None), (1, 100)])
def test_null_histogram_column_never_breaks_a_segment(
    tmp_path: pathlib.Path,
    start: int | None,
    end: int | None,
) -> None:
    # The confs come from the resource yaml through the build's own
    # unpacking, not a hand-built dict; the unbounded and bounded calls
    # cover the task's per-record and bulk dispatch arms.
    resource = _null_histogram_column_tabix(tmp_path)
    _, confs = scan.unpack_score_defs(resource)

    result = scan.do_histogram_task(
        resource, confs, "chr1", start, end)

    assert result.coverage is not None
    assert result.coverage.covered == NULL_COL_COVERED
    assert result.coverage.segment_count == NULL_COL_SEGMENTS


def test_null_histogram_column_scan_path_parity(
    tmp_path: pathlib.Path,
) -> None:
    resource = _null_histogram_column_tabix(tmp_path)
    _, confs = scan.unpack_score_defs(resource)
    per_record = RegionCoverage("chr1", 1, 100)
    bulk = RegionCoverage("chr1", 1, 100)

    scan.do_histogram(
        resource, confs, "chr1", 1, 100, coverage=per_record)
    scan.do_histogram_bulk(
        resource, confs, "chr1", 1, 100, coverage=bulk)

    assert bulk.covered == per_record.covered == NULL_COL_COVERED
    assert bulk.segment_count == per_record.segment_count == NULL_COL_SEGMENTS


def test_bigwig_scan_coverage(
    tmp_path: pathlib.Path,
) -> None:
    # bedGraph rows are 0-based half-open: the four rows are the tabix
    # fixture's 5-9/10-14 (0.1), 15-20 (0.2) and 30-33 (0.2) in 1-based.
    resource = (
        a_bigwig_score()
        .with_score("score", "float")
        .with_data(
            """
            chr1  4   9   0.1
            chr1  9   14  0.1
            chr1  14  20  0.2
            chr1  29  33  0.2
            """)
        .with_chrom_lens({"chr1": 100})
        .build_resource(tmp_path)
    )
    confs: dict = {"score": _hist_conf()}
    coverage = RegionCoverage("chr1", 1, 100)

    scan.do_histogram_bulk(
        resource, confs, "chr1", 1, 100, coverage=coverage)

    assert coverage.covered == 20
    assert coverage.segment_count == 3


@pytest.mark.filterwarnings("error::UserWarning")
def test_an_all_zero_segment_histogram_writes_no_image(
    tmp_path: pathlib.Path,
) -> None:
    # A group that is known and empty, not unknown: the region was
    # scanned and turned up no segments at all.  An empty chart under a
    # "Segment lengths" heading states nothing, and the counts axis is
    # logarithmic, which no all-zero dataset can be drawn on.  The
    # statistics file is still written -- only the image is skipped.
    resource = _multivalued_tabix(tmp_path)
    statistics = CoverageStatistics.deserialize(json.dumps({
        "format_version": 1,
        "chromosomes": {"chr1": {
            "covered_positions": 0,
            "segment_count": 0,
            "segment_length_histogram": [0] * LENGTH_HISTOGRAM_BIN_COUNT,
        }},
    }))

    save_and_plot_coverage(resource, statistics)

    assert not resource.file_exists(COVERAGE_SEGMENT_LENGTHS_IMAGE_FILE)
    assert resource.file_exists(COVERAGE_STATISTICS_FILE)


def _all_zero_segment_statistics() -> CoverageStatistics:
    """A scanned chromosome that turned up no segments at all."""
    return CoverageStatistics.deserialize(json.dumps({
        "format_version": 1,
        "chromosomes": {"chr1": {
            "covered_positions": 0,
            "segment_count": 0,
            "segment_length_histogram": [0] * LENGTH_HISTOGRAM_BIN_COUNT,
        }},
    }))


def test_info_page_says_a_resource_genuinely_has_no_segments(
    tmp_path: pathlib.Path,
) -> None:
    # Known-and-empty is not unknown, and it is not data either: the
    # page states it rather than linking an image that, with nothing
    # positive to draw, is no longer written.
    resource = _multivalued_tabix(tmp_path)
    save_and_plot_coverage(resource, _all_zero_segment_statistics())

    page = PositionScoreImplementation(resource).get_info()

    assert "no segments" in page
    assert COVERAGE_SEGMENT_LENGTHS_IMAGE_FILE not in page
