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


def test_prune_evicts_the_dead_records_a_wide_one_spans() -> None:
    # gain#287.  A record wide enough to span the whole scan stays live for all
    # of it, and pruning that stopped at the first survivor stopped on it --
    # holding behind it every narrow record it spanned, all of them long dead.
    # ``fetch`` then rescanned that pile on every query.
    #
    # Only two records can match ``pos`` at any point here: the spanning one
    # and the point record at ``pos``.  The buffer must stay near that live
    # set rather than growing with the scan.
    buffer = LineBuffer()
    buffer.append(rec("1", 1, 20000))

    max_len = 0
    for pos in range(1, 20001):
        buffer.append(rec("1", pos, pos))
        buffer.prune("1", pos)
        max_len = max(max_len, len(buffer))

    # Bounded by the compaction policy, NOT by the length of the scan -- which
    # is the whole point: before the fix this reached 20001.  The bound is on
    # the buffer's *size*, not its contents: eviction is amortized, so records
    # that died since the last compaction are still held, and ``fetch``
    # filters them out exactly as it always did.
    # The bound is an absolute number on purpose.  Written as
    # ``<= LineBuffer.COMPACT_FLOOR`` it moves with the constant, so raising
    # the floor -- which restores the defect outright, the buffer growing to
    # ~20,000 against a live set of 2 -- would leave this test green.
    # COMPACT_FLOOR is 32 today; 64 leaves room to retune it without hiding a
    # regression three orders of magnitude away.
    assert max_len <= 64
    assert len(buffer) <= 64
    assert len(list(buffer.fetch("1", 20000, 20000))) == 2


def test_prune_rebuilds_the_maxima_exactly_from_the_survivors() -> None:
    # Compaction has to leave both maxima consistent with what is left, or
    # ``contains`` starts admitting positions the buffer cannot answer and
    # ``find_index`` searches a window justified by a record that is gone.
    #
    # For ``_max_width`` to have anything to shrink, the widest record has to
    # be one of the dying ones -- and that puts it at the head: a *surviving*
    # head begins before every other record and reaches past the prune
    # position, so it is always at least as wide as anything the prune drops.
    buffer = LineBuffer()
    buffer.append(rec("1", 1, 499))    # the widest buffered, and dead at 500
    buffer.append(rec("1", 450, 600))  # spans the prune position, pins the head
    for pos in range(451, 500):
        buffer.append(rec("1", pos, pos + 10))  # 451..489 are dead at 500
    assert buffer._max_width == 498  # held by the record about to die

    buffer.prune("1", 500)

    survivors = list(buffer.deque)
    assert survivors
    # Every record that cannot match 500 or later is gone, wherever it sat --
    # before the fix the 39 dead ones behind the spanning record all stayed.
    assert all(record[POS_END] >= 500 for record in survivors)
    assert buffer._max_end == max(r[POS_END] for r in survivors)
    # 498 -> 150: the dead head's width stops widening find_index's scan.
    assert buffer._max_width == max(
        r[POS_END] - r[POS_BEGIN] for r in survivors)


def test_prune_answers_the_same_queries_while_compacting() -> None:
    # Compaction may only drop records that can no longer match -- and between
    # compactions the buffer knowingly holds records that already cannot, which
    # ``fetch`` has to filter out exactly as it always did.  So read every
    # position of the scan back out of a buffer pruned the way the tabix read
    # path prunes it -- append, then prune to the query -- and compare against
    # the same records with nothing ever evicted.
    scan_end = 600
    records = [rec("1", 1, scan_end)]  # spans the whole scan, pins the head

    buffer = LineBuffer()
    buffer.append(records[0])

    max_dead = 0
    max_len = 0
    for pos in range(1, scan_end + 1):
        record = rec("1", pos, pos + (pos % 7))
        records.append(record)
        buffer.append(record)
        buffer.prune("1", pos)

        expected = [r for r in records if r[POS_BEGIN] <= pos <= r[POS_END]]
        assert list(buffer.fetch("1", pos, pos)) == expected, pos

        max_dead = max(
            max_dead, sum(1 for r in buffer.deque if r[POS_END] < pos))
        max_len = max(max_len, len(buffer))

    # The scan really did run in the amortized regime those assertions are
    # there for: dead records were held -- so ``fetch`` had something to filter
    # -- but never more than a compaction's worth.  Before the fix the spanning
    # head pinned every one of them and the buffer grew with the scan.
    assert 0 < max_dead < 64
    assert max_len <= 64


def test_compaction_never_evicts_the_tail() -> None:
    # ``table_tabix`` reads ``peek_last()`` as a stand-in for the file cursor,
    # in ``_gen_from_buffer_and_tabix`` and in
    # ``_should_use_sequential_seek_forward``.  Evicting a dead tail would
    # silently hand both of them an *earlier* record as the cursor, so
    # compaction keeps it however dead it is.
    buffer = LineBuffer()
    buffer.append(rec("1", 1, 10000))  # spans everything; pins the head
    for pos in range(2, 2 + LineBuffer.COMPACT_FLOOR):
        buffer.append(rec("1", pos, pos))
    # The tail begins last but died long ago -- exactly the record at risk.
    tail = buffer.peek_last()
    assert tail[POS_END] < 9000

    buffer.prune("1", 9000)

    assert buffer.peek_last() is tail
    # ...and keeping it may not corrupt the maxima it contributes to.
    survivors = list(buffer.deque)
    assert buffer._max_end == max(r[POS_END] for r in survivors)
    assert buffer._max_width == max(
        r[POS_END] - r[POS_BEGIN] for r in survivors)


def test_clear_resets_the_compaction_baseline() -> None:
    # ``_compact_size`` is the growth rule's baseline.  Carried across a
    # contig change, it would let the new contig's buffer grow to a multiple
    # of the OLD contig's live set before the first walk.
    buffer = LineBuffer()
    for pos in range(1, 200):
        buffer.append(rec("1", pos, pos + 400))
    buffer.prune("1", 300)
    assert buffer._compact_size > 0

    buffer.clear()

    assert buffer._compact_size == 0


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
