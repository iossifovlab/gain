from collections import deque
from collections.abc import Generator
from typing import Any, Protocol

from .record import (
    CHROM,
    POS_BEGIN,
    POS_END,
    Record,
)

Key = str | int


class LineBase(Protocol):
    """Protocol for genomic position table lines."""

    chrom: str
    fchrom: str
    pos_begin: int
    pos_end: int
    ref: str | None
    alt: str | None

    def get(self, key: Key) -> Any:
        ...

    def row(self) -> tuple:
        ...


class Line:
    """Represents a line read from a genomic position table.

    Provides attribute access to a number of important columns - chromosome,
    start position, end position, reference allele and alternative allele.
    """
    __slots__ = (  # noqa: RUF023
        "_data",
        "chrom",
        "fchrom",
        "pos_begin",
        "pos_end",
        "ref",
        "alt",
    )

    def __init__(
        self,
        raw_line: tuple,
        chrom_key: int = 0,
        pos_begin_key: int = 1,
        pos_end_key: int = 2, *,
        ref_key: int | None = None,
        alt_key: int | None = None,
    ):
        self._data: tuple[str, ...] = raw_line

        self.chrom: str = self._data[chrom_key]
        self.fchrom: str = self._data[chrom_key]
        self.pos_begin: int = int(self._data[pos_begin_key])
        self.pos_end: int = int(self._data[pos_end_key])
        self.ref: str | None = \
            self._data[ref_key] if ref_key is not None else None
        self.alt: str | None = \
            self._data[alt_key] if alt_key is not None else None

    def get(self, key: Key) -> str:
        return self._data[key]  # type: ignore

    def row(self) -> tuple:
        return tuple(self._data)


class BigWigLine:
    """Represents a line read from a bigWig file."""

    def __init__(self, raw_line: tuple):
        self._data: tuple[str, int, int, float] = raw_line
        self.chrom: str = self._data[0]
        self.fchrom: str = self._data[0]
        self.pos_begin: int = self._data[1]
        self.pos_end: int = self._data[2]
        self.ref: str | None = None
        self.alt: str | None = None

    def get(self, key: Key) -> str | int | int | float:
        return self._data[key]  # type: ignore

    def row(self) -> tuple:
        return tuple(self._data)


class LineBuffer:
    """Buffer of records read from a Tabix genome position table.

    Holds **records** -- the six-slot tuples the tabular parser builds -- and
    reads them by slot constant (``record[CHROM]``, ``record[POS_BEGIN]``,
    ``record[POS_END]``), never by attribute.  The slots this buffer indexes on
    are immutable -- a record's tuple cells cannot be rebound -- so a buffered
    record can be handed out and retained here at the same time without any
    risk that a later read moves it out from under the positional logic below
    (which the ``Line`` adapter it replaces did allow: the
    zero-based/chrom-mapping transforms rewrote the object in place).

    That promise covers the slots, **not the payload**: the buffer holds the
    same record object the caller got, and a tabix payload is a
    ``pysam.TupleProxy``, which defines ``__setitem__`` -- a caller that writes
    ``record[PAYLOAD][i] = ...`` mutates the row this buffer is holding.  The
    payload is shared by reference on purpose (that is what keeps it lazy); see
    ``record.py``.  Nothing here reads it, so the buffer's own behaviour is
    unaffected either way.

    The semantics are exactly those of the adapter-era buffer: it clears on a
    chromosome change, clears when it observes a non-monotonic ordering
    (:meth:`region`), prunes from the left, and locates a position by binary
    search with a linear back-scan over the equal/overlapping intervals that
    precede the hit.

    The VCF backend feeds this buffer too, with records of its own -- whose
    PAYLOAD is a ``(variant record, allele index)`` pair rather than a raw row.
    Nothing here reads the payload, so the buffer needs to know nothing about
    which backend built the record it is holding: it windows every record by the
    three slots above, and those mean the same thing in all of them.
    """

    def __init__(self) -> None:
        self.deque: deque[Record] = deque()

    def __len__(self) -> int:
        return len(self.deque)

    def clear(self) -> None:
        self.deque.clear()

    def append(self, record: Record) -> None:
        if len(self.deque) > 0 \
                and self.peek_first()[CHROM] != record[CHROM]:
            self.clear()
        self.deque.append(record)

    def peek_first(self) -> Record:
        return self.deque[0]

    def pop_first(self) -> Record:
        return self.deque.popleft()

    def peek_last(self) -> Record:
        return self.deque[-1]

    def region(self) -> tuple[str | None, int | None, int | None]:
        """Return region stored in the buffer."""
        if len(self.deque) == 0:
            return None, None, None

        first = self.peek_first()
        last = self.peek_last()

        if first[CHROM] != last[CHROM] \
                or first[POS_END] > last[POS_END]:
            self.clear()
            return None, None, None

        return first[CHROM], first[POS_BEGIN], last[POS_END]

    def prune(self, chrom: str, pos: int) -> None:
        """Prune the buffer if needed."""
        if len(self.deque) == 0:
            return

        first = self.peek_first()

        if chrom != first[CHROM]:
            self.clear()
            return

        while len(self.deque) > 0:
            first = self.peek_first()
            if pos <= first[POS_END]:
                break
            self.deque.popleft()

    def contains(self, chrom: str, pos: int) -> bool:
        bchrom, bbeg, bend = self.region()
        if bchrom is None or bbeg is None or bend is None:
            return False
        return chrom == bchrom and bend >= pos >= bbeg

    def find_index(self, chrom: str, pos: int) -> int:
        """Find index in line buffer that contains the passed position."""
        if len(self.deque) == 0 or not self.contains(chrom, pos):
            return -1

        if len(self.deque) == 1:
            return 0

        first_index = 0
        last_index = len(self.deque) - 1
        while True:
            mid_index = (last_index - first_index) // 2 + first_index
            if last_index <= first_index:
                break

            mid = self.deque[mid_index]
            if mid[POS_END] >= pos >= mid[POS_BEGIN]:
                break

            if pos < mid[POS_BEGIN]:
                last_index = mid_index - 1
            else:
                first_index = mid_index + 1

        while mid_index > 0:
            prev = self.deque[mid_index - 1]
            if pos > prev[POS_END]:
                break
            mid_index -= 1

        for index in range(mid_index, len(self.deque)):
            record = self.deque[index]
            if record[POS_END] >= pos >= record[POS_BEGIN]:
                mid_index = index
                break
            if record[POS_BEGIN] >= pos:
                mid_index = index
                break

        return mid_index

    def fetch(
        self, chrom: str, pos_begin: int, pos_end: int,
    ) -> Generator[Record, None, None]:
        """Return a generator of records matching the region.

        ``pos_end`` is never ``None`` here: the buffer is only consulted when
        the caller asked for a bounded region.  ``get_records_in_region``
        turns buffering *off* when ``pos_end is None``, so an unbounded query
        never reaches the buffer at all.
        """
        beg_index = self.find_index(chrom, pos_begin)
        if beg_index == -1:
            return

        for index in range(beg_index, len(self.deque)):
            record = self.deque[index]
            if record[POS_END] < pos_begin:
                continue
            if record[POS_BEGIN] > pos_end:
                break
            yield record
