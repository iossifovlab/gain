"""The tabix backend must answer overlapping-interval tables like the oracle.

Every test here pins ONE property: for any region query,
:class:`TabixGenomicPositionTable` yields exactly what
:class:`InmemoryGenomicPositionTable` yields over identical rows.  The
in-memory backend is the oracle -- its predicate *is* the documented contract
of ``get_records_in_region`` ("yield the records overlapping the region"),
written out in three lines with no buffer to get wrong.

Why a whole module for it (gain#250).  The tabix backend serves a query from a
warm :class:`LineBuffer` whenever it can, so its answer depends on *which
queries ran before it*.  Every buffered read path used to assume that a table's
``pos_end`` is non-decreasing -- true for point records and for
strictly-disjoint intervals, and false the moment two intervals overlap.  On
overlapping data the backend then both dropped records that overlap the region
and yielded records that do not, silently, with no exception and no log line.

The pre-existing suite compared the two backends only on POINT data, which is
exactly the shape that is immune -- which is why this survived.  So the tests
below are built around the conditions that expose it:

* a **stateful monotonic scan** -- consecutive 1-bp queries with no buffer
  reset, which is what annotating a sorted VCF/TSV does, and the worst case
  (the buffer is warm for every query but the first);
* **overlapping intervals**, including nested ones and duplicate loci;
* and the point/disjoint shapes alongside them, to pin that the shapes that
  always worked still do.
"""
# pylint: disable=W0621,C0116
# ruff: noqa: S311
# S311 (no `random` for cryptography) does not apply: `random` builds fixture
# rows here, seeded so a failure replays exactly.
import pathlib
import random
import textwrap
from typing import Any

import pytest
from gain.genomic_resources.genomic_position_table import (
    build_genomic_position_table,
)
from gain.genomic_resources.genomic_position_table.record import (
    PAYLOAD,
    Record,
)
from gain.genomic_resources.genomic_position_table.table import (
    GenomicPositionTable,
)
from gain.genomic_resources.testing import (
    build_filesystem_test_resource,
    convert_to_tab_separated,
    setup_directories,
    setup_tabix,
)

CHROM = "1"

Interval = tuple[int, int]
TablePair = tuple[GenomicPositionTable, GenomicPositionTable]


def _content(rows: list[Interval], *, comment_header: bool) -> str:
    """Render the rows as a table, one distinct ``c2`` value per row.

    ``c2`` is a per-row serial number, so two rows sharing a locus stay
    distinguishable -- a comparison keyed only on the interval could not tell a
    dropped duplicate from a kept one.
    """
    hash_ = "#" if comment_header else ""
    lines = [f"{hash_}chrom pos_begin pos_end c2"]
    lines.extend(
        f"{CHROM} {beg} {end} {serial}"
        for serial, (beg, end) in enumerate(rows)
    )
    return "\n".join(lines)


@pytest.fixture
def build_tables(tmp_path: pathlib.Path) -> Any:
    """Return a builder of a (tabix, in-memory) pair over identical rows."""
    def builder(rows: list[Interval]) -> TablePair:
        setup_directories(tmp_path / "tabix", {
            "genomic_resource.yaml": textwrap.dedent("""
                table:
                    format: tabix
                    filename: data.txt.gz
                scores:
                - id: c2
                  name: c2
                  type: int"""),
        })
        setup_tabix(
            tmp_path / "tabix" / "data.txt.gz",
            _content(rows, comment_header=True),
            seq_col=0, start_col=1, end_col=2)

        setup_directories(tmp_path / "mem", {
            "genomic_resource.yaml": textwrap.dedent("""
                table:
                    format: tsv
                    filename: data.txt
                scores:
                - id: c2
                  name: c2
                  type: int"""),
            "data.txt": convert_to_tab_separated(
                _content(rows, comment_header=False)),
        })

        tabix_res = build_filesystem_test_resource(tmp_path / "tabix")
        mem_res = build_filesystem_test_resource(tmp_path / "mem")
        assert tabix_res.config is not None
        assert mem_res.config is not None

        tabix_table = build_genomic_position_table(
            tabix_res, tabix_res.config["table"])
        mem_table = build_genomic_position_table(
            mem_res, mem_res.config["table"])
        tabix_table.open()
        mem_table.open()
        return tabix_table, mem_table
    return builder


def _answer(records: Any) -> list[tuple[str, ...]]:
    """Project records onto their raw rows, ordered.

    The full row, not just the interval: the ``c2`` serial identifies *which*
    row was yielded, so a dropped or duplicated record at a shared locus shows
    up.  ``tuple()`` normalises the two backends' payloads -- a
    ``pysam.TupleProxy`` for tabix, a ``tuple[str, ...]`` in memory -- onto the
    same comparable value.
    """
    return sorted(tuple(record[PAYLOAD]) for record in records)


