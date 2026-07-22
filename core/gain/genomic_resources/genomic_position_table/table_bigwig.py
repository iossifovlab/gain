from __future__ import annotations

from collections.abc import Callable, Generator
from typing import ClassVar

from gain.genomic_resources.genomic_position_table.record import Record
from gain.genomic_resources.genomic_position_table.table import (
    GenomicPositionTable,
)
from gain.genomic_resources.repository import GenomicResource
from gain.utils.regions import Region

# One already-fetched bigWig interval: begin, end and value, with begin and end
# ALREADY converted from the file's 0-based half-open coordinates to the closed
# one-based interval of the record contract.  That ``+1`` conversion lives in
# the fetch methods (``_fetch_buffered``/``_fetch_direct``) and is deliberately
# left there untouched -- the record migration changes what a fetch *yields*,
# not how it fetches -- so a bigWig parser, unlike the tabular one, has no
# coordinate transform of its own to fold in.
BigWigInterval = tuple[int, int, float]

# A bigWig parser maps a (mapped/reference chrom, fetched interval) pair to a
# record.  It is not a ``TabularParser``: a bigWig line is not a row of string
# cells parsed by column key, it is a numeric interval whose contig is threaded
# in from the query, so it has a signature of its own (as the VCF parser does).
BigWigParser = Callable[[str, BigWigInterval], Record]

# --- adaptive chunking -----------------------------------------------------
#
# A region fetch is served in chunks, and the chunking is NOT optional: it is
# the memory guard.  ``pyBigWig.intervals()`` materialises its whole range as a
# Python list (~160 bytes per interval), and the resource-statistics and
# histogram scans ask for a region with no bounds, which expands to a whole
# chromosome -- a single unchunked call on a per-base score (phyloP,
# phastCons) would be ~40 GB on chr1.
#
# What IS wrong is sizing the chunk in base pairs.  Base pairs say nothing
# about how many records a call will return: density varies by orders of
# magnitude between resources, so any fixed window is either fatal on a dense
# track or absurdly chatty on a sparse one.  The old 50 bp default was the
# chatty end -- one range query per 50 bp, ~20,000 of them for a 1 Mb region,
# and 20,000 *empty* ones to cross a 1 Mb gap in a sparse track.
#
# So the budget is a RECORD COUNT, and the base-pair window is the variable
# retuned toward it: issue a call, see how many records came back, scale the
# next window by ``target / observed``.  Density-independent by construction --
# dense ranges converge to a small window (bounding memory), sparse ranges to a
# large one (collapsing the empty-gap walk).

# Records per ``intervals()`` call the window is retuned toward.  At ~160 bytes
# per interval this is ~0.8 MB of live intervals per call, and it is what both
# fetch strategies default to.
DEFAULT_FETCH_TARGET_RECORDS = 5_000

# Base-pair window the retuning starts from, before any density is observed.
# On a per-base track the first call overshoots to this many records (~1.6 MB)
# and the window then converges; on a sparse one it grows away from here.
INITIAL_FETCH_WINDOW = 10_000

# Hard clamps on the window.  The maximum is the real memory bound: a window
# can be at most this many base pairs, so even a per-base track hit right after
# a sparse stretch materialises at most ~1M intervals (~160 MB) in one call
# before the retune reacts.  The minimum keeps a pathologically dense track
# from degenerating into per-base queries.
MIN_FETCH_WINDOW = 50
MAX_FETCH_WINDOW = 1_000_000

# Per-step clamps on the retune, so a single unrepresentative call cannot slam
# the window to a clamp and back.  An empty call carries no density signal at
# all, so it simply takes the maximum growth.
MAX_WINDOW_GROWTH = 8.0
MAX_WINDOW_SHRINK = 0.125


class AdaptiveFetchWindow:
    """A base-pair window retuned toward a target records-per-call budget.

    Owned by a :class:`BigWigTable` and kept across region fetches on purpose:
    density is a property of the *resource*, so what one fetch learns about a
    track is exactly what the next fetch should start from.
    """

    def __init__(self, target_records: int) -> None:
        self.target = max(1, int(target_records))
        self.window = INITIAL_FETCH_WINDOW

    def retune(self, records_fetched: int) -> None:
        """Rescale the window from the record count the last call returned."""
        if records_fetched <= 0:
            scale = MAX_WINDOW_GROWTH
        else:
            scale = self.target / records_fetched
        scale = min(max(scale, MAX_WINDOW_SHRINK), MAX_WINDOW_GROWTH)
        self.window = int(min(
            max(self.window * scale, MIN_FETCH_WINDOW), MAX_FETCH_WINDOW))


