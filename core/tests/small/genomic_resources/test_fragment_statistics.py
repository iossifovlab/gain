# pylint: disable=C0114,C0116,W0212,W0621
import pathlib

import pytest
from gain.genomic_resources.histogram import NumberHistogramConfig
from gain.genomic_resources.implementations.genomic_scores_impl import (
    GenomicScoreImplementation,
    build_score_implementation_from_resource,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.resource_types import FRAGMENT_SCORE_TYPES
from gain.genomic_resources.statistics.coverage import (
    LENGTH_HISTOGRAM_BIN_COUNT,
    CoverageStatistics,
    RegionCoverage,
)
from gain.genomic_resources.testing.builders import (
    a_fragment_score,
    a_position_score,
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


def _fragments(
    tmp_path: pathlib.Path, resource_type: str = "fragment_score",
) -> GenomicResource:
    """Overlapping, nested and duplicate fragments over two contigs.

    Spans 91, 11, 11 and 31 on chr1 -- the two 20-30 rows are an exact
    duplicate pair, and 90-120 overlaps 10-100 -- and 4 on chr2.
    """
    return (
        a_fragment_score()
        .with_resource_type(resource_type)
        .with_score("s", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  s
            chr1   10         100      0.1
            chr1   20         30       0.2
            chr1   20         30       0.3
            chr1   90         120      0.4
            chr2   1          4        0.5
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )


def _stored(resource: GenomicResource) -> CoverageStatistics:
    return CoverageStatistics.deserialize(
        resource.get_file_content("statistics/coverage.json"))


def _bins(counts: dict[int, int]) -> list[int]:
    histogram = [0] * LENGTH_HISTOGRAM_BIN_COUNT
    for index, count in counts.items():
        histogram[index] = count
    return histogram


@pytest.mark.legacy_vocabulary
@pytest.mark.parametrize("resource_type", FRAGMENT_SCORE_TYPES)
def test_every_row_counts_as_one_fragment_per_chromosome_and_global(
    tmp_path: pathlib.Path,
    resource_type: str,
) -> None:
    # A fragment is a table row AS STORED: overlapping, nested and
    # duplicate rows each count, in both spellings of the type --
    # ``cnv_collection`` is the deprecated one, hence the marker.
    resource = _fragments(tmp_path, resource_type)

    GenomicScoreImplementation._do_noregion_histograms(resource)

    stats = _stored(resource)
    assert stats.fragments_by_chromosome() == {"chr1": 4, "chr2": 1}
    assert stats.fragments_global() == 5


def test_fragment_lengths_bin_the_rows_own_span_and_merge_exactly(
    tmp_path: pathlib.Path,
) -> None:
    # Each row is binned by its OWN span on the fixed log2 ladder: 91 in
    # [64, 128), the two 11s in [8, 16), 31 in [16, 32) and 4 in [4, 8).
    # Because both chromosomes are binned on the same ladder, the global
    # histogram is their bin-wise merge and nothing is re-scanned.
    resource = _fragments(tmp_path)

    GenomicScoreImplementation._do_noregion_histograms(resource)

    stats = _stored(resource)
    assert stats.fragment_lengths_by_chromosome() == {
        "chr1": _bins({3: 2, 4: 1, 6: 1}),
        "chr2": _bins({2: 1}),
    }
    assert stats.fragment_lengths_global() == _bins({2: 1, 3: 2, 4: 1, 6: 1})
    assert sum(stats.fragment_lengths_global() or []) \
        == stats.fragments_global() == 5


def test_bulk_and_per_record_scans_produce_the_same_fragment_statistics(
    tmp_path: pathlib.Path,
) -> None:
    # Two paths read the same region; a resource is served by whichever
    # it is eligible for, so they must not measure differently.
    resource = _fragments(tmp_path)
    confs: dict = {"s": _hist_conf()}
    per_record = RegionCoverage(
        "chr1", 1, 200, rows_are_disjoint=False, track_fragments=True)
    bulk = RegionCoverage(
        "chr1", 1, 200, rows_are_disjoint=False, track_fragments=True)

    GenomicScoreImplementation._do_histogram(
        resource, confs, "chr1", 1, 200, coverage=per_record)
    GenomicScoreImplementation._do_histogram_bulk(
        resource, confs, "chr1", 1, 200, coverage=bulk)

    assert bulk.fragment_summary() == per_record.fragment_summary()
    assert per_record.fragment_summary() == (4, _bins({3: 2, 4: 1, 6: 1}))
    assert bulk.covered == per_record.covered


def test_a_position_score_publishes_no_fragment_statistics(
    tmp_path: pathlib.Path,
) -> None:
    # Fragment counts are a fragment score's statistic.  A position
    # score's coverage file carries no fragment keys at all, and reads
    # back as fragments-unknown rather than as zero.
    resource = (
        a_position_score()
        .with_score("s", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  s
            chr1   5          9        0.1
            chr1   15         20       0.2
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )

    GenomicScoreImplementation._do_noregion_histograms(resource)

    stats = _stored(resource)
    assert stats.fragments_by_chromosome() == {}
    assert stats.fragments_global() is None


def _fragments_section(page: str) -> str:
    """The Fragments section's own markup, heading to next heading."""
    heading = "<h2>Fragments</h2>"
    assert heading in page, "no Fragments section on this page"
    rest = page.split(heading, 1)[1]
    return rest.split("<h2>", 1)[0]


def _info_page(resource: GenomicResource) -> str:
    return build_score_implementation_from_resource(resource).get_info()


def test_the_info_page_renders_a_fragments_section(
    tmp_path: pathlib.Path,
) -> None:
    # Per-chromosome counts, a global row and ONE global length image --
    # no per-chromosome images.
    resource = _fragments(tmp_path)
    GenomicScoreImplementation._do_noregion_histograms(resource)

    section = _fragments_section(_info_page(resource))

    assert ">chr1<" in section
    assert ">4<" in section
    assert ">chr2<" in section
    assert ">5<" in section
    assert section.count(
        "statistics/coverage_fragment_lengths.png") == 1
    assert resource.file_exists("statistics/coverage_fragment_lengths.png")


def test_the_fragments_section_is_absent_on_a_position_score(
    tmp_path: pathlib.Path,
) -> None:
    # Not "Fragments: not computed" forever -- the section does not
    # exist at all on a kind that has no fragments.
    resource = (
        a_position_score()
        .with_score("s", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  s
            chr1   5          9        0.1
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )
    GenomicScoreImplementation._do_noregion_histograms(resource)

    assert "Fragments" not in _info_page(resource)


def test_a_fragment_resource_built_before_this_says_not_computed(
    tmp_path: pathlib.Path,
) -> None:
    # The statistics roll out lazily, so a fragment resource can carry
    # histograms and no coverage file at all.  The section is still
    # there; it just has nothing to show.
    resource = _fragments(tmp_path)
    GenomicScoreImplementation._do_noregion_histograms(resource)
    resource.proto.delete_resource_file(
        resource, "statistics/coverage.json")

    section = _fragments_section(_info_page(resource))

    assert "not computed" in section
    assert "coverage_fragment_lengths.png" not in section
