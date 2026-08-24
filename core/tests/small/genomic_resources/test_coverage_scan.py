# pylint: disable=C0114,C0116,W0212,W0621
import pathlib

import pytest
from gain.genomic_resources.implementations.genomic_scores_impl import (
    GenomicScoreImplementation,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.statistics.coverage import (
    CoverageStatistics,
    RegionCoverage,
)
from gain.genomic_resources.testing.builders import (
    a_bigwig_score,
    a_fragment_score,
    a_position_score,
    an_allele_score,
)

from tests.small.genomic_resources.test_histogram_scan_bulk import (
    _hist_conf,
)


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


def test_per_record_scan_accumulates_coverage(
    tmp_path: pathlib.Path,
) -> None:
    resource = _multivalued_tabix(tmp_path)
    confs: dict = {"score": _hist_conf()}
    coverage = RegionCoverage("chr1", 1, 100)

    GenomicScoreImplementation._do_histogram(
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

    GenomicScoreImplementation._do_histogram(
        resource, confs, "chr1", 1, 100, coverage=per_record)
    GenomicScoreImplementation._do_histogram_bulk(
        resource, confs, "chr1", 1, 100, coverage=bulk)

    assert bulk.covered == per_record.covered == COVERED
    assert bulk.segment_count == per_record.segment_count == SEGMENTS


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
        GenomicScoreImplementation, "_SCAN_BATCH_SIZE", batch_size)
    coverage = RegionCoverage("chr1", 1, 100)

    GenomicScoreImplementation._do_histogram_bulk(
        resource, confs, "chr1", 1, 100, coverage=coverage)

    assert coverage.covered == COVERED
    assert coverage.segment_count == SEGMENTS


def test_region_task_carries_coverage_beside_the_histograms(
    tmp_path: pathlib.Path,
) -> None:
    resource = _multivalued_tabix(tmp_path)
    confs: dict = {"score": _hist_conf()}

    result = GenomicScoreImplementation._do_histogram_task(
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

    result = GenomicScoreImplementation._do_histogram_task(
        resource, confs, "chr1", 1, 100)

    assert result.coverage is None


def test_noregion_build_writes_the_coverage_file(
    tmp_path: pathlib.Path,
) -> None:
    resource = _multivalued_tabix(tmp_path)

    GenomicScoreImplementation._do_noregion_histograms(resource)

    content = resource.get_file_content("statistics/coverage.json")
    stats = CoverageStatistics.deserialize(content)
    assert stats.covered_by_chromosome() == {"chr1": COVERED}
    assert stats.covered_global() == COVERED


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
        GenomicScoreImplementation._do_histogram_task(
            resource, confs, "chr1", start,
            min(start + region_size - 1, 60))
        for start in range(1, 61, region_size)
    ]
    stats = GenomicScoreImplementation._merge_coverage(resource, *results)

    assert stats is not None
    assert stats.covered_by_chromosome() == {"chr1": COVERED}
    merged = stats._regions["chr1"]
    assert merged.segment_count == SEGMENTS


def test_statistics_hash_is_untouched_by_the_coverage_build(
    tmp_path: pathlib.Path,
) -> None:
    from gain.genomic_resources.implementations.genomic_scores_impl import (
        build_score_implementation_from_resource,
    )
    resource = _multivalued_tabix(tmp_path)
    before = build_score_implementation_from_resource(
        resource).calc_statistics_hash()

    GenomicScoreImplementation._do_noregion_histograms(resource)

    after = build_score_implementation_from_resource(
        resource).calc_statistics_hash()
    assert after == before


def test_fragment_rows_overlapping_and_nested_count_once(
    tmp_path: pathlib.Path,
) -> None:
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

    GenomicScoreImplementation._do_histogram(
        resource, confs, "chr1", 1, 200, coverage=per_record)
    GenomicScoreImplementation._do_histogram_bulk(
        resource, confs, "chr1", 1, 200, coverage=bulk)

    assert per_record.covered == 122
    assert bulk.covered == 122


def test_bigwig_scan_coverage_matches_tabix(
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

    GenomicScoreImplementation._do_histogram_bulk(
        resource, confs, "chr1", 1, 100, coverage=coverage)

    assert coverage.covered == 20
    assert coverage.segment_count == 3
