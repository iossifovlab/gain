# pylint: disable=C0114,C0116,W0212,W0621
import pathlib

import numpy as np
import pytest
from gain.genomic_resources.genomic_position_table.record import (
    POS_BEGIN,
    POS_END,
)
from gain.genomic_resources.genomic_scores import build_score_from_resource
from gain.genomic_resources.testing.builders import (
    a_bigwig_score,
    a_position_score,
)


def _score(resource: object) -> object:
    return build_score_from_resource(resource)  # type: ignore[arg-type]


def test_region_value_arrays_one_based(tmp_path: pathlib.Path) -> None:
    resource = (
        a_position_score()
        .with_score("s", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  s
            chr1   1          3        0.1
            chr1   5          5        0.9
            chr1   8          9        0.5
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )
    score = _score(resource)
    with score.open() as opened:
        sidx = opened.score_definitions["s"].score_index
        batches = list(opened.table.get_region_value_arrays(
            "chr1", 1, 9, [sidx], 100))

    assert len(batches) == 1
    pos_begin, pos_end, cols = batches[0]
    assert list(pos_begin) == [1, 5, 8]
    assert list(pos_end) == [3, 5, 9]
    assert [str(v) for v in cols[sidx]] == ["0.1", "0.9", "0.5"]


def test_region_value_arrays_zero_based(tmp_path: pathlib.Path) -> None:
    resource = (
        a_position_score()
        .with_score("s", "float")
        .with_zero_based()
        .with_data(
            """
            chrom  pos_begin  pos_end  s
            chr1   0          2        0.1
            chr1   2          6        0.9
            chr1   6          7        0.5
            chr1   8          8        0.3
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )
    score = _score(resource)
    with score.open() as opened:
        sidx = opened.score_definitions["s"].score_index
        batches = list(opened.table.get_region_value_arrays(
            "chr1", 1, 9, [sidx], 100))
        records = list(opened.table.get_records_in_region("chr1", 1, 9))

    pos_begin, pos_end, _cols = batches[0]
    # zero-based: pos_begin+1; single-base (begin==end) also bumps pos_end --
    # which is what the last row, 8..8 -> 9..9, is here to exercise.  Without
    # it the bump could be deleted and this test would still pass (verified by
    # mutation), since no other row has begin == end.
    assert list(pos_begin) == [1, 3, 7, 9]
    assert list(pos_end) == [2, 6, 7, 9]
    # And the same transform the record path applies, on the same rows.
    assert list(zip(pos_begin, pos_end, strict=True)) == [
        (rec[POS_BEGIN], rec[POS_END]) for rec in records]


def test_region_value_arrays_stops_at_query_end(
    tmp_path: pathlib.Path,
) -> None:
    resource = (
        a_position_score()
        .with_score("s", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  s
            chr1   1          1        0.1
            chr1   3          3        0.2
            chr1   7          7        0.3
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )
    score = _score(resource)
    with score.open() as opened:
        sidx = opened.score_definitions["s"].score_index
        # end=5: the row beginning at 7 is past the query and excluded,
        # exactly as the per-record read stops at pos_begin > pos_end.
        batches = list(opened.table.get_region_value_arrays(
            "chr1", 1, 5, [sidx], 100))

    pos_begin = np.concatenate([b[0] for b in batches]) if batches \
        else np.array([])
    assert list(pos_begin) == [1, 3]


def test_region_value_arrays_batches(tmp_path: pathlib.Path) -> None:
    resource = (
        a_position_score()
        .with_score("s", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  s
            chr1   1          1        0.1
            chr1   2          2        0.2
            chr1   3          3        0.3
            chr1   4          4        0.4
            chr1   5          5        0.5
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )
    score = _score(resource)
    with score.open() as opened:
        sidx = opened.score_definitions["s"].score_index
        batches = list(opened.table.get_region_value_arrays(
            "chr1", 1, 5, [sidx], 2))

    # 5 rows in batches of 2 -> 3 batches, all rows preserved in order.
    assert [len(b[0]) for b in batches] == [2, 2, 1]
    pos_begin = np.concatenate([b[0] for b in batches])
    assert list(pos_begin) == [1, 2, 3, 4, 5]


def test_bigwig_region_value_arrays(tmp_path: pathlib.Path) -> None:
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
    score = _score(resource)
    with score.open() as opened:
        sidx = opened.score_definitions["bw"].score_index
        batches = list(opened.table.get_region_value_arrays(
            "chr1", 1, 6, [sidx], 100))

    pos_begin = np.concatenate([b[0] for b in batches])
    pos_end = np.concatenate([b[1] for b in batches])
    values = np.concatenate([b[2][sidx] for b in batches]).astype(float)
    # bigWig 0-based half-open [0,2),[2,4),[4,6) -> closed one-based.
    assert list(pos_begin) == [1, 3, 5]
    assert list(pos_end) == [2, 4, 6]
    assert list(values) == [0.0, 2.0, 4.0]


def test_bigwig_region_value_arrays_out_of_range_column_raises(
    tmp_path: pathlib.Path,
) -> None:
    """An out-of-range column must fail as the record path fails.

    A bigWig payload is the 4-tuple (chrom, pos_begin, pos_end, value), so
    ``payload[4]`` raises IndexError on the record path.  This read used to
    hand back the chromosome string for any index it did not recognise, so a
    mistyped ``index:`` in a resource config turned an aborted repair into a
    silently all-zero histogram.
    """
    resource = (
        a_bigwig_score()
        .with_score("bw", "float")
        .with_data(
            """
            chr1  0  2  0.5
            chr1  2  4  0.7
            """)
        .with_chrom_lens({"chr1": 100})
        .build_resource(tmp_path)
    )
    score = _score(resource)
    with score.open() as opened:
        with pytest.raises(IndexError):
            list(opened.table.get_region_value_arrays(
                "chr1", 1, 4, [4], 100))
        # column 0 IS the chromosome, and still is.
        batches = list(opened.table.get_region_value_arrays(
            "chr1", 1, 4, [0], 100))
    assert list(batches[0][2][0]) == ["chr1", "chr1"]
