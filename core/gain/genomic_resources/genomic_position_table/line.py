from collections import deque
from collections.abc import Generator

from .record import (
    CHROM,
    POS_BEGIN,
    POS_END,
    Record,
)


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
    (:meth:`region`), evicts records it can no longer be asked about
    (:meth:`prune`), and locates a position by binary search with a linear scan
    over the overlapping intervals around the hit.

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

    Neither maximum may ever *under*-estimate -- that would drop records.
    Over-estimating is harmless in both, but by different routes, which is why
    they are worth keeping apart: an over-wide ``_max_width`` only widens
    :meth:`find_index`'s scan, and :meth:`fetch` filters it exactly; an
    over-high ``_max_end`` instead lets :meth:`contains` admit a position the
    buffer cannot answer, and :meth:`find_index` then finds no record to
    return -- whereupon the read falls through to the file, which is a wasted
    look rather than a wrong record.  ``_max_end`` stays exact under every
    eviction (see :meth:`prune`), and both are rebuilt exactly whenever
    :meth:`prune` walks the whole deque -- which is what lets ``_max_width``
    shrink back after a wide record dies rather than widening every subsequent
    search for the rest of the buffer's life.

    The maxima and ``_compact_size`` -- the deque's length as of that last walk
    -- are the only state beyond the deque itself, and all three are reset with
    it.

    The VCF backend feeds this buffer too, with records of its own -- whose
    PAYLOAD is a ``(variant record, allele index)`` pair rather than a raw row.
    Nothing here reads the payload, so the buffer needs to know nothing about
    which backend built the record it is holding: it windows every record by the
    three slots above, and those mean the same thing in all of them.
    """

    # Compaction policy -- see :meth:`prune`.  The deque is walked only once it
    # has grown past ``COMPACT_GROWTH`` times its size at the last walk, which
    # keeps the walk amortized O(1) per buffered record.
    #
    # The bound that buys is ``COMPACT_GROWTH`` times the survivor count *at
    # the last walk* -- not times the live set as it stands.  Only a walk
    # lowers that baseline, so a scan leaving a dense region behind one
    # spanning record (which stops the leading pop from firing) can hold a
    # buffer sized for the density it has left until the next walk corrects
    # it.  Bounded and self-correcting, one walk later; do not read it as a
    # bound on the *current* live set.
    #
    # 1.5 was measured, not guessed: on a real scATAC fragments table (4.2M
    # records on chr21, 99.7% overlapping, widest interval ~2kb) a 1bp scan of
    # the densest region runs at the same speed for every factor from 1.1 to
    # 2.0 and degrades above it, so the smallest buffer within that flat band
    # is free.  A deque shorter than ``COMPACT_FLOOR`` is never walked at all:
    # the scan it would save is already trivial, and prune runs on every query.
    COMPACT_GROWTH: float = 1.5
    COMPACT_FLOOR: int = 32

    def __init__(self) -> None:
        self.deque: deque[Record] = deque()
        # Maximum pos_end, and maximum interval width, over the buffered
        # records.  Both are meaningless while the deque is empty and are reset
        # with it; see the class docstring for what they are for.
        self._max_end: int = 0
        self._max_width: int = 0
        # Deque length as of the last compaction -- the baseline the growth
        # rule in ``prune`` measures against.
        self._compact_size: int = 0

    def __len__(self) -> int:
        return len(self.deque)

    def clear(self) -> None:
        self.deque.clear()
        self._max_end = 0
        self._max_width = 0
        self._compact_size = 0

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
        """Drop the records that can no longer match ``pos`` or later.

        A record is dead once its ``pos_end`` falls below ``pos``: every query
        from here on starts at ``pos`` or later, so nothing it could overlap
        will ever be asked for again.

        ``_max_end`` survives either pass exactly, and not by luck.  Every
        record dropped ends *before* ``pos`` while at least one survivor ends
        at or after it, so a dropped record can never have held the maximum
        unless there are no survivors at all -- and that case empties the deque
        and resets the maxima with it.  That exactness is what keeps
        :meth:`contains` from admitting a position the buffer cannot answer.
        ``_max_width`` is *not* exact between compactions and is not required
        to be: it may only over-estimate, which merely widens
        :meth:`find_index`'s search.  A compaction rebuilds both exactly.

        Pruning drops **every** record that fails that test, not just the
        leading run of them.  Stopping at the first survivor -- which is what
        this did -- let a wide record pin the head and hold behind it every
        narrow record it spans, which :meth:`fetch` then rescanned on every
        query; the buffer grew with the widest interval over a dense region
        rather than staying at the live set (gain#287).  Dropping the dead
        records wherever they sit keeps the deque at the records that can
        actually match, and :meth:`fetch`'s scan is bounded by the deque, so
        the two shrink together.
        """
        if len(self.deque) == 0:
            return

        first = self.peek_first()

        if chrom != first[CHROM]:
            self.clear()
            return

        # Cheap pass first: drop the leading dead run.  Each record is popped
        # at most once over the buffer's life, so this is O(1) amortized, and
        # it is all that is needed wherever ``pos_end`` is non-decreasing --
        # point tables and disjoint intervals never buffer a spanning record,
        # so the head is the only place a dead one can be.
        while len(self.deque) > 0:
            if pos <= self.peek_first()[POS_END]:
                break
            self.deque.popleft()

        if len(self.deque) == 0:
            self.clear()
            return

        # Expensive pass: the dead records *behind* a spanning one.  Reaching
        # them means walking the whole deque, so doing it on every prune costs
        # more than the rescan it saves -- measured, and it is a real loss, not
        # a wash.  Let the deque grow past a multiple of its post-walk size
        # instead, which keeps the walk amortized O(1) per buffered record.
        # See ``COMPACT_GROWTH`` for what that does and does not bound.
        threshold = max(
            int(self.COMPACT_GROWTH * self._compact_size), self.COMPACT_FLOOR)
        if len(self.deque) >= threshold:
            self._compact(pos)

    def _compact(self, pos: int) -> None:
        """Drop every record ending before ``pos``, wherever it sits.

        One pass, rebuilding both maxima from the survivors.  Recomputing them
        is free -- every survivor is visited anyway -- and it is what lets
        ``_max_width`` shrink back after a wide record dies: a stale
        ``_max_width`` keeps widening :meth:`find_index`'s search window long
        after the record that justified it has gone.
        """
        survivors: deque[Record] = deque()
        max_end = 0
        max_width = 0
        last_index = len(self.deque) - 1
        for index, record in enumerate(self.deque):
            pos_end: int = record[POS_END]
            # The tail is kept even when it is already dead.  It is the last
            # record read from the file, and ``table_tabix`` reads it back
            # through :meth:`peek_last` as a stand-in for the file cursor --
            # to decide whether anything unread can still reach the query
            # (``_gen_from_buffer_and_tabix``) and whether to seek forward or
            # re-fetch (``_should_use_sequential_seek_forward``).  Dropping it
            # would leave those two reading an *earlier* record as the cursor.
            # Holding one dead record costs nothing: ``fetch`` filters exactly.
            if pos_end < pos and index != last_index:
                continue
            pos_begin: int = record[POS_BEGIN]
            survivors.append(record)
            max_end = max(max_end, pos_end)
            max_width = max(max_width, pos_end - pos_begin)

        # Never empty: the tail is always kept, and the caller has already
        # established that the head survives.
        self.deque = survivors
        self._max_end = max_end
        self._max_width = max_width
        self._compact_size = len(survivors)

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
