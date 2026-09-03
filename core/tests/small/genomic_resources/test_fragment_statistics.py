# pylint: disable=C0114,C0116,W0212,W0621
import json
import pathlib

import numpy as np
import pytest
from gain.genomic_resources.histogram import NumberHistogramConfig
from gain.genomic_resources.implementations.genomic_scores_impl import (
    build_score_implementation_from_resource,
    scan,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.resource_types import FRAGMENT_SCORE_TYPES
from gain.genomic_resources.statistics.fragments import (
    FRAGMENT_LENGTHS_IMAGE_FILE,
    FRAGMENT_STATISTICS_FILE,
    FragmentStatistics,
    RegionFragments,
    merge_region_fragments,
    save_and_plot_fragments,
)
from gain.genomic_resources.statistics.length_histogram import (
    LENGTH_HISTOGRAM_BIN_COUNT,
)
from gain.genomic_resources.testing.builders import (
    a_fragment_score,
    a_position_score,
)

from tests.small.genomic_resources.info_page_html import (
    section_after,
    table_after,
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


def _stored(resource: GenomicResource) -> FragmentStatistics:
    return FragmentStatistics.deserialize(
        resource.get_file_content(FRAGMENT_STATISTICS_FILE))


def _bins(counts: dict[int, int]) -> list[int]:
    histogram = [0] * LENGTH_HISTOGRAM_BIN_COUNT
    for index, count in counts.items():
        histogram[index] = count
    return histogram


def test_fragment_statistics_are_stored_in_their_own_file(
    tmp_path: pathlib.Path,
) -> None:
    # The fragment tally is its OWN statistic, not a group riding inside
    # the coverage one (gain#1127).  It has to be, because the kind is
    # about to stop being coverage-scanned altogether: while the two
    # shared a carrier, dropping the union dropped the tally with it.
    resource = _fragments(tmp_path)

    scan.do_noregion_histograms(resource)

    stats = FragmentStatistics.deserialize(
        resource.get_file_content(FRAGMENT_STATISTICS_FILE))
    assert stats.fragments_by_chromosome() == {"chr1": 4, "chr2": 1}
    assert stats.fragments_global() == 5


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

    scan.do_noregion_histograms(resource)

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

    scan.do_noregion_histograms(resource)

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
    per_record = RegionFragments("chr1", 1, 200)
    bulk = RegionFragments("chr1", 1, 200)

    scan.do_histogram(
        resource, confs, "chr1", 1, 200, fragments=per_record)
    scan.do_histogram_bulk(
        resource, confs, "chr1", 1, 200, fragments=bulk)

    assert bulk.fragments == per_record.fragments
    assert bulk.length_histogram() == per_record.length_histogram()
    # Pinned absolutely, not just against each other: the four chr1 rows
    # span 91, 11, 11 and 31 base pairs on the fixed log2 ladder.
    assert per_record.fragments == 4
    assert per_record.length_histogram() == _bins({3: 2, 4: 1, 6: 1})


def test_the_per_record_fragment_scan_normalizes_no_values(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A fragment score publishes no segments (ADR 0020, amended by
    # gain#926) and, since gain#1127, no covered positions either -- so
    # the per-record feed has nothing to normalize a row's value tuple
    # FOR.  Only the work is observable, the tally being the same
    # either way, so the normalizer is replaced by one that refuses.
    def _refuse(values: object) -> tuple:
        raise AssertionError(f"a fragment row was normalized: {values}")

    monkeypatch.setattr(scan, "normalize_values", _refuse)
    resource = _fragments(tmp_path)
    fragments = RegionFragments("chr1", 1, 200)

    scan.do_histogram(resource, {"s": _hist_conf()}, "chr1", 1, 200,
                      fragments=fragments)

    assert fragments.fragments == 4


def test_a_position_score_publishes_no_fragment_statistics(
    tmp_path: pathlib.Path,
) -> None:
    # Fragment counts are a fragment score's statistic.  A position
    # score writes no fragment file at all -- absence, not a file of
    # zeroes, is how a kind says the statistic does not apply to it.
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

    scan.do_noregion_histograms(resource)

    assert not resource.file_exists(FRAGMENT_STATISTICS_FILE)


def _info_page(resource: GenomicResource) -> str:
    return build_score_implementation_from_resource(resource).get_info()


def test_the_info_page_renders_a_fragments_section(
    tmp_path: pathlib.Path,
) -> None:
    # Per-chromosome counts, a global row and ONE global length image --
    # no per-chromosome images.
    resource = _fragments(tmp_path)
    scan.do_noregion_histograms(resource)

    page = _info_page(resource)
    table = table_after(page, "<h2>Fragments</h2>")

    # Whole rows, so the counts stay bound to their chromosomes: a test
    # for "the page contains a 4 and a 1 somewhere" would pass on markup
    # that had swapped them.
    assert [[cell.text for cell in row] for row in table.rows] == [
        ["chr1", "4"], ["chr2", "1"]]
    # Pinned as a second <thead> row rather than a <tfoot> (gain#1118),
    # the same shape its two sibling tables now have.
    assert [cell.text for cell in table.head[1]] == ["all chromosomes", "5"]
    assert table.foot == []
    # Counted over the WHOLE Fragments section, subsection included, which
    # is what makes this "one global image and no per-chromosome ones":
    # a per-chromosome image would render beside the table above, inside
    # this section but outside the Fragment lengths subheading.
    assert section_after(page, "<h2>Fragments</h2>").count(
        FRAGMENT_LENGTHS_IMAGE_FILE) == 1
    assert resource.file_exists(FRAGMENT_LENGTHS_IMAGE_FILE)


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
    scan.do_noregion_histograms(resource)

    assert "Fragments" not in _info_page(resource)


def test_a_fragment_resource_with_no_statistics_file_says_not_computed(
    tmp_path: pathlib.Path,
) -> None:
    # The statistics roll out lazily, so a fragment resource can carry
    # histograms and no fragment file at all.  The section is still
    # there; it just has nothing to show.
    resource = _fragments(tmp_path)
    scan.do_noregion_histograms(resource)
    resource.proto.delete_resource_file(
        resource, FRAGMENT_STATISTICS_FILE)

    page = _info_page(resource)

    assert "not computed" in section_after(page, "<h2>Fragments</h2>")
    # Asserted against the whole page rather than the section: with no
    # statistics the Fragment lengths subsection is not rendered at all,
    # so a section-scoped assertion would hold without meaning anything.
    assert FRAGMENT_LENGTHS_IMAGE_FILE not in page


def test_a_file_binned_on_foreign_edges_keeps_counts_and_drops_the_image(
    tmp_path: pathlib.Path,
) -> None:
    # A histogram of another length was binned on edges this code cannot
    # merge with, so the LENGTHS read as unknown -- but the counts are
    # exact whatever the bins were, so the table still renders and only
    # the image goes.  Deleting the whole file (above) does not exercise
    # this: the two are read independently.
    resource = _fragments(tmp_path)
    scan.do_noregion_histograms(resource)
    stored = json.loads(
        resource.get_file_content(FRAGMENT_STATISTICS_FILE))
    for entry in [*stored["chromosomes"].values(), stored["global"]]:
        entry["fragment_length_histogram"] = [0] * 7
    with resource.proto.open_raw_file(
            resource, FRAGMENT_STATISTICS_FILE, mode="wt") as outfile:
        outfile.write(json.dumps(stored))

    stats = _stored(resource)
    assert stats.fragments_by_chromosome() == {"chr1": 4, "chr2": 1}
    assert stats.fragment_lengths_global() is None
    page = _info_page(resource)
    assert "not computed" not in section_after(page, "<h2>Fragments</h2>")
    assert FRAGMENT_LENGTHS_IMAGE_FILE not in page


# Five fragments over chr1, scanned to 200.  Overlapping (10-100 and
# 90-120), nested (20-30 inside 10-100) and duplicated (20-30 twice), so
# a chunked scan has every shape of row to get wrong.
_FRAGMENT_ROWS = ((10, 100), (20, 30), (20, 30), (90, 120), (150, 151))
_FRAGMENT_CONTIG_END = 200


def _fragment_chunk_fixture(tmp_path: pathlib.Path) -> GenomicResource:
    rows = "\n".join(
        f"            chr1   {begin}  {end}  0.{index + 1}"
        for index, (begin, end) in enumerate(_FRAGMENT_ROWS))
    return (
        a_fragment_score()
        .with_score("s", "float")
        .with_data(f"            chrom  pos_begin  pos_end  s\n{rows}\n")
        .with_tabix()
        .build_resource(tmp_path)
    )


@pytest.mark.parametrize("region_size", [1, 2, 3, 7, 100])
def test_fragment_statistics_are_chunk_invariant(
    tmp_path: pathlib.Path,
    region_size: int,
) -> None:
    # A fragment is counted once at its TRUE length however the contig
    # was split -- the property that made the tally worth keeping when
    # the covered-position union it used to ride in was dropped
    # (gain#1127).  Asserts the VALUE histograms too, since a fragment
    # fixture is what exposed gain#816.
    resource = _fragment_chunk_fixture(tmp_path)
    confs: dict = {"s": _hist_conf()}
    starts = list(range(1, _FRAGMENT_CONTIG_END + 1, region_size))
    # Vacuity guard: a chunk-invariance test where nothing is chunked
    # passes trivially.  More than one region, and at least one fragment
    # genuinely straddling a region boundary at THIS size.
    assert len(starts) > 1
    boundaries = [start - 1 for start in starts[1:]]
    assert any(
        begin <= boundary < end
        for begin, end in _FRAGMENT_ROWS
        for boundary in boundaries
    )

    results = [
        scan.do_histogram_task(
            resource, confs, "chr1", start,
            min(start + region_size - 1, _FRAGMENT_CONTIG_END))
        for start in starts
    ]
    whole = scan.do_histogram_task(
        resource, confs, "chr1", 1, _FRAGMENT_CONTIG_END)

    merged = scan.merge_histograms(
        resource, *(result.histograms for result in results))
    assert merged["s"].bars.tolist() == whole.histograms["s"].bars.tolist()
    assert merged["s"].bars.sum() == len(_FRAGMENT_ROWS)

    stats = merge_region_fragments(
        resource.resource_id, (result.fragments for result in results))
    assert stats is not None
    assert stats.fragments_global() == len(_FRAGMENT_ROWS)
    # 91, 11, 11, 31 and 2 base pairs on the fixed log2 bins.
    assert stats.fragment_lengths_global() == _bins({1: 1, 3: 2, 4: 1, 6: 1})


def test_the_info_page_says_a_resource_genuinely_has_no_fragments(
    tmp_path: pathlib.Path,
) -> None:
    # Known-and-empty is not unknown, and it is not data either: the
    # page states it rather than linking an image that, with nothing
    # positive to draw, is no longer written.
    resource = (
        a_fragment_score()
        .with_score("frequency", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  frequency
            chr1   10         100      0.1
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )
    save_and_plot_fragments(
        resource, FragmentStatistics.deserialize(json.dumps({
            "format_version": 1,
            "chromosomes": {"chr1": {
                "fragment_count": 0,
                "fragment_length_histogram": [0] * LENGTH_HISTOGRAM_BIN_COUNT,
            }},
        })))

    page = build_score_implementation_from_resource(resource).get_info()

    assert "no fragments" in page
    assert FRAGMENT_LENGTHS_IMAGE_FILE not in page


def test_the_batch_binning_agrees_with_the_per_length_one() -> None:
    # Two statements of one ladder -- the per-record scan bins lengths
    # one at a time, the bulk scan a whole array -- so they are pinned
    # against each other across the edges, the ends and the clamp.
    lengths = [
        1, 2, 3, 4, 5, 7, 8, 9, 15, 16, 17, 31, 32, 96, 1000,
        2 ** 30, 2 ** 31 - 1, 2 ** 31, 2 ** 31 + 1, 2 ** 40,
    ]
    per_length = RegionFragments("chr1", 1, 10)
    for length in lengths:
        per_length.add_fragment(length)
    batched = RegionFragments("chr1", 1, 10)

    batched.add_fragment_batch(np.array(lengths, dtype=np.int64))

    histogram = batched.length_histogram()
    assert histogram == per_length.length_histogram()
    assert histogram is not None
    assert batched.fragments == per_length.fragments == len(lengths)
    assert sum(histogram) == len(lengths)
    # The clamp: 2**31, 2**31 + 1 and 2**40 all land in the last bin.
    assert histogram[-1] == 3


def test_the_batch_binning_refuses_a_non_positive_length() -> None:
    region = RegionFragments("chr1", 1, 10)
    with pytest.raises(ValueError, match="positive"):
        region.add_fragment_batch(np.array([5, 0], dtype=np.int64))
