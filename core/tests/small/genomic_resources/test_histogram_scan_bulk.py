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
    a_cnv_collection,
    a_np_score,
    a_position_score,
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


def _allele_tabix(tmp_path: pathlib.Path) -> GenomicResource:
    """Three records share position 10 -- the shape a position score forbids."""
    return (
        an_allele_score()
        .with_score("s", "float")
        .with_data(
            """
            chrom  pos_begin  reference  alternative  s
            chr1   10         A          G            0.1
            chr1   10         A          C            0.2
            chr1   10         A          T            0.3
            chr1   16         C          T            0.5
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )


def _cnv_tabix(tmp_path: pathlib.Path) -> GenomicResource:
    """Overlapping regions of very different lengths, each counting once."""
    return (
        a_cnv_collection()
        .with_score("s", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  s
            chr1   10         100      0.1
            chr1   20         200      0.2
            chr1   30         40       0.3
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


def test_bulk_histogram_matches_per_record_allele_score(
    tmp_path: pathlib.Path,
) -> None:
    """An allele score carries several records at ONE position.

    Distinct ref/alt at the same position is what an allele score *is*, and
    each such record weighs 1 -- neither of which the position score's
    span-weight-plus-overlap-guard reading can express.
    """
    resource = _allele_tabix(tmp_path)
    confs: dict = {"s": _hist_conf()}

    ref = GenomicScoreImplementation._do_histogram(
        resource, confs, "chr1", 1, 20)
    bulk = GenomicScoreImplementation._do_histogram_bulk(
        resource, confs, "chr1", 1, 20)

    _assert_hists_equal(bulk, ref)
    # Every record contributes exactly 1 -- four records, four counts.  A
    # span-weighted read would inflate this.
    assert ref["s"].bars.sum() == 4


def test_bulk_histogram_matches_per_record_cnv_collection(
    tmp_path: pathlib.Path,
) -> None:
    """A CNV counts once however long it is, and CNVs overlap freely.

    Both differ from a position score: the span is not the weight, and
    regions covering the same base are ordinary rather than corrupt.
    """
    resource = _cnv_tabix(tmp_path)
    confs: dict = {"s": _hist_conf()}

    ref = GenomicScoreImplementation._do_histogram(
        resource, confs, "chr1", 1, 300)
    bulk = GenomicScoreImplementation._do_histogram_bulk(
        resource, confs, "chr1", 1, 300)

    _assert_hists_equal(bulk, ref)
    # Three CNVs, three counts.  Span-weighting would make this 91+181+11.
    assert ref["s"].bars.sum() == 3


def test_bulk_histogram_matches_per_record_cnv_subregion_clip(
    tmp_path: pathlib.Path,
) -> None:
    """Clipping decides which CNVs are IN, but never what they weigh.

    A region cutting through two of the three CNVs is where a span-derived
    weight would show up as a clipped span rather than a plain 1, and where
    the ``pos_end >= start`` skip has to drop the third.
    """
    resource = _cnv_tabix(tmp_path)
    confs: dict = {"s": _hist_conf()}

    ref = GenomicScoreImplementation._do_histogram(
        resource, confs, "chr1", 50, 150)
    bulk = GenomicScoreImplementation._do_histogram_bulk(
        resource, confs, "chr1", 50, 150)

    _assert_hists_equal(bulk, ref)
    # 10-100 and 20-200 overlap the region; 30-40 ends before it starts.
    assert ref["s"].bars.sum() == 2


def test_bulk_histogram_matches_per_record_allele_subregion_clip(
    tmp_path: pathlib.Path,
) -> None:
    resource = _allele_tabix(tmp_path)
    confs: dict = {"s": _hist_conf()}

    ref = GenomicScoreImplementation._do_histogram(
        resource, confs, "chr1", 11, 20)
    bulk = GenomicScoreImplementation._do_histogram_bulk(
        resource, confs, "chr1", 11, 20)

    _assert_hists_equal(bulk, ref)
    # Only the record at 16 survives; the three sharing position 10 do not.
    assert ref["s"].bars.sum() == 1


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


def test_dispatch_uses_bulk_for_allele_score(tmp_path: pathlib.Path) -> None:
    """The gate has to let allele scores through, not just the bulk path."""
    resource = _allele_tabix(tmp_path)
    confs: dict = {"s": _hist_conf()}

    assert GenomicScoreImplementation._can_bulk_histogram(resource, confs)
    via_task = GenomicScoreImplementation._do_histogram_task(
        resource, confs, "chr1", 1, 20)
    ref = GenomicScoreImplementation._do_histogram(
        resource, confs, "chr1", 1, 20)
    _assert_hists_equal(via_task, ref)


def test_dispatch_uses_bulk_for_cnv_collection(tmp_path: pathlib.Path) -> None:
    resource = _cnv_tabix(tmp_path)
    confs: dict = {"s": _hist_conf()}

    assert GenomicScoreImplementation._can_bulk_histogram(resource, confs)
    via_task = GenomicScoreImplementation._do_histogram_task(
        resource, confs, "chr1", 1, 300)
    ref = GenomicScoreImplementation._do_histogram(
        resource, confs, "chr1", 1, 300)
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


def test_bulk_histogram_overlap_guard_within_one_batch(
    tmp_path: pathlib.Path,
) -> None:
    """Overlapping positions are rejected when both rows are in ONE batch.

    The sibling boundary test drives _SCAN_BATCH_SIZE down to 1, so it only
    ever exercises the carry between batches.  The within-batch comparison --
    ``kleft[1:] <= kright[:-1]`` -- had no test at all: deleting it, or
    weakening it to ``<`` so that mere adjacency slips through, left the whole
    suite green (verified by mutation).
    """
    resource = (
        a_position_score()
        .with_score("s", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  s
            chr1   1          5        0.1
            chr1   3          8        0.2
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )
    confs: dict = {"s": _hist_conf()}
    # Default batch size: both rows land in the same batch.
    with pytest.raises(ValueError, match="multiple values for positions"):
        GenomicScoreImplementation._do_histogram_bulk(
            resource, confs, "chr1", 1, 10)
    # ...and the per-record path rejects it the same way.
    with pytest.raises(ValueError, match="multiple values for positions"):
        GenomicScoreImplementation._do_histogram(
            resource, confs, "chr1", 1, 10)


def test_bulk_histogram_overlap_guard_rejects_adjacency_within_one_batch(
    tmp_path: pathlib.Path,
) -> None:
    # left == previous right is an overlap too (both rows claim that position),
    # which is why the guard uses ``<=``.  Its own test, because the ``<``
    # mutation is invisible to the case above.
    resource = (
        a_position_score()
        .with_score("s", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  s
            chr1   1          5        0.1
            chr1   5          9        0.2
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )
    confs: dict = {"s": _hist_conf()}
    with pytest.raises(ValueError, match="multiple values for positions"):
        GenomicScoreImplementation._do_histogram_bulk(
            resource, confs, "chr1", 1, 10)
    with pytest.raises(ValueError, match="multiple values for positions"):
        GenomicScoreImplementation._do_histogram(
            resource, confs, "chr1", 1, 10)
