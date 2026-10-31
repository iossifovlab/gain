"""The array readers of ``FragmentScore``, against their record readers.

``get_fragment_arrays_starting_in_region`` and
``get_fragment_arrays_overlapping_region`` read the same fragments as
``get_fragment_scores_starting_in_region`` and
``get_fragment_scores_overlapping_region``, in batches of column arrays.
Each test reads one region down both doors and compares them fragment for
fragment.

The toy resource has a ``str`` barcode (``cell``) and an ``int`` count
(``count``), each with one ``NA``. A ``str`` score has no default
``na_values``, so the fixture names ``NA`` for ``cell``. It holds contig
``1`` only.

==============  ======  =====
fragment        cell    count
==============  ======  =====
``(3, 12)``     AAA     2
``(10, 25)``    BBB     NA
``(11, 14)``    CCC     1
``(15, 40)``    AAA     3
``(33, 34)``    NA      4
==============  ======  =====
"""
# W0621: a test takes a pytest fixture as an argument of the same name.
# C0116: the name of each test states its behaviour, so a test has no
# docstring.
# pylint: disable=W0621,C0116
import math
import pathlib
from collections.abc import Iterable

import numpy as np
import pytest
from gain.genomic_resources.genomic_scores import FragmentScore
from gain.genomic_resources.genomic_scores.records import RecordArrays
from gain.genomic_resources.score_def import ScoreValue
from gain.genomic_resources.testing.builders import a_fragment_score

FRAGMENTS = """
    chrom  pos_begin  pos_end  cell  count
    1      3          12       AAA   2
    1      10         25       BBB   NA
    1      11         14       CCC   1
    1      15         40       AAA   3
    1      33         34       NA    4
"""

SCORES = ["cell", "count"]

Fragment = tuple[int, int, tuple[ScoreValue, ...]]


@pytest.fixture
def fragments(tmp_path: pathlib.Path) -> FragmentScore:
    return FragmentScore(
        a_fragment_score()
        .with_score("cell", "str")
        .with_score("count", "int")
        .with_na_values("NA", score_id="cell")
        .with_data(FRAGMENTS)
        .with_tabix()
        .build_resource(tmp_path))


def _unbatched(
    batches: Iterable[RecordArrays], scores: list[str],
) -> list[Fragment]:
    """Flatten array batches into the tuples a record reader yields."""
    result: list[Fragment] = []
    for begins, ends, values in batches:
        assert list(values) == scores
        result.extend(
            (int(beg), int(end),
             tuple(_as_record_value(values[s][row]) for s in scores))
            for row, (beg, end) in enumerate(zip(begins, ends, strict=True)))
    return result


def _as_record_value(value: ScoreValue) -> ScoreValue:
    """Read an array cell the way a record reader reports it.

    A ``float64`` ``nan`` is the array form of ``None``.
    """
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def test_the_starting_reader_reads_what_the_record_reader_reads(
    fragments: FragmentScore,
) -> None:
    with fragments.open() as score:
        records = list(score.get_fragment_scores_starting_in_region(
            "1", 10, 30, scores=SCORES))
        arrays = _unbatched(score.get_fragment_arrays_starting_in_region(
            "1", 10, 30, scores=SCORES), SCORES)

    assert [(beg, end) for beg, end, _ in arrays] == [
        (10, 25), (11, 14), (15, 40)]
    assert arrays == records


def test_the_overlapping_reader_reads_what_the_record_reader_reads(
    fragments: FragmentScore,
) -> None:
    with fragments.open() as score:
        records = list(score.get_fragment_scores_overlapping_region(
            "1", 10, 30, scores=SCORES))
        arrays = _unbatched(score.get_fragment_arrays_overlapping_region(
            "1", 10, 30, scores=SCORES), SCORES)

    assert arrays == records


def test_the_overlapping_reader_keeps_a_fragment_that_begins_before_the_region(
    fragments: FragmentScore,
) -> None:
    with fragments.open() as score:
        arrays = _unbatched(score.get_fragment_arrays_overlapping_region(
            "1", 10, 30, scores=SCORES), SCORES)

    assert [(beg, end) for beg, end, _ in arrays] == [
        (3, 12), (10, 25), (11, 14), (15, 40)]


READERS = [
    pytest.param(
        "get_fragment_arrays_starting_in_region",
        "get_fragment_scores_starting_in_region", id="starting"),
    pytest.param(
        "get_fragment_arrays_overlapping_region",
        "get_fragment_scores_overlapping_region", id="overlapping"),
]


@pytest.mark.parametrize(("array_reader", "record_reader"), READERS)
def test_a_contig_the_resource_lacks_is_refused_as_by_the_record_reader(
    fragments: FragmentScore, array_reader: str, record_reader: str,
) -> None:
    with fragments.open() as score:
        with pytest.raises(ValueError, match="2") as record_error:
            getattr(score, record_reader)("2", 1, 100, scores=SCORES)
        with pytest.raises(ValueError, match="2") as array_error:
            getattr(score, array_reader)("2", 1, 100, scores=SCORES)

    assert str(array_error.value) == str(record_error.value)


