# pylint: disable=C0114,C0116,W0212,W0621
import pathlib

import numpy as np
import pytest
from gain.genomic_resources.histogram import (
    CategoricalHistogram,
    CategoricalHistogramConfig,
    NullHistogram,
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
    # An UNBOUNDED region -- a contig with no start/end -- is not
    # bulk-eligible: the bulk path wants concrete bounds.  This is the shape
    # ``--region-size 0`` produces now that a contig is required, where it
    # used to be spelled ``chrom=None``.
    resource = _multiscore_tabix(tmp_path)
    confs: dict = {"s1": _hist_conf(), "s2": _hist_conf()}
    via_task = GenomicScoreImplementation._do_histogram_task(
        resource, confs, "chr1", None, None)
    ref = GenomicScoreImplementation._do_histogram(
        resource, confs, "chr1", None, None)
    _assert_hists_equal(via_task, ref)


def _int_hist_conf() -> NumberHistogramConfig:
    return NumberHistogramConfig.from_dict({
        "type": "number", "view_range": {"min": 0, "max": 10},
        "number_of_bins": 10, "x_log_scale": False, "y_log_scale": False})


def _int_position_tabix(tmp_path: pathlib.Path) -> GenomicResource:
    """An int score whose column carries every edge the two parses differ on.

    ``3.5``, ``1e3`` and ``0x10`` are what ``float()`` accepts and ``int()``
    does not, ``1_000`` and ``١٢٣`` are what both accept, and ``.`` is the
    configured non-value -- so a column parse that quietly used float
    semantics would bin three values the per-record path drops.
    """
    return (
        a_position_score()
        .with_score("s", "int")
        .with_data(
            """
            chrom  pos_begin  pos_end  s
            chr1   1          3        3
            chr1   4          4        7
            chr1   5          10       3.5
            chr1   11         11       .
            chr1   12         13       1e3
            chr1   14         14       0x10
            chr1   15         18       1_000
            chr1   19         20       ١٢٣
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )


def test_int_score_is_bulk_eligible(tmp_path: pathlib.Path) -> None:
    resource = _int_position_tabix(tmp_path)
    confs: dict = {"s": _int_hist_conf()}
    assert GenomicScoreImplementation._can_bulk_histogram(resource, confs)


def test_bulk_histogram_matches_per_record_int_score(
    tmp_path: pathlib.Path,
) -> None:
    resource = _int_position_tabix(tmp_path)
    confs: dict = {"s": _int_hist_conf()}

    ref = GenomicScoreImplementation._do_histogram(
        resource, confs, "chr1", 1, 20)
    bulk = GenomicScoreImplementation._do_histogram_bulk(
        resource, confs, "chr1", 1, 20)

    _assert_hists_equal(bulk, ref)
    # sanity: the tokens int() refuses really were dropped, and the ones it
    # accepts really were binned -- three records in view, spanning 3+1+4 bp.
    assert ref["s"].bars.sum() == 3 + 1
    assert ref["s"].out_of_range_bins == [0, 4 + 2]


def test_bulk_histogram_matches_per_record_int_score_via_the_task(
    tmp_path: pathlib.Path,
) -> None:
    resource = _int_position_tabix(tmp_path)
    confs: dict = {"s": _int_hist_conf()}

    via_task = GenomicScoreImplementation._do_histogram_task(
        resource, confs, "chr1", 1, 20)
    ref = GenomicScoreImplementation._do_histogram(
        resource, confs, "chr1", 1, 20)

    _assert_hists_equal(via_task, ref)


def _assert_categorical_equal(
    bulk: dict, ref: dict,
) -> None:
    assert set(bulk) == set(ref)
    for score_id in ref:
        got, want = bulk[score_id], ref[score_id]
        assert type(got) is type(want), (score_id, got, want)
        if isinstance(want, NullHistogram):
            assert got.reason == want.reason, score_id
            continue
        assert got.raw_values == want.raw_values, score_id
        assert list(got.raw_values) == list(want.raw_values), score_id


def _str_position_tabix(tmp_path: pathlib.Path) -> GenomicResource:
    """A str score over multi-base records, so the weights actually differ.

    A position score weighs a record by the base pairs of the region it
    covers, and a categorical count that ignored the weight would still agree
    with the per-record path on which values it saw -- only on how many.
    """
    return (
        a_position_score()
        .with_score("s", "str")
        .with_data(
            """
            chrom  pos_begin  pos_end  s
            chr1   1          3        aaa
            chr1   4          4        bbb
            chr1   5          10       aaa
            chr1   11         11       ccc
            chr1   12         20       bbb
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )


def test_str_score_with_a_categorical_histogram_is_bulk_eligible(
    tmp_path: pathlib.Path,
) -> None:
    resource = _str_position_tabix(tmp_path)
    confs: dict = {"s": CategoricalHistogramConfig.default_config()}
    assert GenomicScoreImplementation._can_bulk_histogram(resource, confs)


def test_bulk_categorical_matches_per_record_str_score(
    tmp_path: pathlib.Path,
) -> None:
    resource = _str_position_tabix(tmp_path)
    confs: dict = {"s": CategoricalHistogramConfig.default_config()}

    ref = GenomicScoreImplementation._do_histogram(
        resource, confs, "chr1", 1, 20)
    bulk = GenomicScoreImplementation._do_histogram_bulk(
        resource, confs, "chr1", 1, 20)

    _assert_categorical_equal(bulk, ref)
    # sanity: counted by span, not by record -- "aaa" covers 3 + 6 bases.
    assert ref["s"].raw_values == {"aaa": 9, "bbb": 10, "ccc": 1}


def test_bulk_categorical_matches_per_record_str_score_clipped(
    tmp_path: pathlib.Path,
) -> None:
    """The region clip weighs a partially covered record by its overlap."""
    resource = _str_position_tabix(tmp_path)
    confs: dict = {"s": CategoricalHistogramConfig.default_config()}

    ref = GenomicScoreImplementation._do_histogram(
        resource, confs, "chr1", 2, 13)
    bulk = GenomicScoreImplementation._do_histogram_bulk(
        resource, confs, "chr1", 2, 13)

    _assert_categorical_equal(bulk, ref)
    assert ref["s"].raw_values == {"aaa": 2 + 6, "bbb": 1 + 2, "ccc": 1}


def test_bulk_categorical_matches_per_record_via_the_task(
    tmp_path: pathlib.Path,
) -> None:
    resource = _str_position_tabix(tmp_path)
    confs: dict = {"s": CategoricalHistogramConfig.default_config()}

    via_task = GenomicScoreImplementation._do_histogram_task(
        resource, confs, "chr1", 1, 20)
    ref = GenomicScoreImplementation._do_histogram(
        resource, confs, "chr1", 1, 20)

    _assert_categorical_equal(via_task, ref)


def test_an_int_score_with_a_categorical_histogram_keeps_the_per_record_path(
    tmp_path: pathlib.Path,
) -> None:
    """A categorical histogram is a bulk path for ``str`` scores only.

    The bulk read yields an int score's column as ``float64`` -- its non-value
    has to be a nan -- and a categorical histogram refuses a float outright.
    Routing one here would nullify a histogram the per-record path builds
    happily, so the dispatch keeps it where it works.
    """
    resource = _int_position_tabix(tmp_path)
    confs: dict = {"s": CategoricalHistogramConfig.default_config()}

    assert not GenomicScoreImplementation._can_bulk_histogram(resource, confs)

    # ...and the per-record path it stays on does build the histogram.
    ref = GenomicScoreImplementation._do_histogram(
        resource, confs, "chr1", 1, 20)
    assert ref["s"].raw_values == {3: 3, 7: 1, 1000: 4, 123: 2}


def test_a_str_score_with_a_number_histogram_keeps_the_per_record_path(
    tmp_path: pathlib.Path,
) -> None:
    """A misconfigured score must still nullify, not crash the scan.

    A number histogram over a str score is a config error, and the per-record
    path answers it the way it answers every other one: the histogram refuses
    the value, that score is nullified and the resource keeps its remaining
    statistics.  A batch of str cells is not a value ``NumberHistogram`` can
    refuse -- it is a coercion failure inside ``add_batch`` -- so the pairing
    rule keeps this off the bulk path rather than letting it raise there.
    """
    resource = _str_position_tabix(tmp_path)
    confs: dict = {"s": _hist_conf()}

    assert not GenomicScoreImplementation._can_bulk_histogram(resource, confs)

    ref = GenomicScoreImplementation._do_histogram_task(
        resource, confs, "chr1", 1, 20)
    assert isinstance(ref["s"], NullHistogram)


def _many_valued_str_tabix(
    tmp_path: pathlib.Path, distinct: int,
) -> GenomicResource:
    rows = "\n".join(
        f"chr1  {pos}  {pos}  v{pos}" for pos in range(1, distinct + 1))
    return (
        a_position_score()
        .with_score("s", "str")
        .with_data(f"chrom  pos_begin  pos_end  s\n{rows}")
        .with_tabix()
        .build_resource(tmp_path)
    )


def test_bulk_categorical_nullifies_exactly_as_per_record_does(
    tmp_path: pathlib.Path,
) -> None:
    """Too many distinct values nullifies the score, with the same reason.

    The reason string is not incidental: it is what the saved statistics
    carry in place of the histogram, so a batched count that reported its own
    overshoot would change a resource's recorded statistics.
    """
    distinct = CategoricalHistogram.UNIQUE_VALUES_LIMIT + 30
    resource = _many_valued_str_tabix(tmp_path, distinct)
    confs: dict = {"s": CategoricalHistogramConfig.default_config()}

    ref = GenomicScoreImplementation._do_histogram(
        resource, confs, "chr1", 1, distinct)
    bulk = GenomicScoreImplementation._do_histogram_bulk(
        resource, confs, "chr1", 1, distinct)

    assert isinstance(ref["s"], NullHistogram)
    _assert_categorical_equal(bulk, ref)


def test_a_nullified_score_does_not_cost_the_others_theirs(
    tmp_path: pathlib.Path,
) -> None:
    """One score's overflow leaves the rest of the resource's statistics.

    The per-record path replaces the failed histogram and carries on; the
    bulk path has to do the same, or a single unhistogrammable column would
    take a whole resource's statistics down with it.
    """
    limit = CategoricalHistogram.UNIQUE_VALUES_LIMIT
    rows = "\n".join(
        f"chr1  {pos}  {pos}  v{pos}  0.5" for pos in range(1, limit + 30))
    resource = (
        a_position_score()
        .with_score("s", "str")
        .with_score("f", "float")
        .with_data(f"chrom  pos_begin  pos_end  s  f\n{rows}")
        .with_tabix()
        .build_resource(tmp_path)
    )
    confs: dict = {
        "s": CategoricalHistogramConfig.default_config(),
        "f": _hist_conf(),
    }

    ref = GenomicScoreImplementation._do_histogram(
        resource, confs, "chr1", 1, limit + 30)
    bulk = GenomicScoreImplementation._do_histogram_bulk(
        resource, confs, "chr1", 1, limit + 30)

    assert isinstance(bulk["s"], NullHistogram)
    assert isinstance(bulk["f"], NumberHistogram)
    _assert_hists_equal({"f": bulk["f"]}, {"f": ref["f"]})
    assert bulk["f"].bars.sum() == limit + 29


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
    # An np_score is deliberately left out of the bulk gate: no production GRR
    # has one, so the bulk path is not exercised against it and is not opened
    # to it untested.  The exclusion is NOT about accumulator semantics -- an
    # np_score reads with the same per-allele (weight-1, several records at a
    # position) rules an allele score does, and gain#421 admitted those by
    # having both scan paths read the kind's own record facts.  The dispatch
    # must keep such scores on the per-record path -- and never raise.
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