def _assert_same(
    tabix_table: GenomicPositionTable,
    mem_table: GenomicPositionTable,
    beg: int, end: int,
) -> None:
    tabix_answer = _answer(tabix_table.get_records_in_region(CHROM, beg, end))
    mem_answer = _answer(mem_table.get_records_in_region(CHROM, beg, end))
    assert tabix_answer == mem_answer, \
        f"region ({beg}, {end}): tabix != in-memory"


# ---------------------------------------------------------------------------
# The two minimal reproducers from gain#250.
# ---------------------------------------------------------------------------

def test_warm_buffer_drops_a_record_that_overlaps(
    build_tables: Any,
) -> None:
    """Reproducer A: two identical intervals, the second one went missing.

    The warm-up leaves ``(6, 7)`` buffered but unyielded.  The query at 6 is
    then served from the buffer, and the continuation from the file used to be
    skipped because the buffered record *ends* past the query -- so the second
    ``(6, 7)``, still unread, was never reached.
    """
    tabix_table, mem_table = build_tables([(6, 7), (6, 7)])

    assert _answer(tabix_table.get_records_in_region(CHROM, 5, 5)) == []

    assert _answer(tabix_table.get_records_in_region(CHROM, 6, 6)) == [
        (CHROM, "6", "7", "0"),
        (CHROM, "6", "7", "1"),
    ]
    _assert_same(tabix_table, mem_table, 6, 6)


def test_warm_buffer_yields_a_record_that_does_not_overlap(
    build_tables: Any,
) -> None:
    """Reproducer B: a record lying entirely before the query was yielded.

    ``(15, 15)`` sits inside ``(14, 23)``.  The query at 23 continues reading
    from the file past the buffered ``(14, 23)`` and used to yield everything
    it read up to the query's end -- with no lower bound, ``(15, 15)`` came
    with it.
    """
    tabix_table, mem_table = build_tables([(14, 23), (15, 15)])

    assert _answer(tabix_table.get_records_in_region(CHROM, 13, 13)) == []

    assert _answer(tabix_table.get_records_in_region(CHROM, 23, 23)) == [
        (CHROM, "14", "23", "0"),
    ]
    _assert_same(tabix_table, mem_table, 23, 23)


# ---------------------------------------------------------------------------
# Backend equality over the data-shape matrix.
# ---------------------------------------------------------------------------

SHAPES = [
    "points_unique",
    "points_dup",
    "intervals_disjoint",
    "intervals_overlapping",
    "intervals_overlapping_dup",
    "intervals_nested",
]


def make_rows(shape: str, count: int = 400, seed: int = 3) -> list[Interval]:
    """Build ``count`` sorted rows of the named shape.

    The shapes bracket the defect: the first three are the ones whose
    ``pos_end`` happens to be non-decreasing (and which therefore always
    worked), the last three are not.  ``intervals_nested`` is the extreme --
    a wide interval swallowing the ones that follow it.
    """
    rnd = random.Random(seed)
    rows: list[Interval] = []
    pos = 10
    for _ in range(count):
        if shape == "points_unique":
            rows.append((pos, pos))
            pos += rnd.randint(1, 6)
        elif shape == "points_dup":
            rows.extend((pos, pos) for _ in range(rnd.randint(1, 3)))
            pos += rnd.randint(1, 6)
        elif shape == "intervals_disjoint":
            end = pos + rnd.randint(1, 10)
            rows.append((pos, end))
            pos = end + rnd.randint(1, 4)
        elif shape == "intervals_overlapping":
            rows.append((pos, pos + rnd.randint(2, 25)))
            pos += rnd.randint(1, 8)
        elif shape == "intervals_overlapping_dup":
            end = pos + rnd.randint(2, 25)
            rows.extend((pos, end) for _ in range(rnd.randint(1, 2)))
            pos += rnd.randint(1, 8)
        elif shape == "intervals_nested":
            rows.append((pos, pos + rnd.randint(40, 60)))
            for _ in range(rnd.randint(1, 3)):
                inner = pos + rnd.randint(0, 20)
                rows.append((inner, inner + rnd.randint(0, 3)))
            pos += rnd.randint(1, 10)
        else:
            raise ValueError(f"unknown shape {shape}")
    rows.sort()
    return rows


@pytest.mark.parametrize("shape", SHAPES)
def test_monotonic_scan_matches_inmemory(
    build_tables: Any, shape: str,
) -> None:
    """A stateful forward 1-bp scan -- the case that would have caught this.

    No buffer reset between queries: every query but the first is answered
    against a buffer warmed by its predecessor, which is precisely the state
    the defect lived in.  This is what annotating a sorted file does.
    """
    rows = make_rows(shape)
    tabix_table, mem_table = build_tables(rows)

    lo = min(beg for beg, _ in rows)
    hi = max(end for _, end in rows)
    for pos in range(lo - 2, hi + 3):
        _assert_same(tabix_table, mem_table, pos, pos)


