from __future__ import annotations

from collections.abc import Callable, Generator, Iterable
from typing import ClassVar

import numpy as np

from gain.genomic_resources.genomic_position_table.record import Record
from gain.genomic_resources.genomic_position_table.table import (
    ContigExtent,
    GenomicPositionTable,
)
from gain.genomic_resources.repository import GenomicResource

# One fetched bigWig interval: begin, end and value, exactly as
# ``pyBigWig.intervals()`` returned it -- RAW, in the file's own 0-based
# half-open coordinates.  Converting it to the closed one-based interval of the
# record contract is the parser's job (see :func:`build_bigwig_parser`), as the
# zero-based transform is the tabular parser's.
BigWigInterval = tuple[int, int, float]

# The one column a bigWig has, for the bulk column-array read.  A record's
# PAYLOAD is the interval's value itself, so "column 0 of the payload" and "the
# value" name the same thing and there is no second column to address.  Stated
# once, here, because this module owns the payload's shape; the score layer
# re-exports it (``bigwig_scores.BIGWIG_VALUE_COLUMN``) rather than repeating
# the literal.
VALUE_COLUMN = 0

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
#
# This is the ONLY chunking there is.  A second strategy used to sit beside it,
# keeping the fetched intervals in a buffer retained across calls and serving a
# later query out of it by binary search; ``use_buffered_threshold`` chose
# between them by how far apart two queries started.  It was measured to lose
# on every access pattern real data produces -- irregular query spacing mixes
# the two paths, so each short gap pays for a fill that the next long gap
# discards -- and removed.  It does win when an ``intervals()`` call carries
# network latency, which is a live configuration (``open_bigwig_file`` accepts
# ``http``/``s3``); bringing it back for that case is gain#449.  The
# measurements, and what would have to be true to reinstate it, are in
# ``docs/adr/0002-remove-bigwig-fetch-buffering.md``.