@pytest.mark.parametrize(("array_reader", "record_reader"), READERS)
def test_a_region_with_no_fragments_reads_nothing_as_by_the_record_reader(
    fragments: FragmentScore, array_reader: str, record_reader: str,
) -> None:
    with fragments.open() as score:
        records = list(getattr(score, record_reader)(
            "1", 50, 100, scores=SCORES))
        batches = list(getattr(score, array_reader)(
            "1", 50, 100, scores=SCORES))

    assert not records
    assert not batches


@pytest.mark.parametrize(("array_reader", "record_reader"), READERS)
@pytest.mark.parametrize(("start", "end", "scores"), [
    pytest.param(0, 10, SCORES, id="start-below-one"),
    pytest.param(10, 9, SCORES, id="end-before-start"),
    pytest.param(1, 10, ["nope"], id="unknown-score-id"),
])
def test_an_invalid_request_is_refused_on_the_call_as_by_the_record_reader(
    fragments: FragmentScore, array_reader: str, record_reader: str,
    start: int, end: int, scores: list[str],
) -> None:
    with fragments.open() as score:
        with pytest.raises(ValueError) as record_error:
            getattr(score, record_reader)("1", start, end, scores=scores)
        with pytest.raises(ValueError) as array_error:
            getattr(score, array_reader)("1", start, end, scores=scores)

    assert str(array_error.value) == str(record_error.value)


def test_a_closed_score_is_refused_on_the_call(
    fragments: FragmentScore,
) -> None:
    with pytest.raises(ValueError, match="is not open"):
        fragments.get_fragment_arrays_overlapping_region(
            "1", 1, 10, scores=SCORES)


@pytest.mark.parametrize(("array_reader", "record_reader"), READERS)
def test_a_result_larger_than_a_batch_spans_batches_in_record_order(
    fragments: FragmentScore, array_reader: str, record_reader: str,
) -> None:
    with fragments.open() as score:
        # In batches of 2, the starting reader keeps no row of the first
        # batch, (15, 40) of the second and all of the third.
        records = list(getattr(score, record_reader)(
            "1", 12, 40, scores=SCORES))
        batches = list(getattr(score, array_reader)(
            "1", 12, 40, scores=SCORES, batch_size=2))

    assert len(batches) > 1
    assert all(len(begins) > 0 for begins, _, _ in batches)
    assert _unbatched(batches, SCORES) == records


def test_the_values_follow_the_parse_contract_of_the_value_arrays(
    fragments: FragmentScore,
) -> None:
    with fragments.open() as score:
        [(_, _, values)] = list(
            score.get_fragment_arrays_overlapping_region(
                "1", 1, 40, scores=SCORES))

    assert values["cell"].dtype == np.dtype(object)
    assert list(values["cell"]) == ["AAA", "BBB", "CCC", "AAA", None]
    assert values["count"].dtype == np.dtype(np.float64)
    np.testing.assert_array_equal(
        values["count"], [2.0, np.nan, 1.0, 3.0, 4.0])


def test_no_scores_list_reads_every_score_of_the_resource(
    fragments: FragmentScore,
) -> None:
    with fragments.open() as score:
        batches = list(score.get_fragment_arrays_starting_in_region(
            "1", 1, 40))

    assert [list(values) for _, _, values in batches] == [SCORES]


def test_a_repeated_score_id_gives_one_column(
    fragments: FragmentScore,
) -> None:
    with fragments.open() as score:
        batches = list(score.get_fragment_arrays_overlapping_region(
            "1", 12, 40, scores=["cell", "cell"]))

    assert [list(values) for _, _, values in batches] == [["cell"]]


def test_an_array_read_leaves_the_next_forward_record_read_intact(
    tmp_path: pathlib.Path,
) -> None:
    # A record read leaves its cursor at (33, 34). An array read runs to
    # the end of the file. The next record read continues forward and
    # must still find (60, 70) and (75, 80).
    fragments = FragmentScore(
        a_fragment_score()
        .with_score("cell", "str")
        .with_score("count", "int")
        .with_na_values("NA", score_id="cell")
        .with_data(FRAGMENTS + """
    1      60         70       DDD   5
    1      75         80       EEE   6
""")
        .with_tabix()
        .build_resource(tmp_path))
    with fragments.open() as score:
        expected = list(score.get_fragment_scores_overlapping_region(
            "1", 31, 80, scores=SCORES))
    with fragments.open() as score:
        list(score.get_fragment_scores_overlapping_region(
            "1", 10, 30, scores=SCORES))
        list(score.get_fragment_arrays_overlapping_region(
            "1", 1, 80, scores=SCORES))
        records = list(score.get_fragment_scores_overlapping_region(
            "1", 31, 80, scores=SCORES))

    assert [(beg, end) for beg, end, _ in expected] == [
        (15, 40), (33, 34), (60, 70), (75, 80)]
    assert records == expected