@pytest.mark.parametrize("shape", SHAPES)
def test_random_regions_match_inmemory(build_tables: Any, shape: str) -> None:
    """Random starts and random widths, still against a warm buffer.

    A monotonic scan only ever moves the query forward by one; this walks the
    query start backwards and forwards and varies its width, which exercises
    the buffer-miss paths (the fresh fetch and the sequential seek) as well as
    the buffer hit.
    """
    rows = make_rows(shape)
    tabix_table, mem_table = build_tables(rows)

    lo = min(beg for beg, _ in rows)
    hi = max(end for _, end in rows)
    rnd = random.Random(17)
    for _ in range(400):
        beg = rnd.randint(lo - 5, hi + 5)
        end = beg + rnd.choice([0, 0, 1, 5, 40, 500])
        _assert_same(tabix_table, mem_table, beg, end)


@pytest.mark.parametrize("shape", SHAPES)
def test_cold_buffer_scan_matches_inmemory(
    build_tables: Any, shape: str,
) -> None:
    """The same equality with the buffer reset before every query.

    The two backends already agreed here before gain#250 was fixed -- the
    defect was purely one of warm-buffer state.  That is exactly what makes
    this worth pinning: it is the half that must not break while the warm half
    is being repaired.
    """
    rows = make_rows(shape)
    tabix_table, mem_table = build_tables(rows)

    lo = min(beg for beg, _ in rows)
    hi = max(end for _, end in rows)
    rnd = random.Random(23)
    for _ in range(300):
        beg = rnd.randint(lo - 5, hi + 5)
        end = beg + rnd.choice([0, 1, 7, 60])
        tabix_table.buffer.clear()
        _assert_same(tabix_table, mem_table, beg, end)


def test_monotonic_scan_keeps_the_buffer_warm(build_tables: Any) -> None:
    """Pin the mechanism the tests above depend on.

    If a monotonic scan stopped hitting the buffer -- e.g. if the defect were
    "fixed" by clearing it, or by turning buffering off -- every equality test
    in this module would still pass while the read path's whole reason for
    existing was gone.  So: a 1-bp scan over an overlapping table must serve
    the great majority of its queries from the buffer, and must not re-seek the
    file once per query.
    """
    rows = make_rows("intervals_overlapping", count=100)
    tabix_table, _ = build_tables(rows)

    lo = min(beg for beg, _ in rows)
    hi = max(end for _, end in rows)
    positions = range(lo, hi + 1)
    for pos in positions:
        list(tabix_table.get_records_in_region(CHROM, pos, pos))

    stats = tabix_table.stats
    assert stats["calls"] == len(positions)
    # One fetch to prime the buffer; a scan that never leaves the contig has
    # no reason to seek again.
    assert stats["tabix fetch"] == 1
    assert stats["yield from buffer"] > 0


# ---------------------------------------------------------------------------
# Point tables are immune and must stay untouched.
# ---------------------------------------------------------------------------

def test_point_table_without_a_pos_end_column(tmp_path: pathlib.Path) -> None:
    """A table whose ``pos_end`` resolves to the ``pos_begin`` column.

    The narrowest statement that the point path is unchanged: there is no
    ``pos_end`` column at all, so every record is a single base and the
    non-monotonicity this module is about cannot arise.
    """
    setup_directories(tmp_path, {
        "genomic_resource.yaml": textwrap.dedent("""
            table:
                format: tabix
                filename: data.txt.gz
            scores:
            - id: c2
              name: c2
              type: float"""),
    })
    setup_tabix(
        tmp_path / "data.txt.gz",
        """
        #chrom pos_begin c2
        1      10        3.14
        1      10        4.14
        1      11        5.14
        1      14        6.14
        """, seq_col=0, start_col=1, end_col=1)
    res = build_filesystem_test_resource(tmp_path)
    assert res.config is not None

    with build_genomic_position_table(res, res.config["table"]) as table:
        def rows(beg: int, end: int) -> list[tuple[str, ...]]:
            return _answer(table.get_records_in_region(CHROM, beg, end))

        # A stateful forward scan, each query against the previous one's buffer.
        assert rows(9, 9) == []
        assert rows(10, 10) == [("1", "10", "3.14"), ("1", "10", "4.14")]
        assert rows(11, 11) == [("1", "11", "5.14")]
        assert rows(12, 13) == []
        assert rows(14, 14) == [("1", "14", "6.14")]


