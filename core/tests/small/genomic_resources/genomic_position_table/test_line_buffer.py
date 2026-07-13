# pylint: disable=W0621,C0114,C0116,W0212,W0613
# pylint: disable=no-member
from typing import Any

import pytest
from gain.genomic_resources.genomic_position_table import LineBuffer
from gain.genomic_resources.genomic_position_table.record import (
    CHROM,
    PAYLOAD,
    POS_BEGIN,
    POS_END,
    Record,
)


def rec(
    chrom: str, pos_begin: int, pos_end: int, payload: Any = None,
) -> Record:
    """Build a record the way a tabular parser would.

    The buffer only ever reads the three positional slots; ``ref``/``alt`` are
    irrelevant to it, and the payload stays opaque (here a raw row stand-in).
    """
    return (chrom, pos_begin, pos_end, None, None, payload)


def test_line_buffer_simple() -> None:
    buffer = LineBuffer()
    buffer.append(rec("1", 1435348, 1435664))
    buffer.append(rec("1", 1435665, 1435739))

    assert buffer.region() == ("1", 1435348, 1435739)

    for row in buffer.fetch("1", 1435400, 1435400):
        print(row)
        assert row[CHROM] == "1"
        assert row[POS_BEGIN] == 1435348
        assert row[POS_END] == 1435664


def test_line_buffer_simple_2() -> None:
    buffer = LineBuffer()
    buffer.append(rec("1", 4, 4))
    buffer.append(rec("1", 4, 4))
    buffer.append(rec("1", 5, 5))
    buffer.append(rec("1", 8, 8))

    res = list(buffer.fetch("1", 4, 8))
    assert len(res) == 4


@pytest.mark.parametrize("pos,expected", [
    (1, 5),
    (2, 5),
    (3, 4),
    (4, 4),
    (5, 2),
])
def test_line_buffer_prune(pos: int, expected: int) -> None:
    buffer = LineBuffer()

    buffer.append(rec("1", 2, 2))
    buffer.append(rec("1", 4, 4))
    buffer.append(rec("1", 4, 4))
    buffer.append(rec("1", 5, 5))
    buffer.append(rec("1", 8, 8))

    buffer.prune("1", pos)
    assert len(buffer) == expected


@pytest.mark.parametrize("pos,expected", [
    (1, -1),
    (4, 0),
    (5, 2),
    (8, 3),
    (9, 4),
    (10, 4),
    (11, 5),
])
def test_line_buffer_find_index(pos: int, expected: int) -> None:
    buffer = LineBuffer()
    buffer.append(rec("1", 4, 4))  # 0
    buffer.append(rec("1", 4, 4))  # 1
    buffer.append(rec("1", 5, 5))  # 2
    buffer.append(rec("1", 8, 8))  # 3
    buffer.append(rec("1", 9, 10))  # 4
    buffer.append(rec("1", 12, 14))  # 5

    assert buffer.find_index("1", pos) == expected


def test_line_buffer_simple_3() -> None:
    buffer = LineBuffer()
    buffer.append(rec("1", 1, 10))
    buffer.append(rec("1", 11, 20))
    buffer.append(rec("1", 21, 30))
    buffer.append(rec("1", 31, 40))
    buffer.append(rec("1", 41, 50))
    buffer.append(rec("1", 61, 70))

    assert buffer.contains("1", 1)

    res = list(buffer.fetch("1", 1, 1))
    assert len(res) == 1
    assert res[0][CHROM] == "1"
    assert res[0][POS_BEGIN] == 1
    assert res[0][POS_END] == 10


def test_line_buffer_clears_on_chromosome_change() -> None:
    # A record from another contig empties the buffer: what precedes it can
    # never be an answer to a query on the new contig.
    buffer = LineBuffer()
    buffer.append(rec("1", 1, 10))
    buffer.append(rec("1", 11, 20))
    assert len(buffer) == 2

    buffer.append(rec("2", 1, 10))

    assert len(buffer) == 1
    assert buffer.peek_first()[CHROM] == "2"
    assert buffer.region() == ("2", 1, 10)


def test_line_buffer_clears_on_non_monotonic_order() -> None:
    # The buffer is only meaningful while the records it holds run forward.  A
    # first record that ends after the last one is proof they do not, and the
    # buffer discards itself rather than answer from a scrambled window.
    buffer = LineBuffer()
    buffer.append(rec("1", 100, 200))
    buffer.append(rec("1", 1, 10))
    assert len(buffer) == 2

    assert buffer.region() == (None, None, None)
    assert len(buffer) == 0
    assert not buffer.contains("1", 100)


def test_line_buffer_records_are_not_mutated_by_the_buffer() -> None:
    # A record handed to the buffer is the *same* object the reader yielded;
    # it is a tuple, so nothing downstream can rewrite it while it is buffered.
    record = rec("1", 1, 10, ("1", "1", "10", "0.5"))
    buffer = LineBuffer()
    buffer.append(record)

    fetched = next(iter(buffer.fetch("1", 1, 1)))
    assert fetched is record
    with pytest.raises(TypeError):
        fetched[POS_BEGIN] = 42  # type: ignore[index]


@pytest.mark.parametrize("pos,index", [
    (1847882, 6),
    (1847880, 0),
    (1847881, 3),
    (1847883, 6),
    (1847884, 8),
    (1847885, 11),
])
def test_find_index_buggy(pos: int, index: int) -> None:
    buffer = LineBuffer()
    buffer.append(rec("1", 1847880, 1847880))  # 0
    buffer.append(rec("1", 1847880, 1847880))  # 1
    buffer.append(rec("1", 1847880, 1847880))  # 2
    buffer.append(rec("1", 1847881, 1847881))  # 3
    buffer.append(rec("1", 1847881, 1847881))  # 4
    buffer.append(rec("1", 1847881, 1847881))  # 5
    buffer.append(rec("1", 1847883, 1847883))  # 6
    buffer.append(rec("1", 1847883, 1847883))  # 7
    buffer.append(rec("1", 1847884, 1847884))  # 8
    buffer.append(rec("1", 1847884, 1847884))  # 9
    buffer.append(rec("1", 1847884, 1847884))  # 10
    buffer.append(rec("1", 1847885, 1847885))  # 11

    assert buffer.find_index("1", pos) == index


def test_find_index_buggy_2() -> None:
    buffer = LineBuffer()
    buffer.append(rec("1", 503142, 503143))
    buffer.append(rec("1", 503144, 503144))
    buffer.append(rec("1", 503145, 503145))
    buffer.append(rec("1", 503146, 503146))
    buffer.append(rec("1", 503147, 503148, "found"))
    buffer.append(rec("1", 503149, 503158))
    buffer.append(rec("1", 503159, 503159))

    index = buffer.find_index("1", 503148)
    assert index != -1

    print(index, buffer.deque[index])

    records = list(buffer.fetch("1", 503148, 503148))
    assert len(records) == 1
    record = records[0]
    assert record[CHROM] == "1"
    assert record[POS_BEGIN] == 503147
    assert record[POS_END] == 503148
    assert record[PAYLOAD] == "found"
