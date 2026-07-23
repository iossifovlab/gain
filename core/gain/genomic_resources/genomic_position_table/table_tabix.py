from __future__ import annotations

import itertools
from collections import Counter
from collections.abc import Generator, Iterable
from typing import Any, ClassVar

import numpy as np
import pysam

from gain import logging
from gain.genomic_resources.repository import GenomicResource
from gain.utils.regions import get_chromosome_length_tabix

from .line import LineBuffer
from .record import (
    CHROM,
    POS_BEGIN,
    POS_END,
    Record,
    TabularParser,
    build_tabular_parser,
)
from .table import GenomicPositionTable

PysamFile = pysam.TabixFile | pysam.VariantFile
logger = logging.getLogger(__name__)


class TabixGenomicPositionTable(GenomicPositionTable):
    """Represents Tabix file genome position table.

    Yields **records** -- the six-slot tuples built by the tabular parser --
    whose payload is the raw ``pysam`` row.  The row is handed on by
    reference and never materialised into a tuple of columns: it decodes a
    column only when a caller indexes it, which is what keeps a 454-score
    resource from paying for 454 decodes when a caller wants one score.

    The read cascade for a region query, in order:

    1. **the buffering decision** -- a query wider than ``BUFFER_MAXSIZE``
       (or open-ended) is served straight from the file, unbuffered;
    2. **the provably-empty-gap short-circuit** -- the query starts after the
       previous query's end and ends before the first buffered record: the
       records in between were already read and none of them reach it, so the
       answer is provably empty without touching the file;
    3. **the buffer hit** -- the query's start is inside the buffered window;
    4. **the sequential seek** -- the query's start is beyond the buffer but
       within ``jump_threshold`` of it, so reading forward beats a fresh
       ``pysam`` fetch (which drops the buffer and its index lookup);
    5. **the fresh fetch** -- everything else re-seeks the file.

    :meth:`_gen_from_tabix` buffers every record it pulls *before* it checks
    whether that record has run past the end of the query.  The record that
    terminates a read is therefore buffered although it is never yielded --
    and the short-circuit in (2) and the window in (3) both depend on it
    being there.  Do not reorder those two steps.
    """

    # This backend yields records rather than line adapters.  The VCF backend
    # subclasses this one and yields records too (with a payload of its own),
    # so it inherits the claim as-is -- see VCFGenomicPositionTable.
    yields_records: ClassVar[bool] = True

    BUFFER_MAXSIZE: int = 20_000

    def __init__(
            self, genomic_resource: GenomicResource, table_definition: dict):
        super().__init__(genomic_resource, table_definition)
        self.jump_threshold: int = 2_500
        if "jump_threshold" in self.definition:
            threshold = self.definition["jump_threshold"]
            if threshold == "none":
                self.jump_threshold = 0
            else:
                self.jump_threshold = int(threshold)

        self.jump_threshold = min(
            self.jump_threshold, self.BUFFER_MAXSIZE // 2)

        self._last_call: tuple[str, int, int | None] = "", -1, -1
        self.buffer = LineBuffer()
        self.stats: Counter = Counter()
        # pylint: disable=no-member
        self.pysam_file: PysamFile | None = None
        self.line_iterator: Generator[Record | None, None, None] | None = None
        self.header: Any
        self.zero_based = self.definition.get("zero_based", False)

        # Built in open(), where the column keys and the chromosome map are
        # finally known.  The VCF subclass leaves this None: its records are not
        # parsed from a tabular row, so it builds a parser of its own (whose
        # signature takes an allele index as well) and leaves this one unused.
        self.parser: TabularParser | None = None

    def _load_header(self) -> tuple[str, ...]:
        header_lines = []
        with self.genomic_resource.open_raw_file(
                self.definition.filename, compression="gzip") as infile:
            while True:
                line = infile.readline()
                if line[0] != "#":
                    break
                header_lines.append(line)
        if not header_lines:
            # A raise, not an assert: an assert carries no message (so
            # nothing that catches it can report the cause) and `python -O`
            # drops the check altogether, leaving the header silently wrong
            # instead of failing (gain#364).
            raise ValueError(
                f"the table of resource "
                f"<{self.genomic_resource.get_full_id()}> is configured to "
                f"read its column names from {self.definition.filename}, "
                f"which has no '#' header line; add 'header_mode: none' to "
                f"the table definition and address the columns by index")
        return tuple(header_lines[-1].strip("#\n").split("\t"))

    def open(self) -> TabixGenomicPositionTable:
        self.pysam_file = self.genomic_resource.open_tabix_file(
            self.definition.filename)
        if self.header_mode == "file":
            self.header = self._load_header()
        self._set_core_column_keys()
        self._build_chrom_mapping()
        # The parser fuses record construction with the zero-based and
        # chromosome-mapping transforms, specialised once here rather than
        # branched per line.  It cannot be built any earlier: resolving the
        # column keys needs the header, and the reverse chromosome map needs
        # the file's contigs.
        self.parser = build_tabular_parser(
            self.chrom_key,
            self.pos_begin_key,
            self.pos_end_key,
            self.ref_key,
            self.alt_key,
            self.rev_chrom_map,
            zero_based=self.zero_based,
        )
        # A reopened table must not answer out of the previous open's buffer.
        # The buffer is keyed by region -- through ``_last_call``, the read
        # cascade's own cursor -- not by file or handle, so a table reopened
        # over CHANGED data served the old file's lines for any query landing in
        # the retained span, silently, since a buffer hit never falls through to
        # the file.  This is the invariant's home, exactly as in
        # ``BigWigTable.open()``: ``close()`` clears the buffer too, but only to
        # release memory, and a caller is not required to have called it.
        self._discard_buffer()
        return self

    def _discard_buffer(self) -> None:
        """Drop the buffered lines and the read cursor keyed to them.

        The two are one cache: ``_last_call`` decides whether the next query is
        answered from ``buffer``, so a cursor left pointing into a discarded
        buffer is as wrong as the buffer itself.  Reset them together.
        """
        self.buffer.clear()
        self._last_call = "", -1, -1

    def close(self) -> None:
        # Release the handle BEFORE the base class's chromosome state, not
        # after.  super().close() drops chrom_map/chrom_order/_file_chromosomes,
        # and get_chromosomes() falls back to the file's own contig names once
        # the map is gone (gain#358) -- so if the map were released first and
        # anything here then raised, the table would be left with a LIVE handle
        # and no map, answering its contigs wrongly.  Handle first, and nulled
        # before super().close() runs, so the "live handle + released map" state
        # never exists: a partial failure leaves the table "still open" rather
        # than "open but unmapped".
        if self.pysam_file is not None:
            if self.line_iterator:
                self.line_iterator.close()
            self.pysam_file.close()
        self.pysam_file = None
        self.line_iterator = None

        super().close()
        self.buffer.clear()
        self.stats = Counter()
        # The parser closes over the column keys and the reverse chromosome
        # map, both of which open() resolves from the file; a closed table must
        # not keep them, and re-opening rebuilds it.
        self.parser = None

    def get_chromosomes(self) -> list[str]:
        return list(filter(
            lambda v: v is not None,  # type: ignore
            [
                self.map_chromosome(chrom)
                for chrom in self.get_file_chromosomes()
            ]))

    def _load_file_chromosomes(self) -> list[str]:
        if self.pysam_file is None:
            raise ValueError(
                f"tabix table not open: "
                f"{self.genomic_resource.resource_id}: "
                f"{self.definition}")
        assert isinstance(self.pysam_file, pysam.TabixFile)
        return self.pysam_file.contigs

    def get_chromosome_length(
            self, chrom: str, step: int = 100_000_000) -> int:
        if self.pysam_file is None:
            raise ValueError(
                f"tabix table not open: "
                f"{self.genomic_resource.resource_id}: "
                f"{self.definition}")
        if chrom not in self.get_chromosomes():
            raise ValueError(
                f"contig {chrom} not present in the table's contigs: "
                f"{self.get_chromosomes()}")
        fchrom = self.unmap_chromosome(chrom)
        if fchrom is None:
            raise ValueError(
                f"error in mapping chromsome {chrom} to the file contigs: "
                f"{self.get_file_chromosomes()}",
            )
        length = get_chromosome_length_tabix(self.pysam_file, fchrom, step)
        if length is None:
            raise ValueError(f"Could not find contig '{fchrom}'")
        return length

    def get_all_records(self) -> Generator[Record, None, None]:
        # pylint: disable=no-member
        for record in self.get_line_iterator():
            if record is None:
                continue
            yield record

    def _should_use_sequential_seek_forward(
            self, chrom: str | None, pos: int) -> bool:
        """Determine if sequentially seeking forward is appropriate.

        Determine whether to use sequential access or jump-ahead
        optimization for a given chromosome and position. Sequential access is
        used if the position is on the same chromosome and the distance between
        it and the last record in the buffer is less than the jump threshold.
        """
        if self.jump_threshold == 0:
            return False

        assert chrom is not None
        if len(self.buffer) == 0:
            return False

        last = self.buffer.peek_last()
        if chrom != last[CHROM]:
            return False
        # A record slot is statically opaque (a record is tuple[Any, ...]);
        # annotate the read so the arithmetic below stays typed.
        last_end: int = last[POS_END]
        if pos < last_end:
            return False

        return (pos - last_end) < self.jump_threshold

    def _sequential_seek_forward(self, chrom: str, pos: int) -> bool:
        """Advance the buffer forward to the given position."""
        assert len(self.buffer) > 0
        assert self.jump_threshold > 0

        last: Record = self.buffer.peek_last()
        assert chrom == last[CHROM]
        assert pos >= last[POS_BEGIN]

        self.stats["sequential seek forward"] += 1

        for record in self._gen_from_tabix(chrom, pos, buffering=True):
            last = record
        return bool(pos >= last[POS_END])

    def _gen_from_tabix(
            self, chrom: str, pos: int | None, *_args: Any,
            from_pos: int | None = None,
            buffering: bool = True) -> Generator[Record, None, None]:
        """Read forward from the cursor, yielding records up to ``pos``.

        ``pos`` bounds the read from above: reading stops at the first record
        that begins past it, since the file is sorted by ``pos_begin`` and
        nothing after it can begin any earlier.

        ``from_pos`` bounds each record from *below*, and is optional because
        only one caller needs it.  A read that starts from a fresh
        ``pysam`` fetch does not: tabix has already excluded the records that
        end before the region.  A read that *continues* an existing cursor
        does: the cursor can sit among records lying entirely before the new
        query's start, and yielding those is how a non-overlapping record used
        to reach the caller (gain#250).  Such a record is skipped, not
        returned on -- it says nothing about the ones that follow it, whose
        ``pos_end`` need not be any smaller.
        """
        try:
            assert self.line_iterator is not None
            while True:
                record = next(self.line_iterator)
                if record is None:
                    continue
                # Buffer FIRST, then decide whether this record has run past
                # the query: the record that terminates the read must be in
                # the buffer for the next call's gap/buffer checks to work.
                if buffering:
                    self.buffer.append(record)

                if record[CHROM] != chrom:
                    return
                if pos is not None and record[POS_BEGIN] > pos:
                    return
                if from_pos is not None and record[POS_END] < from_pos:
                    continue

                self.stats["yield from tabix"] += 1
                yield record
        except StopIteration:
            pass

    def _gen_from_buffer_and_tabix(
        self, chrom: str, beg: int, end: int,
    ) -> Generator[Record, None, None]:
        """Serve ``[beg, end]`` from the buffer, then continue from the file.

        The continuation asks one question: can anything still *unread* overlap
        the query?  The file is sorted by ``pos_begin``, so every unread record
        begins at or after the last record read -- if that record already begins
        past ``end``, nothing unread can reach back into the query and the file
        need not be touched.

        This used to test the last record's ``pos_end`` instead, which answers a
        different question ("has the cursor been read past the query?") and only
        coincides with this one when records never overlap.  Where they do, a
        buffered record ending past ``end`` would cut the continuation short and
        the still-unread records overlapping the query were never read
        (gain#250).
        """
        for record in self.buffer.fetch(chrom, beg, end):
            self.stats["yield from buffer"] += 1
            yield record
        last = self.buffer.peek_last()
        if last[POS_BEGIN] > end:
            return

        yield from self._gen_from_tabix(
            chrom, end, from_pos=beg, buffering=True)

    def get_records_in_region(
        self,
        chrom: str | None = None,
        pos_begin: int | None = None,
        pos_end: int | None = None,
    ) -> Generator[Record, None, None]:
        """Yield the records overlapping the region.

        **The PAYLOAD slot is backend-dependent, and the static type does not
        say so.**  This method is inherited by
        :class:`VCFGenomicPositionTable`, whose records carry a ``(variant
        record, allele index)`` pair in the slot where a tabix record carries
        the raw tabular row.  Both are ``tuple[Any, ...]``, so the type checker
        cannot tell them apart.

        So: narrowing a table to this class with ``isinstance`` and then
        indexing ``record[PAYLOAD][i]`` for a *column* is only valid once you
        know the table is not a VCF one -- which is a question about the table,
        and the score layer asks it exactly once, when it picks the score line
        class (``GenomicScore.open``).  The five decoded slots (CHROM ... ALT)
        are safe either way: they mean the same thing in every backend, which is
        what lets the buffer and the read cascade below treat all records alike.
        """
        self.stats["calls"] += 1

        if chrom is None:
            yield from self.get_all_records()
            return

        if chrom not in self.get_chromosomes():
            logger.error(
                "chromosome %s not found in the tabix file "
                "from %s; %s",
                chrom, self.genomic_resource.resource_id, self.definition)
            raise ValueError(
                f"The chromosome {chrom} is not part of the table.")

        buffering = True
        if pos_begin is None:
            pos_begin = 1
        if pos_end is None or pos_end - pos_begin > self.BUFFER_MAXSIZE:
            buffering = False
            self.stats["without buffering"] += 1
        else:
            self.stats["with buffering"] += 1

        prev_call_chrom, prev_call_begin, prev_call_end = self._last_call
        self._last_call = chrom, pos_begin, pos_end

        # The buffer can only answer from the previous query's start onwards.
        # It is pruned to that position once the query has been served (and a
        # fresh fetch begins there), which evicts the records ending before it
        # -- so the buffer holds every record overlapping any LATER position,
        # and is missing records overlapping earlier ones.
        #
        # Its left edge does not say so.  Pruning evicts by ``pos_end``, so a
        # record that survives can begin further left than the records evicted
        # around it, leaving ``peek_first()`` pointing below the positions the
        # buffer just stopped being able to answer.  ``contains`` reads that
        # edge and would wave a backward query through onto a buffer that no
        # longer holds its records (gain#250).  The query's own start is the
        # honest watermark, so gate on it rather than on the buffer's shape.
        #
        # Eviction is also amortized (gain#287): between walks the buffer
        # knowingly holds records that already died, which only ever makes it
        # answer *more* than it must -- ``fetch`` filters exactly.
        if buffering and len(self.buffer) > 0 \
                and prev_call_chrom == chrom \
                and pos_begin >= prev_call_begin:

            first = self.buffer.peek_first()
            assert pos_end is not None
            if first[CHROM] == chrom \
               and prev_call_end is not None \
               and pos_begin > prev_call_end \
               and pos_end < first[POS_BEGIN]:

                assert first[CHROM] == prev_call_chrom
                self.stats["not found"] += 1
                return

            if self.buffer.contains(chrom, pos_begin):
                for record in self._gen_from_buffer_and_tabix(
                        chrom, pos_begin, pos_end):
                    self.stats["yield from buffer and tabix"] += 1
                    yield record

                self.buffer.prune(chrom, pos_begin)
                return

            if self._should_use_sequential_seek_forward(chrom, pos_begin):
                self._sequential_seek_forward(chrom, pos_begin)

                yield from self._gen_from_buffer_and_tabix(
                        chrom, pos_begin, pos_end)
                self.buffer.prune(chrom, pos_begin)
                return

        # without using buffer
        self.line_iterator = self.get_line_iterator(chrom, pos_begin - 1)
        yield from self._gen_from_tabix(chrom, pos_end, buffering=buffering)

    def get_line_iterator(
        self, chrom: str | None = None,
        pos_begin: int | None = None,
    ) -> Generator[Record | None, None, None]:
        """Fetch the raw rows and parse them into records.

        A row whose contig is absent from a configured chromosome map parses
        to ``None`` and is dropped by the callers, exactly as the adapter-era
        transform dropped it.
        """
        assert isinstance(self.pysam_file, pysam.TabixFile)
        assert self.parser is not None
        parser = self.parser

        if chrom is not None:
            fchrom = self.unmap_chromosome(chrom)
            if fchrom is None:
                raise ValueError(
                    f"error in mapping chromosome {chrom} to file contigs: "
                    f"{self.get_file_chromosomes()}")
        else:
            fchrom = None

        self.stats["tabix fetch"] += 1
        self.buffer.clear()

        # Yes, the argument for the chromosome/contig is called "reference".
        # ``pysam.asTuple()`` hands up one lazily-decoding row object per line;
        # it becomes the record's payload as-is.
        for raw in self.pysam_file.fetch(
            reference=fchrom, start=pos_begin, parser=pysam.asTuple(),
        ):
            yield parser(raw)

    def get_region_value_arrays(
        self,
        chrom: str,
        start: int | None,
        end: int | None,
        value_columns: Iterable[int],
        batch_size: int,
    ) -> Generator[
            tuple[np.ndarray, np.ndarray, dict[int, np.ndarray]], None, None]:
        """Yield a region's rows as column arrays, without building records.

        A fast path for a full sequential scan (statistics): the rows are read
        straight from ``pysam`` and returned per batch as the parsed one-based
        ``pos_begin``/``pos_end`` int arrays plus the raw string cells of each
        requested column index -- paying neither the per-row ``Record`` tuple
        nor the parser call.  The one-based / zero-based transform matches
        :func:`build_tabular_parser` exactly (``pos_begin += 1``, and a
        single-base zero-based interval bumps ``pos_end`` too); the contig is
        fixed by the fetch, so no per-row chromosome map is needed.

        The read starts and stops where :meth:`get_records_in_region` would:
        ``fetch`` begins at ``start - 1`` and a row whose parsed ``pos_begin``
        runs past ``end`` terminates the scan (that row and everything after
        it are not yielded), mirroring ``_gen_from_tabix``.  Records ending
        before ``start`` are still yielded here and dropped by the caller's
        clip, exactly as the per-record path drops them.
        """
        assert isinstance(self.pysam_file, pysam.TabixFile)
        fchrom = self.unmap_chromosome(chrom)
        if fchrom is None:
            raise ValueError(
                f"error in mapping chromosome {chrom} to file contigs: "
                f"{self.get_file_chromosomes()}")

        columns = list(value_columns)
        pos_begin_key = self.pos_begin_key
        pos_end_key = self.pos_end_key
        fetch_start = None if start is None else start - 1
        raw_iter = self.pysam_file.fetch(
            reference=fchrom, start=fetch_start, parser=pysam.asTuple())

        while True:
            rows = list(itertools.islice(raw_iter, batch_size))
            if not rows:
                return
            exhausted = len(rows) < batch_size

            pos_begin = np.array(
                [row[pos_begin_key] for row in rows]).astype(np.int64)
            pos_end = np.array(
                [row[pos_end_key] for row in rows]).astype(np.int64)
            if self.zero_based:
                single_base = pos_begin == pos_end
                pos_end = pos_end + single_base
                pos_begin = pos_begin + 1

            truncated = False
            if end is not None:
                past_end = pos_begin > end
                if bool(past_end.any()):
                    cut = int(np.argmax(past_end))
                    rows = rows[:cut]
                    pos_begin = pos_begin[:cut]
                    pos_end = pos_end[:cut]
                    truncated = True

            cols = {
                col: np.array([row[col] for row in rows], dtype=object)
                for col in columns
            }
            yield pos_begin, pos_end, cols

            if truncated or exhausted:
                return
