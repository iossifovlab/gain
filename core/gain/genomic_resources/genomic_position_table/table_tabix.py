from __future__ import annotations

import itertools
from collections import Counter
from collections.abc import Generator, Iterable
from typing import Any, ClassVar, cast

import numpy as np
import pysam

from gain import logging
from gain.genomic_resources.repository import (
    GenomicResource,
    resolve_tabix_index_filename_for_read,
)
from gain.genomic_resources.resource_errors import (
    index_column_mismatch_error,
)
from gain.utils.regions import get_chromosome_length_tabix

from .index_columns import (
    INDEX_HEADER_SIZE,
    parse_index_columns,
)
from .line import LineBuffer
from .record import (
    CHROM,
    POS_BEGIN,
    POS_END,
    Record,
    TabularParser,
    build_tabular_parser,
)
from .table import ContigExtent, GenomicPositionTable

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

    # Serves the bulk column-array read; see get_region_value_arrays below.
    # NOT inherited in spirit by the VCF backend, which sets it back to False.
    supports_value_arrays: ClassVar[bool] = True

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

    @property
    def index_filename(self) -> str | None:
        """The index this table's definition configures, if any.

        ``None`` means "not configured" -- the protocol then resolves the
        index of ``filename`` itself (manifest first, probe second; see
        ``resolve_tabix_index_filename_for_read``).  A table opens its file
        at more than one site (the VCF backend opens it a second time to
        read the file's contigs off the index), and every one of them must
        use the SAME index, so the key is read here rather than at each
        open (gain#596).
        """
        return cast("str | None", self.definition.get("index_filename", None))

    def _opened_index_filename(self) -> str:
        """Name the index the handle was opened against.

        ``index_filename`` is what the definition configures and what
        ``open_tabix_file`` was handed: where it is set, that is the index the
        handle uses, and only where it is ``None`` does the protocol resolve
        one from the sibling suffixes -- which this repeats, on the same
        resource.  Re-deriving a name from the data file instead would
        validate a different index, or one that is not there.
        """
        configured = self.index_filename
        if configured is not None:
            return configured
        return resolve_tabix_index_filename_for_read(
            self.genomic_resource, self.definition.filename)

    def _decline_index_check(
        self, index_filename: str, reason: str,
    ) -> None:
        """Say out loud that the check did not happen.

        Declined rather than passed: an index this table has not checked is
        not an index it has agreed with, and a resource that goes unchecked
        must say so.  One message for every way the check can fail to run, so
        that a reader cannot tell them apart by tone and conclude that one of
        them was somehow less unchecked than the other.
        """
        logger.warning(
            "the columns of index %s of resource <%s> could not be read "
            "(%s), so the table's configured columns are NOT validated "
            "against them",
            index_filename, self.genomic_resource.get_full_id(), reason)

    def _validate_index_columns(self) -> None:
        """Refuse the table when its index was built over other columns.

        The check is **best-effort**: a check that cannot be *performed* --
        the index cannot be read, or its bytes carry no column configuration
        to decode -- declines out loud and opens the table unvalidated, and
        only a check that ran and *disagreed* refuses.  Both failures say the
        same thing, which is nothing about the resource and everything about
        the check, so both reach the same outcome; refusing on one and warning
        on the other was an accident of where the read sat rather than a
        policy (gain#628).

        The index is what a region query is *filtered* by; the resolved column
        keys are what the records it returns are *read* through.  Where the two
        disagree the disagreement is invisible at read time -- a record the
        index says overlaps the query is fetched and then dropped by the score
        layer, with no warning, no error and no count -- and a statistics scan
        over such a table reports success for a region it never covered
        (gain#553).  So it is refused here, before a record is read, rather
        than being left to a reader to notice.

        The comparison is against the *resolved* column keys and not against
        the raw configuration: a ``pos_end`` that was never configured still
        resolves to a column (see ``_set_core_column_keys``), and can still
        disagree with the index.  Which is also why the message says the
        configuration *resolves to* a column rather than that it *states* one:
        the resolution mixes explicit config, deprecated spellings, a header
        lookup and hardcoded defaults, and a coordinate nothing states is read
        through a hardcoded fallback just as surely as one that is spelled
        out.  A fallback that disagrees with the index is the same read-time
        split as a contradiction is -- the index filters on one column, the
        table reads another -- so it is refused on the same terms.

        Both directions of disagreement are refused, not just the one that
        loses records.  An index spanning MORE than the configured record only
        over-fetches, but it is the same resource defect, it is as cheap to
        detect, and under ADR 0008 a scan that completes over such a table is
        what vouches for it.
        """
        index_filename = self._opened_index_filename()
        try:
            with self.genomic_resource.open_raw_file(
                    index_filename, mode="rb", compression="gzip") as infile:
                header = infile.read(INDEX_HEADER_SIZE)
        except Exception as error:  # ruff: ignore[blind-except]
            # The one step here that can fail for reasons that have nothing to
            # do with the resource: a reset, a throttle or a 5xx against an
            # http or s3 GRR, or a truncated read.  Refusing on it would
            # refuse a resource whose index htslib has *just* loaded in full,
            # one open above -- the bytes were readable a moment ago, and will
            # be again.
            #
            # Broad on purpose, and only around the fetch.  ``OSError`` alone
            # does not span what the transports raise: measured against this
            # repo's own protocols, a 404, a 403 and every 5xx arrive from
            # ``fsspec`` as ``FileNotFoundError`` and ``s3fs`` translates even
            # an unrecognised code to ``IOError``, but a connection reset PART
            # WAY THROUGH the read arrives as ``aiohttp.ClientPayloadError``,
            # which descends from ``Exception`` -- the ``ConnectionResetError``
            # is only its ``__context__``.  Narrowing to ``OSError`` would
            # therefore still refuse a resource for a mid-read reset, the very
            # failure this policy exists to stop being fatal.  The table layer
            # has no business importing aiohttp or botocore to name those
            # types, so it declines on anything the fetch raises instead
            # (gain#628).
            #
            # The decode and the comparison stay OUTSIDE this, so a defect in
            # either still raises rather than being logged away as a decline.
            self._decline_index_check(index_filename, str(error))
            return
        columns = parse_index_columns(header)
        if columns is None:
            self._decline_index_check(
                index_filename, "no column configuration to decode")
            return

        mismatches = [
            (name, indexed, configured)
            for name, indexed, configured in (
                (self.CHROM, columns.chrom, self.chrom_key),
                (self.POS_BEGIN, columns.pos_begin, self.pos_begin_key),
                (self.POS_END, columns.pos_end, self.pos_end_key),
            )
            if indexed != configured
        ]
        if mismatches:
            # The "no end column" note belongs to ``pos_end`` alone; a
            # ``chrom`` disagreement on such an index says nothing about it.
            end_is_implied = columns.end_is_implied and any(
                name == self.POS_END for name, _, _ in mismatches)
            raise index_column_mismatch_error(
                self.genomic_resource.get_full_id(), index_filename,
                mismatches, end_is_implied=end_is_implied)

    def open(self) -> TabixGenomicPositionTable:
        self.pysam_file = self.genomic_resource.open_tabix_file(
            self.definition.filename, self.index_filename)
        try:
            if self.header_mode == "file":
                self.header = self._load_header()
            self._set_core_column_keys()
            self._validate_index_columns()
        except Exception:
            # The handle is this method's to release: nothing above has been
            # told the table is open -- ``close()`` would not be called on it
            # -- so a raise from here without this would leak the file, and on
            # the http and s3 protocols the connection under it.
            self.close()
            raise
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

    def find_chromosome_length(
            self, chrom: str,
            step: int = 100_000_000) -> int | ContigExtent:
        # The closed table FIRST, as the raising wrapper does it: without this
        # the probe runs against a released handle and the caller gets a
        # message-less AttributeError off None instead of being told which
        # resource was read after closing (gain#358).
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
            # UNDETERMINED rather than a raise: the contig IS listed (checked
            # just above), so the caller asked a fair question -- it is the
            # chrom_mapping that cannot answer it.  From this method's only
            # in-tree caller the case is close to unreachable, since the mapping
            # is what produces the contig list in the first place; treating it
            # as undetermined means a contig that turns out to hold records is
            # read rather than dropped, which is slow at worst, never wrong.
            return ContigExtent.UNDETERMINED
        length = get_chromosome_length_tabix(self.pysam_file, fchrom, step)
        if length is None:
            # UNDETERMINED, never EMPTY: a tabix index only carries contigs
            # that HAVE records, so this backend is never in a position to
            # prove one holds none.  The records are still there to be read.
            return ContigExtent.UNDETERMINED
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
        chrom: str,
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
        #
        # ONE ``try/finally`` around the whole buffered branch, rather than one
        # per path.  A consumer is under no obligation to finish reading, and
        # the prune used to sit after each path's yield loop, which a caller
        # that stopped part-way never reached: ``GeneratorExit`` is raised at
        # the suspended yield, the query's records were never evicted, and the
        # buffer grew by a query's worth on every abandoned read (gain#1120).
        #
        # Wrapping the branch and not the paths is deliberate.  A per-path
        # ``finally`` is one a later path can be written without: the paths
        # here alternate over a scan -- 199 buffer hits to 200 sequential
        # seeks, measured -- so whichever one still prunes keeps the buffer
        # bounded on behalf of the one that does not, and a scan-shaped test
        # passes either way.  There is nothing to forget if there is one exit.
        #
        # It also covers the two exits that had no prune and want none: the
        # ``not found`` return above (every buffered record begins past
        # ``pos_end``, so none of them ends before ``pos_begin`` and the prune
        # drops nothing) and the fall-through to the unbuffered read below
        # (which clears the buffer as its first act).  Both are no-ops, which
        # is what makes the single exit safe as well as tidy.
        #
        # Pruning after an *exception* is new too, and safe for the same
        # reason it is safe at all: ``prune`` drops only the records that can
        # no longer match ``pos_begin`` or later, so it can retain dead
        # records but never lose live ones.
        #
        # What this rests on: the ``finally`` runs when the generator is
        # CLOSED or DROPPED, and CPython calls ``close()`` at refcount zero.
        # That reaches here even through the generator expression
        # ``ScoreFilter.select`` wraps a filtered read in: a genexp does not
        # propagate ``close()`` to what it iterates, but it and
        # ``fetch_records``' own frame are the only things holding us, and
        # they are torn down together.  A reference CYCLE is what defers it,
        # to a cyclic GC pass that is not guaranteed to come; nothing in tree
        # builds one, and a caller that does gets the old unbounded behaviour
        # back.
        #
        # ``_prune_if_current`` rather than a bare prune, because the caller
        # also controls *when* the close happens: see there.
        if buffering and len(self.buffer) > 0 \
                and prev_call_chrom == chrom \
                and pos_begin >= prev_call_begin:

            try:
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
                    return

                if self._should_use_sequential_seek_forward(chrom, pos_begin):
                    # The seek is where the whole run-up to the query gets
                    # buffered, so a raise in it is the moment the prune helps
                    # most -- another exit the single wrapper covers for free.
                    self._sequential_seek_forward(chrom, pos_begin)
                    yield from self._gen_from_buffer_and_tabix(
                            chrom, pos_begin, pos_end)
                    return
            finally:
                self._prune_if_current(chrom, pos_begin, pos_end)

        # without using buffer
        #
        # No prune here, and none is wanted -- not even a ``finally`` one.
        # This path re-seeks the file, and ``get_line_iterator`` CLEARS the
        # buffer before the first record is read, so what this read leaves
        # behind is ONE query's worth rather than an accumulation: abandoning
        # it retains no more than draining it does, which is the whole of
        # what gain#1120 asks of a read.
        #
        # A prune to ``pos_begin`` would also be close to a no-op, since
        # ``prune`` evicts by ``pos_end`` and tabix has already excluded the
        # records ending before the region.  (Not because the records all
        # BEGIN at or after ``pos_begin`` -- the fetch starts at
        # ``pos_begin - 1`` and a record overlapping the region can begin far
        # to its left.)
        #
        # What this path does not promise is a bound in RECORDS.
        # ``BUFFER_MAXSIZE`` caps the query's width in base pairs, and a
        # dense enough table can hold many more records than that in the
        # window -- and an unbuffered read (``buffering=False``) lands here
        # too, appending nothing at all.
        self.line_iterator = self.get_line_iterator(chrom, pos_begin - 1)
        yield from self._gen_from_tabix(chrom, pos_end, buffering=buffering)

    def _prune_if_current(
        self, chrom: str, pos_begin: int, pos_end: int | None,
    ) -> None:
        """Evict the dead records, unless another query has moved us on.

        A query prunes to its own start once it has been served.  That is
        sound while the prune runs in query order -- the buffered paths are
        gated on ``pos_begin >= prev_call_begin``, so every later query starts
        at or after the position pruned to, and a backward query re-fetches
        instead.

        A ``finally`` breaks that ordering, because the caller decides when
        the generator is closed.  Hold a read at 500 open, run a backward
        query at 200 (which re-seeks and refills the buffer), then close the
        held generator: its prune lands on the *new* buffer with the *old*
        query's ``pos_begin``, evicting records at 200 that are live.  The
        answer at 205 then silently loses a record -- ``contains`` still
        admits it, because a wide record keeps ``_max_end`` past it, and
        gain#250's fix gates on the query's start rather than on that edge.

        So the prune is conditional on this generator still being the table's
        current reader.  A stale one evicts nothing, which costs a query's
        worth of retained records exactly once -- the buffer stays bounded,
        because whichever query IS current prunes on its own way out.

        The late direction needs no guard and gets none: a prune to a position
        the table has already passed evicts *less* than the current query
        would, never more.  It is the held-open-across-a-backward-query
        direction that loses records, and the token catches both.

        **This does not make interleaved region reads supported, and must not
        be read as saying so.**  A table serves ONE live region read at a
        time: ``line_iterator`` is the table's cursor, and a second query
        replaces it, so a held generator may be CLOSED across another query --
        which is what this guard is for -- but not resumed.  Resuming one
        reads from wherever the other query left the cursor, and that was
        true before this guard and is unchanged by it.
        """
        if self._last_call == (chrom, pos_begin, pos_end):
            self.buffer.prune(chrom, pos_begin)

    def buffered_record_count(self) -> int:
        """The records held in the ``LineBuffer`` between queries.

        The one backend with a non-zero answer.  It over-reports by design:
        eviction is amortized (gain#287), so between walks the buffer
        knowingly holds records that are already dead.  What the contract
        asks of this number is that it stay bounded across a scan, not that
        it equal the live set on any given query.
        """
        return len(self.buffer)

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