# Records per ``intervals()`` call the window is retuned toward.  At ~160 bytes
# per interval this is ~0.8 MB of live intervals per call.  Overridable per
# resource as ``fetch_size``.
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

    A bigWig record's PAYLOAD is the **value itself** -- a bare ``float``, not
    a tuple.  A bigWig carries one number per interval; everything else the
    payload used to repeat (``(chrom, pos_begin, pos_end, value)``, inherited
    from the retired ``BigWigLine``'s raw row) is already decoded into the
    record's own slots, and the repetition existed only so the value was
    addressable at ``payload[3]``.  With the narrowing, reading a bigWig score
    is ``record[PAYLOAD]`` -- an identity, with no index and no parse (see
    ``bigwig_scores.extract_bigwig_value``).  REF and ALT are always ``None``:
    a bigWig carries neither.

    The three reasons the earlier, wider shape was kept are recorded -- and
    answered -- in the ledger entry in this package's ``__init__``.  The short
    of it: ``index: 3`` survives as an accepted deprecated no-op rather than as
    a payload shape, the out-of-range ``IndexError`` is superseded by an
    open-time refusal that names the resource and the score, and the
    "no speed to be had" measurement predated the removal of the parse.

    **The parser owns the coordinate conversion.**  It takes the interval RAW,
    in the file's 0-based half-open coordinates, and produces the closed
    one-based interval of the record contract by doing the ``+1`` on the begin
    itself -- the same fusion of transform and record construction the tabular
    parser performs for its zero-based tables.  This reverses an earlier design
    point, which converted in the fetch methods and documented the parser as
    having no transform of its own: converting a layer up meant building a
    3-tuple per interval whose only reader was this function (gain#823).

    The parser closes over nothing.  A bigWig has no configurable transform for
    it to specialise on -- it is a binary format with a fixed layout, its
    conversion is fixed by the format rather than by config, and its result
    contig is the (already reference-mapped) query chrom threaded in per call.
    It is still built here, once, rather than inlined, to keep the shape of the
    three record backends identical: a parser is built at open() and produces
    the records the fetch path yields.
    """
    def parse(chrom: str, interval: BigWigInterval) -> Record:
        return (chrom, interval[0] + 1, interval[1], None, None, interval[2])
    return parse


class BigWigTable(GenomicPositionTable):
    """bigWig format implementation of the genomic position table.

    Yields **records** -- the six-slot plain tuples of the record contract --
    exactly like the tabix and in-memory backends.  A bigWig record's PAYLOAD
    is the interval's value, a bare ``float`` (see
    :func:`build_bigwig_parser`); the score layer reads it straight out of the
    slot, with no index and no parse.
    """

    # This backend yields records rather than line adapters (#238).
    yields_records: ClassVar[bool] = True

    # The header carries an exact size for every contig it lists (see
    # :meth:`find_chromosome_length`), so lengths from this backend can
    # serve as true denominators.
    chrom_lengths_are_exact: ClassVar[bool] = True

    # Serves the bulk column-array read; see get_region_value_arrays below.
    supports_value_arrays: ClassVar[bool] = True

    def __init__(
        self,
        genomic_resource: GenomicResource,
        table_definition: dict,
    ):
        super().__init__(genomic_resource, table_definition)
        self._bw_file = None
        self.chroms: dict[str, int] = {}

        # A budget in RECORDS per ``intervals()`` call -- not in base pairs,
        # which is what it used to mean and what made a region fetch cost one
        # range query per 50 bp (see the module notes on adaptive chunking).
        self.fetch_size = self.definition.get(
            "fetch_size", DEFAULT_FETCH_TARGET_RECORDS)
        self._window = AdaptiveFetchWindow(self.fetch_size)

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
        return self

    def close(self) -> None:
        super().close()
        if self._bw_file is not None:
            self._bw_file.close()
        self._bw_file = None
        self.parser = None
        # The file's whole contig dictionary -- ~600 entries on hg38.  open()
        # reads it back off the handle unconditionally, and every reader of it
        # is already behind a not-open guard -- `find_chromosome_length` and
        # `_load_file_chromosomes` raise ValueError off `_bw_file` (gain#358),
        # the fetch paths assert on `_bw_file` or on the `parser` this method
        # also drops -- so a closed table holds a copy nothing can reach
        # (gain#350).
        self.chroms = {}
        # There is no fetched-interval state to release here any more: a fetch
        # materialises one chunk at a time inside the generator that yields it,
        # so nothing survives the call (gain#345 was about a retained buffer,
        # which this backend no longer keeps).

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

    def get_records_in_region(
        self,
        chrom: str,
        pos_begin: int | None = None,
        pos_end: int | None = None,
    ) -> Generator[Record, None, None]:
        """Yield the records overlapping the region, as record tuples.

        Chromosome mapping is applied on **both** ends, unchanged: the query
        contig is mapped reference->file by ``_map_file_chrom`` before the
        fetch, and each record's CHROM slot carries ``chrom`` back -- the
        reference-space contig the caller asked for -- so the result stays in
        reference space.  The intervals go to the parser RAW, in the file's
        0-based half-open coordinates, and the parser converts them to the
        contract's closed one-based interval in the same expression that
        assembles the record (see :func:`build_bigwig_parser`).

        The adaptive chunk walk is inlined here rather than sitting behind a
        fetch generator of its own, which would resume once per record to hand
        over an interval this method passes straight on (gain#823).  It is the
        only fetch strategy; the retained-buffer one is gone (see
        ``docs/adr/0002-remove-bigwig-fetch-buffering.md``).
        """
        assert self.parser is not None
        parser = self.parser

        fchrom = self._map_file_chrom(chrom)
        if fchrom not in self.chroms:
            # Says which contig, which resource, and what the file does have
            # -- the last is the actual diagnostic, since the usual cause is a
            # 'chr1' / '1' spelling mismatch the chrom_mapping did not cover.
            # This was a bare ``raise KeyError``: no argument, no message, so
            # the only thing naming the resource was a log line one layer up
            # in ``GenomicScore.fetch_records``.  Same defect, same fix, as
            # ``find_chromosome_length`` and ``_load_file_chromosomes``
            # (gain#358).  The TYPE stays ``KeyError`` -- callers catch it.
            raise KeyError(
                f"bigwig table of resource "
                f"{self.genomic_resource.resource_id}: contig {chrom!r} "
                f"(mapped to {fchrom!r}) is not among the file's contigs: "
                f"{sorted(self.chroms)}")
        # Same normalization as get_region_value_arrays', in the same shape:
        # both walks bound themselves by the contig's length, and reading them
        # side by side is what keeps them from drifting apart.
        chrom_len = self.chroms[fchrom]
        start = 0 if pos_begin is None else max(0, pos_begin - 1)
        stop = chrom_len if pos_end is None else min(pos_end, chrom_len)

        while start < stop:
            # A generator that outlives close() must not look like a complete,
            # shorter result set.  The handle is what says the table is still
            # usable, and it is checked per chunk rather than once on entry: a
            # close landing between two chunks is exactly the case a lazily-
            # consumed fetch produces.  Stated with a message because the
            # message is the whole point -- a truncated score list is
            # indistinguishable from a complete one at the call site, and for
            # an annotation read that is wrong data rather than an error.
            assert self._bw_file is not None, \
                "bigWig table closed while a region fetch was in flight"
            intervals, start = self._fetch_chunk(
                self._window, fchrom, start, stop, stop)
            if not intervals:
                return
            for interval in intervals:
                yield parser(chrom, interval)

    def get_region_value_arrays(
        self,
        chrom: str,
        start: int | None,
        end: int | None,
        value_columns: Iterable[int],
        batch_size: int,  # noqa: ARG002
    ) -> Generator[
            tuple[np.ndarray, np.ndarray, dict[int, np.ndarray]], None, None]:
        """Yield a region's intervals as column arrays, without records.

        The bigWig counterpart of
        :meth:`TabixGenomicPositionTable.get_region_value_arrays`: a fast path
        for a full sequential scan (statistics).  It reuses the adaptive
        :meth:`_fetch_chunk` windowing -- so memory stays bounded exactly as
        the record path's -- but turns each chunk of raw intervals into arrays
        in one shot rather than building a ``Record`` per interval.  The
        coordinates match the record path's: the raw zero-based half-open
        ``[begin, end)`` becomes closed one-based (``begin + 1``, ``end``) --
        here in one vectorized shift, there in :func:`build_bigwig_parser`.

        **A bigWig has exactly one column, and its index is 0.**  A record's
        PAYLOAD is the interval's value (see :func:`build_bigwig_parser`), so
        "the payload's column 0" and "the value" are the same thing, and any
        other index names a column this backend does not have -- refused with
        a ``KeyError`` naming the resource rather than served whatever the old
        four-tuple reconstruction happened to hold at that offset.  That
        reconstruction existed to make a bad index raise the ``IndexError``
        the record path raised; the record path no longer indexes anything, and
        a misconfigured index is now refused when the *score* is opened, by
        name, which is a better diagnostic than either.  This check is the
        backstop for a caller that reaches the table directly.

        ``batch_size`` is accepted for a uniform producer signature; the batch
        size here is set by the adaptive fetch window, not this argument.
        """
        assert self._bw_file is not None
        fchrom = self._map_file_chrom(chrom)
        if fchrom not in self.chroms:
            raise KeyError(fchrom)

        chrom_len = self.chroms[fchrom]
        pos = 0 if start is None else max(0, start - 1)
        scan_stop = chrom_len if end is None else min(end, chrom_len)
        bad_columns = sorted(
            col for col in value_columns if col != VALUE_COLUMN)
        if bad_columns:
            raise KeyError(
                f"bigwig table of resource "
                f"{self.genomic_resource.resource_id}: a bigWig record's "
                f"payload is its value, so column {VALUE_COLUMN} is the only "
                f"one there is; asked for {bad_columns}")

        while pos < scan_stop:
            intervals, pos = self._fetch_chunk(
                self._window, fchrom, pos, scan_stop, scan_stop)
            if not intervals:
                return
            raw = np.array(intervals, dtype=np.float64)
            pos_begin = raw[:, 0].astype(np.int64) + 1
            pos_end = raw[:, 1].astype(np.int64)
            yield pos_begin, pos_end, {VALUE_COLUMN: raw[:, 2]}

    def get_all_records(self) -> Generator[Record, None, None]:
        assert self._bw_file is not None
        for chrom in self.get_chromosomes():
            yield from self.get_records_in_region(chrom)

    def find_chromosome_length(
        self, chrom: str,
        step: int = 100_000_000,  # noqa: ARG002
    ) -> int | ContigExtent:
        # NEITHER ContigExtent member is reachable from this backend, and that
        # is a property of the format rather than an omission: a bigWig header
        # carries an exact size for every contig it lists, so a listed contig
        # always has a length and an unlisted one is refused below as the bad
        # question it is.  There is nothing to prove empty and nothing to leave
        # undetermined -- which is why the return type is still the tri-state
        # one (the base class's hook) while every path here returns an int or
        # raises.
        #
        # A closed table FIRST, in the words the other three backends use --
        # this carried a bare ``assert`` where the tabix backend raises, so a
        # closed table's caller got a message-less AssertionError from the
        # backend whose siblings all name the resource; and under ``python -O``,
        # which strips the assert, control fell through into the branch below,
        # whose message interpolates get_chromosomes() -- a read a closed table
        # refuses, so the intended diagnostic could not be built (gain#358).
        if self._bw_file is None:
            raise ValueError(
                f"bigwig table not open: "
                f"{self.genomic_resource.resource_id}: "
                f"{self.definition}")
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
        """Return the contigs ``open()`` read off the handle.

        A closed table refuses, in the words its three siblings use: the
        contract is that a closed table does not answer reads derived from the
        file (see :meth:`GenomicPositionTable.close`), and it is stated as a
        ``ValueError`` rather than the bare ``assert`` this used to carry so
        that all four backends refuse the same way and a caller can catch one
        thing (gain#358).  ``self.chroms`` cannot be the guard -- ``close()``
        empties it, and an empty dict would otherwise be handed back as an
        answer -- so this reads the handle, which ``open()`` establishes before
        it calls ``_build_chrom_mapping`` and ``close()`` drops.
        """
        if self._bw_file is None:
            raise ValueError(
                f"bigwig table not open: "
                f"{self.genomic_resource.resource_id}: "
                f"{self.definition}")
        return list(self.chroms.keys())