def build_bigwig_parser() -> BigWigParser:
    """Build a (chrom, interval) -> record parser for the bigWig backend.

    Built once, at :meth:`BigWigTable.open`, and called per line -- the point
    of the record migration is that a fetched line no longer constructs a
    per-line ``BigWigLine`` adapter object, only a plain record tuple.

    A bigWig record's PAYLOAD is the **four-element interval**
    ``(chrom, pos_begin, pos_end, value)`` -- the very tuple the retired
    ``BigWigLine`` carried as its raw row -- so the single value column stays
    addressable at index 3, which is where every bigWig score's ``index: 3``
    resolves through :class:`RecordScoreLine`'s by-index payload read.  REF and
    ALT are always ``None``: a bigWig carries neither.

    The parser closes over nothing.  A bigWig has no configurable transform for
    it to specialise on -- it is a binary format with a fixed layout, its
    coordinates are converted upstream in the fetch methods, and its result
    contig is the (already reference-mapped) query chrom threaded in per call.
    It is still built here, once, rather than inlined, to keep the shape of the
    three record backends identical: a parser is built at open() and produces
    the records the fetch path yields.
    """
    def parse(chrom: str, interval: BigWigInterval) -> Record:
        # PAYLOAD repeats chrom/begin/end so that ``payload[3]`` is the value:
        # this is byte-for-byte the tuple ``BigWigLine((chrom, *interval))``
        # wrapped, kept identical so the score read is unchanged.
        payload = (chrom, *interval)
        return (chrom, interval[0], interval[1], None, None, payload)
    return parse


