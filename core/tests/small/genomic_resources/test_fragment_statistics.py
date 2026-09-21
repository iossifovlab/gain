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
from gain.genomic_resources.statistics.exact_lengths import (
    NO_LENGTHS,
    ExactLengths,
    LengthArrayTally,
)
from gain.genomic_resources.statistics.fragments import (
    FRAGMENT_LENGTHS_IMAGE_FILE,
    FRAGMENT_STATISTICS_FILE,
    FragmentStatistics,
    RegionFragments,
    merge_region_fragments,
    save_and_plot_fragments,
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


def test_an_unknown_record_round_trips_as_unknown_not_as_empty(
    tmp_path: pathlib.Path,
) -> None:
    # The shape ``serialize`` actually emits for a chromosome whose
    # lengths are unknown: the key is OMITTED, not written as an empty
    # record.  Read back as an empty record it would claim the fragments
    # were measured and had no lengths, which is a different -- and
    # false -- statement from "this file cannot say".  Written through the
    # statistic's own serializer rather than by hand, so the reader and
    # the writer are pinned against each other and not against a fixture
    # that could drift from either.
    resource = _fragments(tmp_path)
    scan.do_noregion_histograms(resource)
    known = _stored(resource)
    unknown = FragmentStatistics()
    for chrom, count in known.fragments_by_chromosome().items():
        unknown.fold_region(RegionFragments.frozen(chrom, count, None))

    restored = FragmentStatistics.deserialize(unknown.serialize())

    assert "fragment_lengths" not in unknown.serialize()
    assert restored.fragments_by_chromosome() == {"chr1": 4, "chr2": 1}
    assert restored.fragments_global() == 5
    assert restored.fragment_lengths_by_chromosome() == {}
    assert restored.fragment_lengths_global() is None


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


#: The ``_fragments`` fixture's chr1 rows span 91, 11, 11 and 31 base
#: pairs; chr2's one row spans 4.  Stored form (string keys: JSON).
STORED_CHR1_LENGTHS = {
    "lengths": {"11": 2, "31": 1, "91": 1},
    "count": 4, "sum": 144, "min": 11, "max": 91,
}
STORED_CHR2_LENGTHS = {
    "lengths": {"4": 1}, "count": 1, "sum": 4, "min": 4, "max": 4,
}
STORED_GLOBAL_LENGTHS = {
    "lengths": {"4": 1, "11": 2, "31": 1, "91": 1},
    "count": 5, "sum": 148, "min": 4, "max": 91,
}
CHR1_LENGTHS = ExactLengths({11: 2, 31: 1, 91: 1}, 4, 144, 11, 91)
CHR2_LENGTHS = ExactLengths({4: 1}, 1, 4, 4, 4)
GLOBAL_LENGTHS = ExactLengths({4: 1, 11: 2, 31: 1, 91: 1}, 5, 148, 4, 91)


def test_the_file_stores_each_chromosomes_fragment_lengths_exactly(
    tmp_path: pathlib.Path,
) -> None:
    """Format version 2: the ladder leaves the file, the exact record
    enters it, and the record's count is the fragment count -- the same
    rows, counted twice by two different routes."""
    resource = _fragments(tmp_path)

    scan.do_noregion_histograms(resource)

    content = resource.get_file_content(FRAGMENT_STATISTICS_FILE)
    data = json.loads(content)
    assert data["format_version"] == 2
    chromosomes = data["chromosomes"]
    assert chromosomes["chr1"]["fragment_lengths"] == STORED_CHR1_LENGTHS
    assert chromosomes["chr2"]["fragment_lengths"] == STORED_CHR2_LENGTHS
    for entry in chromosomes.values():
        assert entry["fragment_lengths"]["count"] == entry["fragment_count"]
    assert data["global"]["fragment_lengths"] == STORED_GLOBAL_LENGTHS
    assert data["global"]["fragment_count"] == 5
    assert "fragment_length_histogram" not in content


def test_fragment_lengths_bin_the_rows_own_span_and_merge_exactly(
    tmp_path: pathlib.Path,
) -> None:
    # Each row is counted at its OWN span -- 91, 11, 11 and 31 on chr1,
    # 4 on chr2 -- and the global record is the fold of the two
    # chromosomes' records: nothing is re-scanned.
    resource = _fragments(tmp_path)

    scan.do_noregion_histograms(resource)

    stats = _stored(resource)
    assert stats.fragment_lengths_by_chromosome() == {
        "chr1": CHR1_LENGTHS,
        "chr2": CHR2_LENGTHS,
    }
    assert stats.fragment_lengths_global() == GLOBAL_LENGTHS
    assert GLOBAL_LENGTHS.total == stats.fragments_global() == 5


def test_reading_the_file_builds_no_array_tally(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The array tally is the SCAN's: one clamp-sized counter block per
    # region, so a batch of billions of rows folds at a cost bounded by
    # the clamp.  A region restored from the file never accumulates, so
    # it has no use for one -- and at 64 KB a piece, a draft assembly
    # with 100k scaffolds would need gigabytes just to render its info
    # page (gain#1565).  Pinned as a count of constructions rather than
    # a memory bound, which would be a flaky pin.
    resource = _fragments(tmp_path)
    scan.do_noregion_histograms(resource)
    content = resource.get_file_content(FRAGMENT_STATISTICS_FILE)
    constructions: list[LengthArrayTally] = []
    build = LengthArrayTally.__init__

    def spy(self: LengthArrayTally) -> None:
        constructions.append(self)
        build(self)
    monkeypatch.setattr(LengthArrayTally, "__init__", spy)

    stats = FragmentStatistics.deserialize(content)
    global_lengths = stats.fragment_lengths_global()

    assert not constructions, \
        f"the reader built {len(constructions)} array tallies"
    assert global_lengths == GLOBAL_LENGTHS
    assert stats.fragment_lengths_by_chromosome() == {
        "chr1": CHR1_LENGTHS,
        "chr2": CHR2_LENGTHS,
    }


def test_a_frozen_region_refuses_to_fold_onto_a_held_one() -> None:
    # A region restored from a file holds its record as read and has no
    # merge of its own: it carries no extents, so the adjacency rule
    # refuses it before any merge arithmetic runs.  That refusal is
    # what lets a frozen region hold a record rather than a mergeable
    # tally (gain#1565), so it is pinned here rather than assumed.
    stats = FragmentStatistics()
    stats.fold_region(RegionFragments.frozen("chr1", 1, CHR2_LENGTHS))

    with pytest.raises(ValueError, match="not adjacent-and-in-order"):
        stats.fold_region(RegionFragments.frozen("chr1", 1, CHR2_LENGTHS))

    assert stats.fragments_by_chromosome() == {"chr1": 1}
    assert stats.fragment_lengths_global() == CHR2_LENGTHS


def test_bulk_and_per_record_scans_produce_the_same_fragment_statistics(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Two paths read the same region; a resource is served by whichever
    # it is eligible for, so they must not measure differently.  And
    # the bulk path folds a batch's lengths with ONE array call, never
    # one per row (decision 4 of gain#1541): the cost of tallying a
    # batch is bounded by the clamp, not by the row count.
    batches: list[list[int]] = []
    fold = LengthArrayTally.add_batch

    def spy(self: LengthArrayTally, lengths: np.ndarray) -> None:
        batches.append(sorted(lengths.tolist()))
        fold(self, lengths)
    monkeypatch.setattr(LengthArrayTally, "add_batch", spy)
    resource = _fragments(tmp_path)
    confs: dict = {"s": _hist_conf()}
    per_record = RegionFragments("chr1", 1, 200)
    bulk = RegionFragments("chr1", 1, 200)

    scan.do_histogram(
        resource, confs, "chr1", 1, 200, fragments=per_record)
    assert not batches, f"the per-record path made an array call: {batches}"
    scan.do_histogram_bulk(
        resource, confs, "chr1", 1, 200, fragments=bulk)

    assert batches == [[11, 11, 31, 91]]
    assert bulk.fragments == per_record.fragments
    assert bulk.fragment_lengths() == per_record.fragment_lengths()
    # Pinned absolutely, not just against each other: the four chr1 rows
    # span 91, 11, 11 and 31 base pairs.
    assert per_record.fragments == 4
    assert per_record.fragment_lengths() == CHR1_LENGTHS


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
    # this section but outside the Fragment lengths subheading.  The one
    # image is referenced twice -- once as the thumbnail, once full-size
    # inside the modal the thumbnail opens (gain#1544).
    section = section_after(page, "<h2>Fragments</h2>")
    assert section.count(FRAGMENT_LENGTHS_IMAGE_FILE) == 2
    assert section.count('class="figure-thumbnail"') == 1
    assert section.count('data-modal-trigger="modal-fragment-lengths"') == 1
    assert section.count('id="modal-fragment-lengths"') == 1
    assert resource.file_exists(FRAGMENT_LENGTHS_IMAGE_FILE)


def test_the_info_page_tables_the_fragment_lengths_exactly(
    tmp_path: pathlib.Path,
) -> None:
    """The four numbers the exact record exists for (gain#1544), read
    straight off the fixture's rows: lengths 4, 11, 11, 31 and 91 have
    a mean of 29.6 and a median of 11."""
    resource = _fragments(tmp_path)
    scan.do_noregion_histograms(resource)

    page = _info_page(resource)

    assert table_after(page, "<h3>Fragment lengths</h3>").text == [
        ["", "fragments", "min", "max", "mean", "median"],
        ["fragments", "5", "4", "91", "29.6", "11"],
    ]


def test_a_median_past_the_clamp_renders_as_a_floor(
    tmp_path: pathlib.Path,
) -> None:
    """Fragments of 3, 9000 and 9500 bp: the middle one is longer than
    the map's clamp, so the median is only known to be at least the
    clamp and the page says so -- while min, max and the mean, taken on
    the unclamped lengths, stay exact."""
    resource = (
        a_fragment_score()
        .with_score("s", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  s
            chr1   1          3        0.1
            chr1   10         9009     0.2
            chr1   20         9519     0.3
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )
    scan.do_noregion_histograms(resource)

    page = _info_page(resource)

    assert table_after(page, "<h3>Fragment lengths</h3>").text == [
        ["", "fragments", "min", "max", "mean", "median"],
        ["fragments", "3", "3", "9500", "6167.67", "≥8192"],
    ]


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


def _version_1_file() -> str:
    """A fragments.json as gain wrote it before gain#1544 for the
    ``_fragments`` fixture: the fragment count beside the log2 ladder
    (chr1's 11, 11, 31 and 91 in bins 3, 3, 4 and 6; chr2's 4 in bin 2),
    no exact record."""
    chr1_ladder = [0, 0, 0, 2, 1, 0, 1] + [0] * 25
    chr2_ladder = [0, 0, 1, 0, 0, 0, 0] + [0] * 25
    global_ladder = [0, 0, 1, 2, 1, 0, 1] + [0] * 25
    return json.dumps({
        "format_version": 1,
        "chromosomes": {
            "chr1": {"fragment_count": 4,
                     "fragment_length_histogram": chr1_ladder},
            "chr2": {"fragment_count": 1,
                     "fragment_length_histogram": chr2_ladder},
        },
        "global": {"fragment_count": 5,
                   "fragment_length_histogram": global_ladder},
    })


def test_a_version_1_file_keeps_its_counts_and_reads_lengths_unknown(
    tmp_path: pathlib.Path,
) -> None:
    """Decision 5 of gain#1541: a fragment score built before the exact
    record keeps its Fragments table -- the counts are still true -- and
    the subsection says the LENGTHS are not computed, which is what a
    rebuild acts on.  The stored ladder is not read at all: one reader,
    no compatibility branch.  Not "no fragments": the table right above
    says there are five."""
    resource = _fragments(tmp_path)
    scan.do_noregion_histograms(resource)
    with resource.proto.open_raw_file(
            resource, FRAGMENT_STATISTICS_FILE, mode="wt") as outfile:
        outfile.write(_version_1_file())

    stats = _stored(resource)
    page = _info_page(resource)

    assert stats.fragments_by_chromosome() == {"chr1": 4, "chr2": 1}
    assert stats.fragment_lengths_by_chromosome() == {}
    assert stats.fragment_lengths_global() is None
    table = table_after(page, "<h2>Fragments</h2>")
    assert [cell.text for cell in table.head[0]] == ["Chromosome", "Fragments"]
    assert [cell.text for cell in table.head[1]] == ["all chromosomes", "5"]
    section = section_after(page, "<h3>Fragment lengths</h3>")
    assert "<p>fragment lengths not computed</p>" in section
    assert "no fragments" not in section
    assert FRAGMENT_LENGTHS_IMAGE_FILE not in section
    assert "modal-fragment-lengths" not in page


def test_a_version_1_file_rebuilds_no_image(tmp_path: pathlib.Path) -> None:
    """Lengths unknown is not lengths empty, but it draws the same
    nothing: there is no record to derive a ladder from."""
    resource = _fragments(tmp_path)

    save_and_plot_fragments(
        resource, FragmentStatistics.deserialize(_version_1_file()))

    assert not resource.file_exists(FRAGMENT_LENGTHS_IMAGE_FILE)


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
    # 91, 11, 11, 31 and 2 base pairs, each once.
    assert stats.fragment_lengths_global() == ExactLengths(
        {2: 1, 11: 2, 31: 1, 91: 1}, 5, 146, 2, 91)
    # And the FILE is the same bytes whatever the region size: the
    # single-region scan is the reference the chunked ones must match.
    single = merge_region_fragments(resource.resource_id, [whole.fragments])
    assert single is not None
    assert stats.serialize() == single.serialize()


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
            "format_version": 2,
            "chromosomes": {"chr1": {
                "fragment_count": 0,
                "fragment_lengths": NO_LENGTHS.stored(),
            }},
        })))

    page = build_score_implementation_from_resource(resource).get_info()

    section = section_after(page, "<h3>Fragment lengths</h3>")
    assert "<p>no fragments</p>" in section
    assert "not computed" not in section
    assert FRAGMENT_LENGTHS_IMAGE_FILE not in page


def test_the_batch_binning_refuses_a_non_positive_length() -> None:
    region = RegionFragments("chr1", 1, 10)
    with pytest.raises(ValueError, match="positive"):
        region.add_fragment_batch(np.array([5, 0], dtype=np.int64))
