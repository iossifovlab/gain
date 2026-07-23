# pylint: disable=C0114,C0116,W0212,W0621
import pathlib

import numpy as np
import pytest
from gain.genomic_resources.histogram import (
    NumberHistogram,
    NumberHistogramConfig,
)
from gain.genomic_resources.implementations.genomic_scores_impl import (
    GenomicScoreImplementation,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing.builders import (
    a_bigwig_score,
    a_np_score,
    a_position_score,
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


def _multiscore_tabix(tmp_path: pathlib.Path) -> GenomicResource:
    return (
        a_position_score()
        .with_score("s1", "float")
        .with_score("s2", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  s1    s2
            chr1   1          3        0.1   0.9
            chr1   4          4        0.5   .
            chr1   5          10       0.95  0.2
            chr1   11         11       .     0.0
            chr1   12         20       1.0   0.5
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )


def test_bulk_histogram_matches_per_record_tabix_multiscore(
    tmp_path: pathlib.Path,
) -> None:
    resource = _multiscore_tabix(tmp_path)
    confs: dict = {"s1": _hist_conf(), "s2": _hist_conf()}

    ref = GenomicScoreImplementation._do_histogram(
        resource, confs, "chr1", 1, 20)
    bulk = GenomicScoreImplementation._do_histogram_bulk(
        resource, confs, "chr1", 1, 20)

    _assert_hists_equal(bulk, ref)
    # sanity: the NA rows were actually skipped, not binned as 0.
    assert ref["s2"].bars.sum() < ref["s1"].bars.sum() + 10


def test_bulk_histogram_matches_per_record_zero_based(
    tmp_path: pathlib.Path,
) -> None:
    resource = (
        a_position_score()
        .with_score("s", "float")
        .with_zero_based()
        .with_data(
            """
            chrom  pos_begin  pos_end  s
            chr1   0          2        0.1
            chr1   2          6        0.95
            chr1   6          7        0.5
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )
    confs: dict = {"s": _hist_conf()}
    ref = GenomicScoreImplementation._do_histogram(
        resource, confs, "chr1", 1, 7)
    bulk = GenomicScoreImplementation._do_histogram_bulk(
        resource, confs, "chr1", 1, 7)
    _assert_hists_equal(bulk, ref)


def test_bulk_histogram_matches_per_record_configured_na(
    tmp_path: pathlib.Path,
) -> None:
    # A numeric NA sentinel ("-1") parses to a real number; the bulk path
    # must still treat it as NA (isin on the raw value), not bin it.
    resource = (
        a_position_score()
        .with_score("s", "float")
        .with_na_values("-1")
        .with_data(
            """
            chrom  pos_begin  pos_end  s
            chr1   1          2        0.2
            chr1   3          4        -1
            chr1   5          6        0.8
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )
    confs: dict = {"s": _hist_conf()}
    ref = GenomicScoreImplementation._do_histogram(
        resource, confs, "chr1", 1, 6)
    bulk = GenomicScoreImplementation._do_histogram_bulk(
        resource, confs, "chr1", 1, 6)
    _assert_hists_equal(bulk, ref)
    assert bulk["s"].bars.sum() == ref["s"].bars.sum()


def test_bulk_histogram_matches_per_record_subregion_clip(
    tmp_path: pathlib.Path,
) -> None:
    resource = _multiscore_tabix(tmp_path)
    confs: dict = {"s1": _hist_conf(), "s2": _hist_conf()}
    # A sub-region that clips the first and last spanning records.
    ref = GenomicScoreImplementation._do_histogram(
        resource, confs, "chr1", 3, 15)
    bulk = GenomicScoreImplementation._do_histogram_bulk(
        resource, confs, "chr1", 3, 15)
    _assert_hists_equal(bulk, ref)


def test_bulk_histogram_matches_per_record_bigwig(
    tmp_path: pathlib.Path,
) -> None:
    resource = (
        a_bigwig_score()
        .with_score("bw", "float")
        .with_data(
            """
            chr1  0  2  0.0
            chr1  2  4  2.0
            chr1  4  6  4.0
            """)
        .with_chrom_lens({"chr1": 100})
        .build_resource(tmp_path)
    )
    confs: dict = {"bw": NumberHistogramConfig.from_dict({
        "type": "number", "view_range": {"min": 0, "max": 4},
        "number_of_bins": 4, "x_log_scale": False, "y_log_scale": False})}
    ref = GenomicScoreImplementation._do_histogram(
        resource, confs, "chr1", 1, 6)
    bulk = GenomicScoreImplementation._do_histogram_bulk(
        resource, confs, "chr1", 1, 6)
    _assert_hists_equal(bulk, ref)


def test_bulk_histogram_overlap_guard_across_batch_boundary(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Two overlapping records; force them into separate batches so the guard
    # must fire on the carried right edge, not just within one batch.
    resource = (
        a_position_score()
        .with_score("s", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  s
            chr1   1          5        0.2
            chr1   3          7        0.8
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )
    confs: dict = {"s": _hist_conf()}
    monkeypatch.setattr(
        GenomicScoreImplementation, "_SCAN_BATCH_SIZE", 1)

    # The per-record path rejects this fixture; the bulk path must too.
    with pytest.raises(ValueError, match="multiple values"):
        GenomicScoreImplementation._do_histogram(
            resource, confs, "chr1", 1, 7)
    with pytest.raises(ValueError, match="multiple values"):
        GenomicScoreImplementation._do_histogram_bulk(
            resource, confs, "chr1", 1, 7)


def test_dispatch_uses_bulk_for_float_tabix(tmp_path: pathlib.Path) -> None:
    resource = _multiscore_tabix(tmp_path)
    confs: dict = {"s1": _hist_conf(), "s2": _hist_conf()}

    assert GenomicScoreImplementation._can_bulk_histogram(resource, confs)
    via_task = GenomicScoreImplementation._do_histogram_task(
        resource, confs, "chr1", 1, 20)
    ref = GenomicScoreImplementation._do_histogram(
        resource, confs, "chr1", 1, 20)
    _assert_hists_equal(via_task, ref)


def test_dispatch_falls_back_for_whole_table_scan(
    tmp_path: pathlib.Path,
) -> None:
    # chrom=None (whole-table) is not bulk-eligible: the overlap guard runs
    # per contig, so it keeps the per-record path.
    resource = _multiscore_tabix(tmp_path)
    confs: dict = {"s1": _hist_conf(), "s2": _hist_conf()}
    via_task = GenomicScoreImplementation._do_histogram_task(
        resource, confs, None, None, None)
    ref = GenomicScoreImplementation._do_histogram(
        resource, confs, None, None, None)
    _assert_hists_equal(via_task, ref)


def test_int_score_is_not_bulk_eligible(tmp_path: pathlib.Path) -> None:
    resource = (
        a_position_score()
        .with_score("s", "int")
        .with_data(
            """
            chrom  pos_begin  pos_end  s
            chr1   1          2        3
            chr1   3          4        7
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )
    confs: dict = {"s": NumberHistogramConfig.from_dict({
        "type": "number", "view_range": {"min": 0, "max": 10},
        "number_of_bins": 10, "x_log_scale": False, "y_log_scale": False})}
    assert not GenomicScoreImplementation._can_bulk_histogram(resource, confs)


def test_bulk_matches_per_record_float_underscore_token(
    tmp_path: pathlib.Path,
) -> None:
    # Python float() (the per-record parser) accepts PEP-515 underscores;
    # the bulk coercion must agree, not silently drop "1_000" as NaN.
    resource = (
        a_position_score()
        .with_score("s", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  s
            chr1   1          2        0.5
            chr1   3          4        1_000
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )
    confs: dict = {"s": NumberHistogramConfig.from_dict({
        "type": "number", "view_range": {"min": 0, "max": 2000},
        "number_of_bins": 10, "x_log_scale": False, "y_log_scale": False})}
    ref = GenomicScoreImplementation._do_histogram(
        resource, confs, "chr1", 1, 4)
    bulk = GenomicScoreImplementation._do_histogram_bulk(
        resource, confs, "chr1", 1, 4)
    _assert_hists_equal(bulk, ref)
    assert bulk["s"].max_value == 1000.0


def test_np_score_is_not_bulk_eligible(tmp_path: pathlib.Path) -> None:
    # An np_score/allele_score reads with per-allele (weight-1,
    # multiple-alleles-per-position) semantics; the position-score bulk path
    # would impose span weights and its overlap guard would raise on a
    # multi-allele site.  The dispatch must keep such scores on the per-record
    # path -- and never raise.
    resource = (
        a_np_score().with_score("score", "float").with_tabix()
        .build_resource(tmp_path)
    )
    confs: dict = {"score": _hist_conf()}
    assert not GenomicScoreImplementation._can_bulk_histogram(resource, confs)

    via_task = GenomicScoreImplementation._do_histogram_task(
        resource, confs, "1", 1, 20)
    ref = GenomicScoreImplementation._do_histogram(
        resource, confs, "1", 1, 20)
    _assert_hists_equal(via_task, ref)
