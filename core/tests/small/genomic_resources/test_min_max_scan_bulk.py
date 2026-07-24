# pylint: disable=C0114,C0116,W0212,W0621
import pathlib

import numpy as np
from gain.genomic_resources.implementations.genomic_scores_impl import (
    GenomicScoreImplementation as G,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing.builders import (
    a_bigwig_score,
    a_cnv_collection,
    a_position_score,
    an_allele_score,
)


def _assert_min_max_equal(bulk: dict, ref: dict) -> None:
    assert set(bulk) == set(ref)
    for sid in ref:
        got, want = bulk[sid], ref[sid]
        assert np.array_equal([got.min], [want.min], equal_nan=True), \
            (sid, got.min, want.min)
        assert np.array_equal([got.max], [want.max], equal_nan=True), \
            (sid, got.max, want.max)
        assert got.count == want.count, (sid, got.count, want.count)


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


def test_bulk_min_max_matches_per_record_allele_score(
    tmp_path: pathlib.Path,
) -> None:
    """Several records at one position must not abort the min/max scan."""
    resource = (
        an_allele_score()
        .with_score("s", "float")
        .with_data(
            """
            chrom  pos_begin  reference  alternative  s
            chr1   10         A          G            0.1
            chr1   10         A          C            0.7
            chr1   10         A          T            0.3
            chr1   16         C          T            0.5
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )

    ref = G._do_min_max(resource, ["s"], "chr1", 1, 20)
    bulk = G._do_min_max_bulk(resource, ["s"], "chr1", 1, 20)

    _assert_min_max_equal(bulk, ref)
    # The extremes both sit on records that share position 10 with others,
    # so a scan that dropped or deduplicated them would not find these.
    assert (bulk["s"].min, bulk["s"].max) == (0.1, 0.7)


def test_bulk_min_max_matches_per_record_cnv_collection(
    tmp_path: pathlib.Path,
) -> None:
    """A CNV collection's min/max also carries a record COUNT.

    Unlike a position score -- whose count stays 0 -- the per-record path
    counts every CNV, so the bulk path has to as well or the two disagree on
    a field that reaches the serialized statistic.
    """
    resource = (
        a_cnv_collection()
        .with_score("s", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  s
            chr1   10         100      0.1
            chr1   20         200      0.7
            chr1   30         40       0.3
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )

    ref = G._do_min_max(resource, ["s"], "chr1", 1, 300)
    bulk = G._do_min_max_bulk(resource, ["s"], "chr1", 1, 300)

    _assert_min_max_equal(bulk, ref)
    assert (bulk["s"].min, bulk["s"].max) == (0.1, 0.7)
    assert bulk["s"].count == 3


def test_dispatch_min_max_uses_bulk_for_allele_score(
    tmp_path: pathlib.Path,
) -> None:
    """The min/max dispatch must let allele scores through too."""
    resource = (
        an_allele_score()
        .with_score("s", "float")
        .with_data(
            """
            chrom  pos_begin  reference  alternative  s
            chr1   10         A          G            0.1
            chr1   10         A          C            0.7
            chr1   16         C          T            0.5
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )

    assert G._bulk_scan_eligible(resource, ["s"])
    via_task = G._do_min_max_task(resource, ["s"], "chr1", 1, 20)
    ref = G._do_min_max(resource, ["s"], "chr1", 1, 20)

    _assert_min_max_equal(via_task, ref)


def test_bulk_min_max_matches_per_record_tabix(
    tmp_path: pathlib.Path,
) -> None:
    resource = _multiscore_tabix(tmp_path)
    ref = G._do_min_max(resource, ["s1", "s2"], "chr1", 1, 20)
    bulk = G._do_min_max_bulk(resource, ["s1", "s2"], "chr1", 1, 20)
    _assert_min_max_equal(bulk, ref)
    assert (bulk["s1"].min, bulk["s1"].max) == (0.1, 1.0)
    assert (bulk["s2"].min, bulk["s2"].max) == (0.0, 0.9)  # NA rows skipped


def test_bulk_min_max_matches_per_record_subregion(
    tmp_path: pathlib.Path,
) -> None:
    resource = _multiscore_tabix(tmp_path)
    ref = G._do_min_max(resource, ["s1", "s2"], "chr1", 5, 15)
    bulk = G._do_min_max_bulk(resource, ["s1", "s2"], "chr1", 5, 15)
    _assert_min_max_equal(bulk, ref)


def test_bulk_min_max_matches_per_record_zero_based(
    tmp_path: pathlib.Path,
) -> None:
    resource = (
        a_position_score()
        .with_score("s", "float")
        .with_zero_based()
        .with_data(
            """
            chrom  pos_begin  pos_end  s
            chr1   0          2        0.3
            chr1   2          6        0.95
            chr1   6          7        0.1
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )
    ref = G._do_min_max(resource, ["s"], "chr1", 1, 7)
    bulk = G._do_min_max_bulk(resource, ["s"], "chr1", 1, 7)
    _assert_min_max_equal(bulk, ref)


def test_bulk_min_max_empty_region_is_nan(tmp_path: pathlib.Path) -> None:
    resource = _multiscore_tabix(tmp_path)
    # A region below all data: both paths leave min/max as nan.
    ref = G._do_min_max(resource, ["s1"], "chr1", 100, 200)
    bulk = G._do_min_max_bulk(resource, ["s1"], "chr1", 100, 200)
    _assert_min_max_equal(bulk, ref)
    assert np.isnan(bulk["s1"].min) and np.isnan(bulk["s1"].max)


def test_bulk_min_max_matches_per_record_bigwig(
    tmp_path: pathlib.Path,
) -> None:
    resource = (
        a_bigwig_score()
        .with_score("bw", "float")
        .with_data(
            """
            chr1  0  2  0.0
            chr1  2  4  2.5
            chr1  4  6  4.0
            """)
        .with_chrom_lens({"chr1": 100})
        .build_resource(tmp_path)
    )
    ref = G._do_min_max(resource, ["bw"], "chr1", 1, 6)
    bulk = G._do_min_max_bulk(resource, ["bw"], "chr1", 1, 6)
    _assert_min_max_equal(bulk, ref)


def test_dispatch_min_max_uses_bulk_and_matches(
    tmp_path: pathlib.Path,
) -> None:
    resource = _multiscore_tabix(tmp_path)
    assert G._bulk_scan_eligible(resource, ["s1", "s2"])
    via_task = G._do_min_max_task(resource, ["s1", "s2"], "chr1", 1, 20)
    ref = G._do_min_max(resource, ["s1", "s2"], "chr1", 1, 20)
    _assert_min_max_equal(via_task, ref)


def test_dispatch_min_max_falls_back_for_whole_table_scan(
    tmp_path: pathlib.Path,
) -> None:
    resource = _multiscore_tabix(tmp_path)
    via_task = G._do_min_max_task(resource, ["s1"], None, None, None)
    ref = G._do_min_max(resource, ["s1"], None, None, None)
    _assert_min_max_equal(via_task, ref)


def test_int_score_is_not_bulk_scan_eligible(
    tmp_path: pathlib.Path,
) -> None:
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
    assert not G._bulk_scan_eligible(resource, ["s"])


def test_bulk_min_max_matches_per_record_literal_nan(
    tmp_path: pathlib.Path,
) -> None:
    # A literal 'nan' token that is NOT a configured NA sentinel (na_values is
    # "." here, so the default "nan" sentinel is dropped): both paths skip it
    # for min/max -- MinMaxValue skips nan like NumberHistogram does, rather
    # than letting min(nan, x) wipe the running extremum.
    resource = (
        a_position_score()
        .with_score("s", "float")
        .with_na_values(".")
        .with_data(
            """
            chrom  pos_begin  pos_end  s
            chr1   1          2        0.5
            chr1   3          4        nan
            chr1   5          6        0.9
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )
    ref = G._do_min_max(resource, ["s"], "chr1", 1, 6)
    bulk = G._do_min_max_bulk(resource, ["s"], "chr1", 1, 6)
    _assert_min_max_equal(bulk, ref)
    assert (bulk["s"].min, bulk["s"].max) == (0.5, 0.9)


def test_bulk_min_max_matches_per_record_high_precision_tokens(
    tmp_path: pathlib.Path,
) -> None:
    # Scientific notation and >=16 significant digits -- the shape a p-value or
    # allele-frequency column has.  ``pd.to_numeric`` is NOT correctly rounded
    # here (it also truncates long decimals to ~10 significant digits), so a
    # parser built on it diverges from the per-record ``float()`` by ULPs or
    # much more, silently, on exactly the resources that need precision most.
    resource = (
        a_position_score()
        .with_score("s", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  s
            chr1   1          1        1e-25
            chr1   2          2        0.00000071009127180852
            chr1   3          3        96.43868415975565
            chr1   4          4        6.754841e-20
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )
    ref = G._do_min_max(resource, ["s"], "chr1", 1, 4)
    bulk = G._do_min_max_bulk(resource, ["s"], "chr1", 1, 4)
    _assert_min_max_equal(bulk, ref)
    # Pinned exactly: these are the values Python's float() produces.
    assert bulk["s"].min == 1e-25
    assert bulk["s"].max == 96.43868415975565
