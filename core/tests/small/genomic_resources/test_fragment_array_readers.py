"""The two array readers of :class:`FragmentScore`, against the record reads.

Each array reader is pinned to its record reader: the concatenated batches
hold the same fragments, in the same order, with the same begins, ends and
values.  The table is tabix, because only a backend that serves
``fetch_region_value_arrays`` serves the array readers.
"""
# pylint: disable=W0621,C0114,C0116
import math
import pathlib
from collections.abc import Iterable, Iterator
from typing import Any

import numpy as np
import pytest
from gain.genomic_resources.genomic_scores import FragmentScore, RecordArrays
from gain.genomic_resources.testing.builders import a_fragment_score

SCORES = ["f", "i", "s"]

READER_PAIRS = [
    ("get_fragment_arrays_starting_in_region",
     "get_fragment_scores_starting_in_region"),
    ("get_fragment_arrays_overlapping_region",
     "get_fragment_scores_overlapping_region"),
]


@pytest.fixture
def fragments(tmp_path: pathlib.Path) -> Iterator[FragmentScore]:
    """Six fragments on ``chr1``, some without a value, none on ``chr2``.

    ``(50, 149)`` begins before the region ``[100, 199]`` and reaches into
    it.  ``(190, 389)`` begins inside it and runs past its end.
    """
    score = FragmentScore(
        a_fragment_score()
        .with_score("f", "float")
        .with_score("i", "int")
        .with_score("s", "str")
        .with_na_values("NA", score_id="s")
        .with_data("""
            chrom  pos_begin  pos_end  f    i   s
            chr1   50         149      3.5  3   AAA
            chr1   100        199      NA   1   NA
            chr1   120        130      1.5  NA  BBB
            chr1   150        159      2.0  2   CCC
            chr1   190        389      4.0  4   DDD
            chr1   600        700      5.0  5   EEE
        """)
        .with_tabix()
        .build_resource(tmp_path),
    )
    score.open()
    yield score
    score.close()


def _flatten(
    batches: Iterable[RecordArrays], scores: list[str],
) -> list[tuple[int, int, tuple[Any, ...]]]:
    """The batches as ``(begin, end, values)`` rows, ``nan`` read as None."""
    rows = []
    for begins, ends, values in batches:
        assert len(begins) == len(ends)
        for row, (begin, end) in enumerate(zip(begins, ends, strict=True)):
            cells = []
            for score_id in scores:
                cell = values[score_id][row]
                if isinstance(cell, float) and math.isnan(cell):
                    cell = None
                cells.append(cell)
            rows.append((int(begin), int(end), tuple(cells)))
    return rows


def _as_floats(
    rows: Iterable[tuple[int, int, tuple[Any, ...]]],
) -> list[tuple[int, int, tuple[Any, ...]]]:
    """Record rows with each number as a float, so ``int`` meets ``float64``."""
    return [
        (begin, end, tuple(
            float(cell) if isinstance(cell, int) else cell
            for cell in values))
        for begin, end, values in rows
    ]


def test_starting_reader_matches_the_record_reader(
    fragments: FragmentScore,
) -> None:
    # Arrange
    expected = _as_floats(fragments.get_fragment_scores_starting_in_region(
        "chr1", 100, 199, scores=SCORES))

    # Act
    got = _flatten(
        fragments.get_fragment_arrays_starting_in_region(
            "chr1", 100, 199, scores=SCORES),
        SCORES)

    # Assert
    assert got == expected
    assert [(b, e) for b, e, _ in got] == [
        (100, 199), (120, 130), (150, 159), (190, 389)]


def test_overlapping_reader_matches_the_record_reader(
    fragments: FragmentScore,
) -> None:
    # Arrange
    expected = _as_floats(fragments.get_fragment_scores_overlapping_region(
        "chr1", 100, 199, scores=SCORES))

    # Act
    got = _flatten(
        fragments.get_fragment_arrays_overlapping_region(
            "chr1", 100, 199, scores=SCORES),
        SCORES)

    # Assert
    assert got == expected
    assert [(b, e) for b, e, _ in got] == [
        (50, 149), (100, 199), (120, 130), (150, 159), (190, 389)]


@pytest.mark.parametrize(
    ("array_read", "record_read", "start", "end"),
    [
        # No fragment touches [450, 550].
        ("get_fragment_arrays_starting_in_region",
         "get_fragment_scores_starting_in_region", 450, 550),
        ("get_fragment_arrays_overlapping_region",
         "get_fragment_scores_overlapping_region", 450, 550),
        # (100, 199) overlaps [160, 180], but no fragment begins in it.
        ("get_fragment_arrays_starting_in_region",
         "get_fragment_scores_starting_in_region", 160, 180),
    ],
)
def test_a_region_without_fragments_yields_no_batch(
    fragments: FragmentScore,
    array_read: str, record_read: str, start: int, end: int,
) -> None:
    # Arrange
    expected = list(getattr(fragments, record_read)(
        "chr1", start, end, scores=SCORES))

    # Act
    batches = list(getattr(fragments, array_read)(
        "chr1", start, end, scores=SCORES))

    # Assert
    assert not expected
    assert not batches


@pytest.mark.parametrize(("array_read", "record_read"), READER_PAIRS)
def test_a_small_batch_size_spreads_the_result_over_several_batches(
    fragments: FragmentScore, array_read: str, record_read: str,
) -> None:
    # Arrange
    expected = _as_floats(getattr(fragments, record_read)(
        "chr1", 100, 700, scores=SCORES))

    # Act
    batches = list(getattr(fragments, array_read)(
        "chr1", 100, 700, scores=SCORES, batch_size=2))

    # Assert
    assert len(batches) > 1
    assert all(len(begins) > 0 for begins, _, _ in batches)
    assert _flatten(batches, SCORES) == expected


