# pylint: disable=C0114,C0116,W0212,W0621
import pathlib

import numpy as np
import pytest
from gain.genomic_resources.histogram import (
    CategoricalHistogramConfig,
    NullHistogramConfig,
    NumberHistogram,
    NumberHistogramConfig,
)
from gain.genomic_resources.implementations.genomic_scores_impl import (
    GenomicScoreImplementation as G,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.resource_types import FRAGMENT_SCORE_TYPES
from gain.genomic_resources.testing.builders import (
    a_fragment_score,
    a_np_score,
    a_vcf_info_score,
    an_allele_score,
)


def _hist_conf() -> NumberHistogramConfig:
    return NumberHistogramConfig.from_dict({
        "type": "number",
        "view_range": {"min": 0, "max": 1},
        "number_of_bins": 10,
        "x_log_scale": False,
        "y_log_scale": False,
    })


def _assert_hists_equal(
    bulk: dict[str, NumberHistogram],
    ref: dict[str, NumberHistogram],
) -> None:
    assert set(bulk) == set(ref)
    for score_id in ref:
        got, want = bulk[score_id], ref[score_id]
        assert np.array_equal(got.bars, want.bars), \
            (score_id, got.bars, want.bars)
        assert got.out_of_range_bins == want.out_of_range_bins, score_id
        assert np.array_equal(
            [got.min_value], [want.min_value], equal_nan=True), score_id
        assert np.array_equal(
            [got.max_value], [want.max_value], equal_nan=True), score_id


def _allele_tabix(tmp_path: pathlib.Path) -> GenomicResource:
    """An allele score with THREE records sharing position 10."""
    return (
        an_allele_score()
        .with_score("s", "float")
        .with_data(
            """
            chrom  pos_begin  reference  alternative  s
            chr1   10         A          G            0.1
            chr1   10         A          C            0.2
            chr1   10         A          T            0.9
            chr1   14         C          T            0.3
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )


def test_allele_score_is_bulk_scan_eligible(tmp_path: pathlib.Path) -> None:
    assert G._bulk_scan_eligible(_allele_tabix(tmp_path), ["s"])


def test_bulk_histogram_matches_per_record_allele_shared_position(
    tmp_path: pathlib.Path,
) -> None:
    # Three records sit at position 10 (distinct ref/alt) -- which is what an
    # allele score IS, and what the position-score overlap guard rejects.
    resource = _allele_tabix(tmp_path)
    confs: dict = {"s": _hist_conf()}
    ref = G._do_histogram(resource, confs, "chr1", 1, 20)
    bulk = G._do_histogram_bulk(resource, confs, "chr1", 1, 20)
    _assert_hists_equal(bulk, ref)
    # Every record weighs 1, so the bars hold one count per record.
    assert bulk["s"].bars.sum() == 4


def _assert_min_max_equal(bulk: dict, ref: dict) -> None:
    assert set(bulk) == set(ref)
    for sid in ref:
        got, want = bulk[sid], ref[sid]
        assert np.array_equal([got.min], [want.min], equal_nan=True), \
            (sid, got.min, want.min)
        assert np.array_equal([got.max], [want.max], equal_nan=True), \
            (sid, got.max, want.max)
        assert got.count == want.count, (sid, got.count, want.count)


def test_bulk_min_max_matches_per_record_allele_shared_position(
    tmp_path: pathlib.Path,
) -> None:
    resource = _allele_tabix(tmp_path)
    ref = G._do_min_max(resource, ["s"], "chr1", 1, 20)
    bulk = G._do_min_max_bulk(resource, ["s"], "chr1", 1, 20)
    _assert_min_max_equal(bulk, ref)
    assert (bulk["s"].min, bulk["s"].max) == (0.1, 0.9)


def test_bulk_allele_matches_per_record_when_region_clips_the_edges(
    tmp_path: pathlib.Path,
) -> None:
    # 10..14 clips the shared-position site at its left edge and the last
    # record at its right, exercising the keep mask alongside the weight rule.
    resource = _allele_tabix(tmp_path)
    confs: dict = {"s": _hist_conf()}
    _assert_hists_equal(
        G._do_histogram_bulk(resource, confs, "chr1", 10, 14),
        G._do_histogram(resource, confs, "chr1", 10, 14))
    _assert_min_max_equal(
        G._do_min_max_bulk(resource, ["s"], "chr1", 10, 14),
        G._do_min_max(resource, ["s"], "chr1", 10, 14))


def _allele_multibase_tabix(tmp_path: pathlib.Path) -> GenomicResource:
    """An allele score whose first record spans TEN bases via ``pos_end``."""
    return (
        an_allele_score()
        .with_score("s", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  reference   alternative  s
            chr1   10         19       AGGGGGGGGG  A            0.1
            chr1   10         10       A           C            0.9
            chr1   30         30       C           T            0.5
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )


def test_multi_base_allele_record_weighs_one_not_its_span(
    tmp_path: pathlib.Path,
) -> None:
    # ``AlleleScore.fetch_region_values`` yields ``(pos, pos, values)``, so a
    # record weighs 1 however far its ``pos_end`` reaches.  Span-weighting it
    # in bulk would silently produce a different histogram.
    resource = _allele_multibase_tabix(tmp_path)
    confs: dict = {"s": _hist_conf()}
    ref = G._do_histogram(resource, confs, "chr1", 1, 40)
    bulk = G._do_histogram_bulk(resource, confs, "chr1", 1, 40)
    _assert_hists_equal(bulk, ref)
    # Three records, one count each -- not 10 + 1 + 1 for the spans.
    assert bulk["s"].bars.sum() == 3


def test_multi_base_allele_record_clipped_by_region_weighs_one(
    tmp_path: pathlib.Path,
) -> None:
    # Region 15..25 keeps only the ten-base record, clipped to five bases: the
    # clipped span (5), the full span (10) and the correct weight (1) are three
    # different numbers, so a wrong weight cannot hide.
    resource = _allele_multibase_tabix(tmp_path)
    confs: dict = {"s": _hist_conf()}
    ref = G._do_histogram(resource, confs, "chr1", 15, 25)
    bulk = G._do_histogram_bulk(resource, confs, "chr1", 15, 25)
    _assert_hists_equal(bulk, ref)
    assert bulk["s"].bars.sum() == 1


def _fragment_tabix(
    tmp_path: pathlib.Path, resource_type: str = "cnv_collection",
) -> GenomicResource:
    """A fragment score with overlapping spans of DIFFERING lengths."""
    return (
        a_fragment_score()
        .with_resource_type(resource_type)
        .with_score("s", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  s
            chr1   10         100      0.1
            chr1   20         30       0.9
            chr1   25         200      0.5
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )


@pytest.mark.parametrize("resource_type", FRAGMENT_SCORE_TYPES)
def test_fragment_score_is_bulk_scan_eligible_in_both_spellings(
    tmp_path: pathlib.Path, resource_type: str,
) -> None:
    # ``fragment_score`` and ``cnv_collection`` are both permanent spellings of
    # one kind (gain#471).  A gate that named only one would send the other
    # back to the per-record path silently -- no error, no failing test.
    resource = _fragment_tabix(tmp_path, resource_type)
    assert resource.get_type() == resource_type
    assert G._bulk_scan_eligible(resource, ["s"])


@pytest.mark.parametrize("resource_type", FRAGMENT_SCORE_TYPES)
def test_bulk_histogram_matches_per_record_fragment(
    tmp_path: pathlib.Path, resource_type: str,
) -> None:
    resource = _fragment_tabix(tmp_path, resource_type)
    confs: dict = {"s": _hist_conf()}
    ref = G._do_histogram(resource, confs, "chr1", 1, 300)
    bulk = G._do_histogram_bulk(resource, confs, "chr1", 1, 300)
    _assert_hists_equal(bulk, ref)
    # Three fragments, one count each; their spans (91, 11, 176) stay out.
    assert bulk["s"].bars.sum() == 3


def test_bulk_min_max_matches_per_record_fragment_including_count(
    tmp_path: pathlib.Path,
) -> None:
    # A fragment score's min/max ALSO reports how many records it saw, and
    # that count reaches the serialized statistic.
    resource = _fragment_tabix(tmp_path)
    ref = G._do_min_max(resource, ["s"], "chr1", 1, 300)
    bulk = G._do_min_max_bulk(resource, ["s"], "chr1", 1, 300)
    _assert_min_max_equal(bulk, ref)
    assert (bulk["s"].min, bulk["s"].max) == (0.1, 0.9)
    assert bulk["s"].count == 3


def test_bulk_fragment_matches_per_record_when_region_clips_the_edges(
    tmp_path: pathlib.Path,
) -> None:
    resource = _fragment_tabix(tmp_path)
    confs: dict = {"s": _hist_conf()}
    _assert_hists_equal(
        G._do_histogram_bulk(resource, confs, "chr1", 26, 150),
        G._do_histogram(resource, confs, "chr1", 26, 150))
    _assert_min_max_equal(
        G._do_min_max_bulk(resource, ["s"], "chr1", 26, 150),
        G._do_min_max(resource, ["s"], "chr1", 26, 150))


def test_vcf_backed_allele_score_is_not_bulk_scan_eligible(
    tmp_path: pathlib.Path,
) -> None:
    # An ``allele_score`` is now an eligible KIND, so what keeps a VCF-backed
    # one on the per-record path is its table: a VCF record's payload is not a
    # raw row, so the backend serves no column arrays.
    resource = a_vcf_info_score().build_resource(tmp_path)
    assert resource.get_type() == "allele_score"
    assert not G._bulk_scan_eligible(resource, ["score"])


def test_np_score_is_not_bulk_scan_eligible(tmp_path: pathlib.Path) -> None:
    # Deliberately left out: no production GRR has an np_score, so the bulk
    # path is not exercised against one.
    resource = (
        a_np_score().with_score("score", "float").with_tabix()
        .build_resource(tmp_path)
    )
    assert not G._bulk_scan_eligible(resource, ["score"])


def test_categorical_histogram_keeps_the_per_record_path(
    tmp_path: pathlib.Path,
) -> None:
    resource = _allele_tabix(tmp_path)
    confs: dict = {"s": CategoricalHistogramConfig.default_config()}
    assert not G._can_bulk_histogram(resource, confs)


def test_a_categorical_score_disqualifies_the_whole_resource(
    tmp_path: pathlib.Path,
) -> None:
    # One categorical score is enough: the histogram build is dispatched per
    # resource, not per score, and a null config is merely skipped.
    resource = _allele_tabix(tmp_path)
    confs: dict = {
        "s": _hist_conf(),
        "other": CategoricalHistogramConfig.default_config(),
        "third": NullHistogramConfig("no reason"),
    }
    assert not G._can_bulk_histogram(resource, confs)


def test_dispatch_uses_bulk_for_allele_and_fragment(
    tmp_path: pathlib.Path,
) -> None:
    # The task-level entry points, not just the bulk functions directly.
    for resource in (
        _allele_tabix(tmp_path / "allele"),
        _fragment_tabix(tmp_path / "fragment"),
    ):
        confs: dict = {"s": _hist_conf()}
        assert G._can_bulk_histogram(resource, confs)
        _assert_hists_equal(
            G._do_histogram_task(resource, confs, "chr1", 1, 300),
            G._do_histogram(resource, confs, "chr1", 1, 300))
        _assert_min_max_equal(
            G._do_min_max_task(resource, ["s"], "chr1", 1, 300),
            G._do_min_max(resource, ["s"], "chr1", 1, 300))