def test_disjoint_intervals_are_unchanged(build_tables: Any) -> None:
    """Strictly non-overlapping intervals, over a stateful scan."""
    tabix_table, _ = build_tables([(10, 12), (15, 20), (21, 30)])

    def rows(beg: int, end: int) -> list[tuple[str, ...]]:
        return _answer(tabix_table.get_records_in_region(CHROM, beg, end))

    assert rows(9, 9) == []
    assert rows(10, 10) == [("1", "10", "12", "0")]
    assert rows(12, 12) == [("1", "10", "12", "0")]
    assert rows(13, 14) == []
    assert rows(15, 15) == [("1", "15", "20", "1")]
    assert rows(18, 21) == [("1", "15", "20", "1"), ("1", "21", "30", "2")]
    assert rows(31, 31) == []


# ---------------------------------------------------------------------------
# The buffer's own invariants under a non-monotonic pos_end.
# ---------------------------------------------------------------------------

def rec(
    chrom: str, pos_begin: int, pos_end: int, payload: Any = None,
) -> Record:
    return (chrom, pos_begin, pos_end, None, None, payload)


def test_buffer_keeps_a_non_monotonic_pos_end() -> None:
    """A nested interval is normal data, not a scrambled buffer.

    ``region()`` used to read ``first[POS_END] > last[POS_END]`` as proof that
    the records had stopped running forward, and threw the buffer away.  For
    nested intervals that ordering is expected: the buffer is ordered by
    ``pos_begin``, and a wide first record legitimately outlives a narrow last
    one.
    """
    from gain.genomic_resources.genomic_position_table import LineBuffer

    buffer = LineBuffer()
    buffer.append(rec("1", 10, 100))
    buffer.append(rec("1", 20, 30))

    # Ordered by pos_begin, so the right edge is the widest end, not the last.
    assert buffer.region() == ("1", 10, 100)
    assert len(buffer) == 2
    assert buffer.contains("1", 90)


def test_buffer_finds_a_record_hidden_behind_a_shorter_one() -> None:
    """``fetch`` must reach a record that a nearer, shorter one masks.

    ``(10, 100)`` contains position 90; ``(20, 30)`` does not and sits between
    it and the binary search's landing point.  The back-scan used to stop at
    the first predecessor that fails to reach the position, so the record that
    *does* reach it was never found.
    """
    from gain.genomic_resources.genomic_position_table import LineBuffer

    buffer = LineBuffer()
    buffer.append(rec("1", 10, 100, "wide"))
    buffer.append(rec("1", 20, 30, "narrow"))
    buffer.append(rec("1", 95, 95, "point"))

    assert [r[PAYLOAD] for r in buffer.fetch("1", 90, 90)] == ["wide"]
    assert [r[PAYLOAD] for r in buffer.fetch("1", 25, 25)] == ["wide", "narrow"]
    assert [r[PAYLOAD] for r in buffer.fetch("1", 95, 95)] == ["wide", "point"]


def test_backward_query_does_not_trust_a_pruned_buffer(
    build_tables: Any,
) -> None:
    """A query that moves backwards must not be served from a stale buffer.

    ``(10, 20)`` and ``(15, 30)`` overlap.  The query at 25 leaves the buffer
    holding ``(15, 30)`` and ``(40, 50)`` -- ``(10, 20)`` is absent, rightly:
    it cannot match 25 or any later position.  But its absence drags nothing
    with it, and ``(15, 30)`` keeps the buffer's left edge at 15, so the
    following query at 18 looks like it falls inside the buffered window while
    the record it needs is not there.

    The buffer is only ever complete from the *previous query's* start onwards,
    which is the position it was pruned (or freshly fetched) to; its left edge
    does not report that, because eviction goes by ``pos_end``.  With point or
    disjoint records the two coincide and the edge is a sound guard -- overlap
    is what pulls them apart.
    """
    tabix_table, mem_table = build_tables([(10, 20), (15, 30), (40, 50)])

    assert _answer(tabix_table.get_records_in_region(CHROM, 25, 25)) == [
        (CHROM, "15", "30", "1"),
    ]
    # Backwards -- (10, 20) was never buffered and must be re-read.
    assert _answer(tabix_table.get_records_in_region(CHROM, 18, 18)) == [
        (CHROM, "10", "20", "0"),
        (CHROM, "15", "30", "1"),
    ]
    _assert_same(tabix_table, mem_table, 18, 18)


def test_buffer_still_clears_on_a_non_monotonic_pos_begin() -> None:
    """The ordering the buffer does rely on is ``pos_begin``'s."""
    from gain.genomic_resources.genomic_position_table import LineBuffer

    buffer = LineBuffer()
    buffer.append(rec("1", 100, 200))
    buffer.append(rec("1", 1, 10))

    assert buffer.region() == (None, None, None)
    assert len(buffer) == 0