BAD_REQUESTS = {
    # The resource holds no fragment on chr2 and does not know the contig.
    "unknown contig": ("chr2", 100, 199, SCORES),
    "unknown score id": ("chr1", 100, 199, ["nope"]),
    "start below 1": ("chr1", 0, 199, SCORES),
    "end before start": ("chr1", 199, 100, SCORES),
}


def _raised_by_call(call: Any) -> type[BaseException]:
    """The type that ``call()`` raises, without a ``next()`` on its result."""
    with pytest.raises(Exception) as caught:
        call()
    return caught.type


@pytest.mark.parametrize(("array_read", "record_read"), READER_PAIRS)
@pytest.mark.parametrize("request_name", sorted(BAD_REQUESTS))
def test_a_bad_request_raises_at_the_call_like_the_record_reader(
    fragments: FragmentScore,
    array_read: str, record_read: str, request_name: str,
) -> None:
    # Arrange
    chrom, start, end, scores = BAD_REQUESTS[request_name]

    def record_call() -> Any:
        return getattr(fragments, record_read)(
            chrom, start, end, scores=scores)

    def array_call() -> Any:
        return getattr(fragments, array_read)(
            chrom, start, end, scores=scores)

    # Act
    expected = _raised_by_call(record_call)
    got = _raised_by_call(array_call)

    # Assert
    assert got is expected is ValueError


@pytest.mark.parametrize(("array_read", "record_read"), READER_PAIRS)
def test_a_closed_score_raises_at_the_call_like_the_record_reader(
    fragments: FragmentScore, array_read: str, record_read: str,
) -> None:
    # Arrange
    fragments.close()

    # Act
    expected = _raised_by_call(lambda: getattr(fragments, record_read)(
        "chr1", 100, 199, scores=SCORES))
    got = _raised_by_call(lambda: getattr(fragments, array_read)(
        "chr1", 100, 199, scores=SCORES))

    # Assert
    assert got is expected is ValueError


@pytest.mark.parametrize(("array_read", "record_read"), READER_PAIRS)
def test_no_score_list_reads_every_score(
    fragments: FragmentScore, array_read: str, record_read: str,
) -> None:
    # Arrange
    expected = _as_floats(getattr(fragments, record_read)("chr1", 100, 199))

    # Act
    batches = list(getattr(fragments, array_read)("chr1", 100, 199))

    # Assert
    assert [list(values) for _, _, values in batches] == [SCORES]
    assert _flatten(batches, SCORES) == expected


def test_the_values_follow_the_parse_contract(
    fragments: FragmentScore,
) -> None:
    # Act
    [(begins, ends, values)] = list(
        fragments.get_fragment_arrays_overlapping_region(
            "chr1", 100, 130, scores=SCORES))

    # Assert
    assert begins.tolist() == [50, 100, 120]
    assert ends.tolist() == [149, 199, 130]
    assert values["f"].dtype == np.float64
    assert np.array_equal(
        values["f"], [3.5, np.nan, 1.5], equal_nan=True)
    assert values["i"].dtype == np.float64
    assert np.array_equal(values["i"], [3.0, 1.0, np.nan], equal_nan=True)
    assert values["s"].dtype == object
    assert values["s"].tolist() == ["AAA", None, "BBB"]


@pytest.mark.parametrize(("array_read", "record_read"), READER_PAIRS)
def test_a_table_without_array_reads_raises_type_error_at_the_call(
    tmp_path: pathlib.Path, array_read: str, record_read: str,
) -> None:
    # Arrange
    score = FragmentScore(
        a_fragment_score()
        .with_score("f", "float")
        .with_data("""
            chrom  pos_begin  pos_end  f
            chr1   100        199      1.0
        """)
        .build_resource(tmp_path),
    )
    score.open()

    # Act
    records = list(getattr(score, record_read)("chr1", 100, 199))
    with pytest.raises(TypeError):
        getattr(score, array_read)("chr1", 100, 199)

    # Assert
    assert len(records) == 1
    score.close()


@pytest.fixture
def edge_fragments(tmp_path: pathlib.Path) -> Iterator[FragmentScore]:
    """Fragments on each inclusive edge of the region ``[100, 199]``.

    ``(50, 99)`` and ``(200, 250)`` are adjacent to the region and touch
    no position of it. ``(60, 100)`` ends at its start, and ``(199, 250)``
    begins at its end.
    """
    score = FragmentScore(
        a_fragment_score()
        .with_score("f", "float")
        .with_data("""
            chrom  pos_begin  pos_end  f
            chr1   50         99       1.0
            chr1   60         100      2.0
            chr1   199        250      3.0
            chr1   200        250      4.0
        """)
        .with_tabix()
        .build_resource(tmp_path),
    )
    score.open()
    yield score
    score.close()


@pytest.mark.parametrize(
    ("array_read", "record_read", "extents"),
    [
        ("get_fragment_arrays_starting_in_region",
         "get_fragment_scores_starting_in_region",
         [(199, 250)]),
        ("get_fragment_arrays_overlapping_region",
         "get_fragment_scores_overlapping_region",
         [(60, 100), (199, 250)]),
    ],
)
def test_the_inclusive_region_edges_match_the_record_reader(
    edge_fragments: FragmentScore,
    array_read: str, record_read: str, extents: list[tuple[int, int]],
) -> None:
    # Arrange
    expected = _as_floats(getattr(edge_fragments, record_read)(
        "chr1", 100, 199, scores=["f"]))

    # Act
    got = _flatten(
        getattr(edge_fragments, array_read)(
            "chr1", 100, 199, scores=["f"]),
        ["f"])

    # Assert
    assert got == expected
    assert [(b, e) for b, e, _ in got] == extents