class BigWigTable(GenomicPositionTable):
    """bigWig format implementation of the genomic position table.

    Yields **records** -- the six-slot plain tuples of the record contract --
    so the score layer wraps its lines in a :class:`RecordScoreLine`, exactly
    like the tabix and in-memory backends.  A bigWig record's PAYLOAD is the
    four-element interval ``(chrom, pos_begin, pos_end, value)`` (see
    :func:`build_bigwig_parser`); the value column is read from it by index,
    the same read the retired ``BigWigLine`` adapter served through ``get``.
    """

    # This backend yields records rather than line adapters (#238).
    yields_records: ClassVar[bool] = True

    def __init__(
        self,
        genomic_resource: GenomicResource,
        table_definition: dict,
    ):
        super().__init__(genomic_resource, table_definition)
        self._bw_file = None
        self.chroms: dict[str, int] = {}
        self._buffer: list[tuple[int, int, float]] = []
        self._buffer_region: Region = Region("?", -1, -1)

        # Both fetch sizes are budgets in RECORDS per ``intervals()`` call --
        # not in base pairs, which is what they used to mean and what made a
        # region fetch cost one range query per 50 bp (see the module notes on
        # adaptive chunking).  Both are accepted by the table schema.
        self.direct_fetch_size = self.definition.get(
            "direct_fetch_size", DEFAULT_FETCH_TARGET_RECORDS)
        self.buffer_fetch_size = self.definition.get(
            "buffer_fetch_size", DEFAULT_FETCH_TARGET_RECORDS)
        self.use_buffered_threshold = \
            self.definition.get("use_buffered_threshold", 500)

        self._direct_window = AdaptiveFetchWindow(self.direct_fetch_size)
        self._buffer_window = AdaptiveFetchWindow(self.buffer_fetch_size)

        # this forces the initial fetch to be made directly
        self._last_pos = -(self.use_buffered_threshold + 1)

        # Built in open(): a bigWig parser closes over nothing, but it is built
        # there and torn down in close() to keep the record backends uniform.
        self.parser: BigWigParser | None = None

    def open(self) -> BigWigTable:
        self._bw_file = self.genomic_resource.open_bigwig_file(
            self.definition.filename)
        if self._bw_file is None:
            raise OSError
        self.chroms = self._bw_file.chroms()
        self._set_core_column_keys()
        self._build_chrom_mapping()
        self.parser = build_bigwig_parser()
        # A reopened table must not answer out of the previous open's buffer.
        # The buffer is keyed by region, not by file, so a table closed and
        # reopened over CHANGED data served the old file's values for any query
        # landing in the retained span -- silently, since a buffer hit never
        # falls through to the file.  This is the invariant's home: close()
        # clears the buffer as well, but only to release the memory, and a
        # caller is not required to have called it.
        self._discard_buffer()
        return self

    def close(self) -> None:
        if self._bw_file is not None:
            self._bw_file.close()
        self._bw_file = None
        self.parser = None
        # Release the fetched intervals too.  open() re-establishes this
        # anyway, so what the clearing here buys is memory, not correctness: a
        # closed table that still holds its last chunk keeps up to a full fetch
        # window of intervals alive for as long as anything holds the table,
        # which is what made gain#345 expensive rather than merely untidy.
        self._discard_buffer()

    def _discard_buffer(self) -> None:
        """Drop the buffered intervals and the region they cover."""
        self._buffer = []
        self._buffer_region = Region("?", -1, -1)

    def _fetch_chunk(
        self, window: AdaptiveFetchWindow,
        chrom: str, pos: int, scan_stop: int, hard_stop: int,
    ) -> tuple[list[BigWigInterval], int]:
        """Return the first non-empty chunk of intervals at or after ``pos``.

        Issues ``intervals()`` calls of ``window`` base pairs, retuning the
        window from each call's record count, until one comes back non-empty or
        ``pos`` reaches ``scan_stop``.  Windows never extend past ``hard_stop``.

        Returns the chunk together with the position to resume from.  That
        resume position is ``max(window_end, last_interval_end)``: an interval
        straddling the window end is returned whole by ``intervals()``, so
        resuming at its end is what keeps it from being yielded twice, while
        resuming at the window end (rather than at an earlier last-interval
        end) skips re-scanning a tail already known to be empty.

        An empty return means the range is exhausted -- ``intervals()`` returns
        every interval overlapping its range, so a call that comes back empty
        proves the whole window it covered holds no records.  That is what
        makes the adaptive stride safe over gaps: one growing query per gap
        instead of one fixed-size query per 50 bp of it.
        """
        assert self._bw_file is not None
        while pos < scan_stop:
            end = min(pos + window.window, hard_stop)
            intervals = self._bw_file.intervals(chrom, pos, end)
            window.retune(len(intervals) if intervals else 0)
            if intervals:
                return list(intervals), max(end, intervals[-1][1])
            if end <= pos:
                break
            pos = end
        return [], pos

    def _fill(self, chrom: str, start: int, stop: int) -> None:
        """
        Attempts to fill the buffer with records for the given range.

        Fetches adaptively-sized ranges starting from ``start`` until either
        results are found or ``stop`` is reached.  As before, a range may
        reach past ``stop`` (up to the end of the contig), so the buffer can
        hold records beyond the requested end -- it is a buffer, and
        :meth:`_fetch_buffered` bounds what it yields out of it.
        """
        assert self._bw_file is not None
        self._buffer = []
        self._buffer_region = Region("?", -1, -1)

        chromlen = self.chroms[chrom]
        res, _ = self._fetch_chunk(
            self._buffer_window, chrom, start, min(stop, chromlen), chromlen)

        self._buffer = res
        if res:
            self._buffer_region = Region(
                chrom,
                self._buffer[0][0] + 1,
                self._buffer[-1][1])

    def _find(self, chrom: str, pos_begin: int, pos_end: int) -> int:
        """Return the buffer index the query starts at, or -1 to refill.

        On a hit, that is the first buffered interval overlapping the query.
        On a miss -- the query falls in an unscored gap *between* two buffered
        intervals -- it is the insertion point: the first interval that is not
        entirely to the LEFT of the query.  Returning the left-hand neighbour
        instead would make :meth:`_fetch_buffered`, which bounds only the right
        side of what it yields, emit a record from before the query.  That is a
        real score value at a position the track does not cover, and the wider
        the fill window, the more often a query lands inside the buffer's span
        rather than outside it.
        """

        def _left_right_helper(
            q_start: int, q_stop: int,
            start: int, stop: int,
        ) -> int:
            if q_stop <= start:
                return -1
            if q_start >= stop:
                return 1
            return 0

        query = Region(chrom, pos_begin + 1, pos_end)
        if not query.intersects(self._buffer_region):
            return -1

        # do binary search on buffer, get idx
        l_bound = 0
        r_bound = len(self._buffer) - 1
        while l_bound <= r_bound:
            idx: int = (r_bound + l_bound) // 2
            line = self._buffer[idx]
            res = _left_right_helper(
                pos_begin, pos_end, line[0], line[1])
            if res == 1:
                l_bound = idx + 1
            elif res == -1:
                r_bound = idx - 1
            elif res == 0:
                if idx == 0:
                    return idx
                prevline = self._buffer[idx - 1]
                subres = _left_right_helper(
                    pos_begin, pos_end, prevline[0], prevline[1])
                if subres == 0:
                    r_bound = idx - 1
                else:
                    return idx
        # No overlap: ``l_bound`` is the insertion point.  It is always a valid
        # index here -- the query intersects the buffer's region, so the last
        # buffered interval cannot be entirely to the query's left, and only an
        # entirely-left interval advances ``l_bound`` past its index.
        return l_bound

    def _fetch_buffered(
        self, chrom: str, pos_begin: int, pos_end: int,
    ) -> Generator[tuple[int, int, float], None, None]:
        pos_current = pos_begin

        idx = self._find(chrom, pos_begin, pos_begin + 1)
        if idx == -1:
            self._fill(chrom, pos_begin, pos_end)
            idx = self._find(chrom, pos_begin, pos_begin + 1)

        if idx == -1:
            # there's no direct match for (pos_begin, pos_begin + 1), but
            # we set the idx to 0 anyways since there might be something
            # in the buffer to yield (since _fill is called with pos_end)
            idx = 0

        while self._buffer:
            # A generator that outlives close() must not look like a complete,
            # shorter result set.  The loop guard is the buffer, and close()
            # empties it, so without this the consumer of a fetch straddling a
            # close gets a silently truncated scan instead of an error; a fetch
            # *started* after close already raises on the parser assert in
            # get_records_in_region.
            assert self._bw_file is not None, \
                "bigWig table closed while a region fetch was in flight"
            line = self._buffer[idx]
            if line[0] + 1 > pos_end:
                return
            yield (line[0] + 1, line[1], line[2])
            pos_current = line[1]
            if pos_current >= pos_end:
                return
            idx += 1
            if idx == len(self._buffer):
                self._fill(chrom, pos_current, pos_end)
                idx = 0

    def _fetch_direct(
        self, chrom: str, pos_begin: int, pos_end: int,
    ) -> Generator[tuple[int, int, float], None, None]:
        assert self._bw_file is not None
        chrom_len = self.chroms[chrom]
        pos_end = min(pos_end, chrom_len)

        start = pos_begin
        while start < pos_end:
            intervals, start = self._fetch_chunk(
                self._direct_window, chrom, start, pos_end, pos_end)
            if not intervals:
                return
            for interval in intervals:
                yield (interval[0] + 1, interval[1], interval[2])

    def get_records_in_region(
        self,
        chrom: str | None = None,
        pos_begin: int | None = None,
        pos_end: int | None = None,
    ) -> Generator[Record, None, None]:
        """Yield the records overlapping the region, as record tuples.

        Chromosome mapping is applied on **both** ends, unchanged: the query
        contig is mapped reference->file by ``_map_file_chrom`` before the
        fetch, and each record's CHROM slot carries ``chrom`` back -- the
        reference-space contig the caller asked for -- so the result stays in
        reference space.  The interval the fetch method yields is already in
        the record contract's closed one-based coordinates (the ``+1`` lives in
        the fetch methods); the parser only assembles the record around it.
        """
        if chrom is None:
            yield from self.get_all_records()
            return

        assert self.parser is not None
        parser = self.parser

        fchrom = self._map_file_chrom(chrom)
        if fchrom not in self.chroms:
            raise KeyError
        if pos_begin is None:
            pos_begin = 0
        if pos_end is None:
            pos_end = self.chroms[fchrom]

        pos_begin = max(0, pos_begin - 1)

        fetch_method = self._fetch_buffered \
            if pos_begin - self._last_pos <= self.use_buffered_threshold \
            else self._fetch_direct

        self._last_pos = pos_begin

        for interval in fetch_method(fchrom, pos_begin, pos_end):
            yield parser(chrom, interval)

    def get_all_records(self) -> Generator[Record, None, None]:
        assert self._bw_file is not None
        for chrom in self.get_chromosomes():
            yield from self.get_records_in_region(chrom)

    def get_chromosome_length(
        self, chrom: str,
        step: int = 100_000_000,  # noqa: ARG002
    ) -> int:
        assert self._bw_file is not None
        if chrom not in self.get_chromosomes():
            raise ValueError(
                f"contig {chrom} not present in the table's contigs: "
                f"{self.get_chromosomes()}")
        fchrom = self._map_file_chrom(chrom)
        if fchrom is None:
            raise ValueError(
                f"error in mapping chromsome {chrom} to the file contigs: "
                f"{self.get_file_chromosomes()}",
            )
        if fchrom not in self.get_file_chromosomes():
            raise ValueError(
                f"contig {fchrom} not present in the file's contigs: "
                f"{self.get_file_chromosomes()}",
            )
        return self.chroms[fchrom]

    def _load_file_chromosomes(self) -> list[str]:
        assert self._bw_file is not None
        return list(self.chroms.keys())
