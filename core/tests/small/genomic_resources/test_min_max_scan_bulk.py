# pylint: disable=C0114,C0116,W0212,W0621
import pathlib

import numpy as np
from gain.genomic_resources.implementations.genomic_scores_impl import (
    GenomicScoreImplementation as G,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing.builders import (
    a_bigwig_score,
    a_position_score,
)


def _assert_min_max_equal(bulk: dict, ref: dict) -> None:
    assert set(bulk) == set(ref)
    for sid in ref:
        got, want = bulk[sid], ref[sid]
        assert np.array_equal([got.min], [want.min], equal_nan=True), \
            (sid, got.min, want.min)
        assert np.array_equal([got.max], [want.max], equal_nan=True), \
            (sid, got.max, want.max)


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
    # An unbounded region -- a contig with no start/end -- keeps the
    # per-record path; see the histogram twin of this test.
    resource = _multiscore_tabix(tmp_path)
    via_task = G._do_min_max_task(resource, ["s1"], "chr1", None, None)
    ref = G._do_min_max(resource, ["s1"], "chr1", None, None)
    _assert_min_max_equal(via_task, ref)


def _int_tabix(tmp_path: pathlib.Path) -> GenomicResource:
    return (
        a_position_score()
        .with_score("s", "int")
        .with_data(
            """
            chrom  pos_begin  pos_end  s
            chr1   1          2        3
            chr1   3          4        7
            chr1   5          6        .
            chr1   7          8        3.5
            chr1   9          10       -4
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )


def test_int_score_is_bulk_scan_eligible(
    tmp_path: pathlib.Path,
) -> None:
    assert G._bulk_scan_eligible(_int_tabix(tmp_path), ["s"])


def test_bulk_min_max_matches_per_record_int_score(
    tmp_path: pathlib.Path,
) -> None:
    resource = _int_tabix(tmp_path)
    ref = G._do_min_max(resource, ["s"], "chr1", 1, 10)
    bulk = G._do_min_max_bulk(resource, ["s"], "chr1", 1, 10)

    _assert_min_max_equal(bulk, ref)
    # "3.5" is not an int, so it is a non-value in both paths, and "." is the
    # configured one -- the extremes come from 3, 7 and -4.
    assert (bulk["s"].min, bulk["s"].max) == (-4, 7)


def test_an_int_score_min_max_serializes_as_ints(
    tmp_path: pathlib.Path,
) -> None:
    """The saved statistic keeps the type the value has.

    ``MinMaxValue.serialize`` writes whatever it was folded with, so a column
    read that left the extremes as ``float`` would rewrite every int score's
    ``min_max`` file from ``min: 3`` to ``min: 3.0`` the next time its
    statistics were built.
    """
    resource = _int_tabix(tmp_path)
    ref = G._do_min_max(resource, ["s"], "chr1", 1, 10)
    bulk = G._do_min_max_bulk(resource, ["s"], "chr1", 1, 10)

    assert bulk["s"].serialize() == ref["s"].serialize()
    assert "min: -4\n" in bulk["s"].serialize()


def _str_tabix(tmp_path: pathlib.Path, cell: str = "aaa") -> GenomicResource:
    return (
        a_position_score()
        .with_score("s", "str")
        .with_na_values(".")
        .with_data(
            f"""
            chrom  pos_begin  pos_end  s
            chr1   1          2        {cell}
            chr1   3          4        {cell}
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )


def test_a_str_score_is_bulk_scan_eligible_as_a_column_read(
    tmp_path: pathlib.Path,
) -> None:
    """The capability query is about the parse, not about min/max.

    The bulk column read serves a str score, which is what makes its
    categorical histogram batchable -- and it is the min/max consumer's own
    job to add that a min/max is a number, which the sibling below pins.
    """
    assert G._bulk_scan_eligible(_str_tabix(tmp_path), ["s"])


def test_a_str_score_never_takes_the_bulk_min_max_path(
    tmp_path: pathlib.Path,
) -> None:
    """A min/max reduces with ``np.isnan``, so it is numbers only.

    A str score is scheduled for a min/max scan only through a
    misconfiguration -- a number histogram over a str score, with no view
    range -- but the column read serves it, so nothing in
    :meth:`_bulk_scan_eligible` alone keeps it off this path.  The condition
    belongs to this consumer, which is where it is stated.
    """
    resource = _str_tabix(tmp_path)

    assert G._bulk_scan_eligible(resource, ["s"])
    assert not G._can_bulk_min_max(resource, ["s"])


def test_an_all_na_str_column_does_not_raise_through_the_task(
    tmp_path: pathlib.Path,
) -> None:
    """The shape where an ungated bulk min/max would change the outcome.

    With real text in the column both paths refuse the value alike.  With
    every cell an NA sentinel the per-record path completes quietly on an
    empty min/max -- so this is the column where reducing an object array
    instead raises ``ufunc 'isnan' not supported``, out of a generator and
    past every nullify handler, and a resource that used to degrade to
    nullified statistics dies outright.
    """
    resource = _str_tabix(tmp_path, cell=".")

    ref = G._do_min_max(resource, ["s"], "chr1", 1, 4)
    dispatched = G._do_min_max_task(resource, ["s"], "chr1", 1, 4)

    _assert_min_max_equal(dispatched, ref)
    assert np.isnan(ref["s"].min) and np.isnan(ref["s"].max)


def test_a_bool_score_is_not_bulk_scan_eligible(
    tmp_path: pathlib.Path,
) -> None:
    """No column parse is defined for ``bool``, so the gate stays shut."""
    resource = (
        a_position_score()
        .with_score("s", "bool")
        .with_data(
            """
            chrom  pos_begin  pos_end  s
            chr1   1          2        True
            chr1   3          4        False
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
