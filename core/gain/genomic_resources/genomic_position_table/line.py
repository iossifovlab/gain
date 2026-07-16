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

    The semantics are those of the adapter-era buffer: it clears on a
    chromosome change, clears when it observes a non-monotonic ordering
    (:meth:`region`), prunes from the left, and locates a position by binary
    search with a linear scan over the overlapping intervals around the hit.

    **The buffer is ordered by ``pos_begin`` -- and by nothing else.**  That is
    the file's order, and it is the only ordering this class may assume.  Every
    method here used to lean on a second, unstated assumption -- that
    ``pos_end`` is non-decreasing too -- which holds for point records and for
    strictly-disjoint intervals and fails the moment two intervals overlap: a
    record can then *contain* the ones that follow it.  Reading a warm buffer
    through that assumption both hid records that overlap the query and
    surfaced records that do not (gain#250).  So:

    * the right edge of :meth:`region` is the **maximum** ``pos_end`` in the
      buffer (:attr:`_max_end`), not the last record's -- the last record is
      merely the one that begins last;
    * :meth:`region` judges the ordering by ``pos_begin``, since a ``pos_end``
      that runs backwards is ordinary nested data rather than corruption;
    * :meth:`find_index` bounds its scan by the widest interval buffered
      (:attr:`_max_width`) instead of stopping at the first record that fails
      to reach the position.

    The two maxima are the only state beyond the deque, and both may only ever
    *over*-estimate: an over-wide window costs a few extra comparisons in
    :meth:`fetch`, which filters exactly, whereas an under-estimate would drop
    records.  See :meth:`prune` for why ``_max_end`` nevertheless stays exact
    where it matters.

    The VCF backend feeds this buffer too, with records of its own -- whose
    PAYLOAD is a ``(variant record, allele index)`` pair rather than a raw row.
    Nothing here reads the payload, so the buffer needs to know nothing about
    which backend built the record it is holding: it windows every record by the
    three slots above, and those mean the same thing in all of them.
    """

    def __init__(self) -> None:
        self.deque: deque[Record] = deque()
        # Maximum pos_end, and maximum interval width, over the buffered
        # records.  Both are meaningless while the deque is empty and are reset
        # with it; see the class docstring for what they are for.
        self._max_end: int = 0
        self._max_width: int = 0

    def __len__(self) -> int:
        return len(self.deque)

    def clear(self) -> None:
        self.deque.clear()
        self._max_end = 0
        self._max_width = 0

    def append(self, record: Record) -> None:
        """Buffer a record read from the file, maintaining the maxima.

        A record from another contig empties the buffer first: nothing that
        precedes it can answer a query on the new contig.
        """
        if len(self.deque) > 0 \
                and self.peek_first()[CHROM] != record[CHROM]:
            self.clear()
        self.deque.append(record)
        # A record slot is statically opaque (a record is tuple[Any, ...]);
        # annotate the reads so the arithmetic stays typed.
        pos_begin: int = record[POS_BEGIN]
        pos_end: int = record[POS_END]
        self._max_end = max(self._max_end, pos_end)
        self._max_width = max(self._max_width, pos_end - pos_begin)

    def peek_first(self) -> Record:
        return self.deque[0]

    def pop_first(self) -> Record:
        return self.deque.popleft()

    def peek_last(self) -> Record:
        return self.deque[-1]

    def region(self) -> tuple[str | None, int | None, int | None]:
        """Return the region the buffered records span.

        The right edge is the widest ``pos_end`` buffered, not the last
        record's: the records are ordered by ``pos_begin``, so the last one to
        *begin* need not be the last one to *end* (see the class docstring).

        Ordering is judged by ``pos_begin`` for the same reason.  A first record
        that ends after the last one is not evidence of anything -- that is what
        a nested interval looks like -- but a first record that *begins* after
        the last one contradicts the one order the buffer is built on, so the
        buffer discards itself rather than answer from a scrambled window.
        """
        if len(self.deque) == 0:
            return None, None, None

        first = self.peek_first()
        last = self.peek_last()

        if first[CHROM] != last[CHROM] \
                or first[POS_BEGIN] > last[POS_BEGIN]:
            self.clear()
            return None, None, None

        return first[CHROM], first[POS_BEGIN], self._max_end

    def prune(self, chrom: str, pos: int) -> None:
        """Drop the leading records that can no longer match ``pos`` or later.

        ``_max_end`` survives this exactly, and not by luck.  Pruning stops at
        the first record whose ``pos_end`` reaches ``pos``, so every record it
        drops ends *before* ``pos`` while that survivor ends at or after it --
        a dropped record can therefore never have held the maximum unless there
        are no survivors at all, and that case empties the deque and resets the
        maxima with it.  (``_max_width`` is not exact under pruning and is not
        required to be: it may only over-estimate.)

        Pruning stops at the first survivor, not the last -- so a wide record
        pins the head and holds behind it the narrow ones it spans, which
        :meth:`fetch` then rescans on every query.  The buffer therefore grows
        with the widest interval over a dense region, rather than staying at
        the query's own width.
        """
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

        if len(self.deque) == 0:
            self.clear()

    def contains(self, chrom: str, pos: int) -> bool:
        bchrom, bbeg, bend = self.region()
        if bchrom is None or bbeg is None or bend is None:
            return False
        return chrom == bchrom and bend >= pos >= bbeg

    def find_index(self, chrom: str, pos: int) -> int:
        """Find the first index in the buffer relevant to ``pos``.

        Returns the leftmost record that overlaps ``pos`` or, when none does,
        the leftmost that begins at or after it -- and ``-1`` when the buffer
        does not span ``pos`` at all.  :meth:`fetch` scans forward from here and
        filters exactly, so the one thing this must never do is land to the
        *right* of a record that overlaps.

        Which is what it used to do.  The old back-scan walked left while the
        predecessor reached ``pos`` and stopped at the first one that did not --
        sound only if a record that fails to reach ``pos`` proves that
        everything before it fails too, i.e. only if ``pos_end`` is
        non-decreasing.  With overlapping intervals a record containing ``pos``
        can sit to the left of one that does not, behind that stop.

        The bound that does hold: the buffer is sorted by ``pos_begin``, and a
        record overlapping ``pos`` spans it, so it cannot begin before
        ``pos - _max_width``.  Binary-searching for that lower bound puts the
        scan at or left of every record that can overlap, whatever the ends do.
        The window is as tight as the buffered data's widest interval; a stale
        ``_max_width`` only widens it, and :meth:`fetch` filters regardless.
        """
        if len(self.deque) == 0 or not self.contains(chrom, pos):
            return -1

        if len(self.deque) == 1:
            return 0

        # Leftmost index whose pos_begin reaches the lower bound.  Binary
        # search on pos_begin -- the key the deque is actually ordered by.
        lower_bound = pos - self._max_width
        first_index = 0
        last_index = len(self.deque)
        while first_index < last_index:
            mid_index = (first_index + last_index) // 2
            if self.deque[mid_index][POS_BEGIN] < lower_bound:
                first_index = mid_index + 1
            else:
                last_index = mid_index

        for index in range(first_index, len(self.deque)):
            record = self.deque[index]
            if record[POS_END] >= pos >= record[POS_BEGIN]:
                return index
            if record[POS_BEGIN] >= pos:
                return index

        # Unreachable while ``_max_end`` is exact: ``contains`` has already
        # established that some record reaches ``pos``.  Should it ever
        # over-estimate, "no record overlaps and none begins after" is the
        # honest answer, and ``fetch`` yields nothing on -1.
        return -1

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
