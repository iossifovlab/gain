from __future__ import annotations

import abc
import enum
from collections.abc import Generator, Iterable
from types import TracebackType
from typing import ClassVar, cast

import numpy as np
from box import Box

from gain import logging
from gain.genomic_resources.repository import GenomicResource

from .record import Record

logger = logging.getLogger(__name__)


class ContigExtent(enum.Enum):
    """Why a backend has no length to report for a contig.

    The return of :meth:`GenomicPositionTable.find_chromosome_length` when
    there is no number to give.  The two members are NOT interchangeable, and
    that is the whole reason this type exists: a caller that splits a contig
    into regions treats them oppositely (gain#509).

    Which member a backend can return is a property OF THE BACKEND, not of the
    contig -- which is what the caller used to encode as an ``isinstance``
    ladder over concrete table classes:

    * a backend holding the whole file (in-memory) can PROVE a contig has no
      records, and never has to guess a length for one that does -- so it
      returns ``EMPTY`` and never ``UNDETERMINED``;
    * a backend reading lengths out of a header (bigWig) always has an exact
      length for a contig it lists, and returns neither;
    * a backend probing an index (tabix, VCF) only indexes contigs that HAVE
      records, so it never sees an empty one, and its probe can fail on a
      contig that is not -- so it returns ``UNDETERMINED`` and never ``EMPTY``.

    Neither member is a failure.  A caller that asked wrongly -- a closed
    table, a contig the table does not list -- gets ``ValueError`` instead, and
    that split is the contract: an exception means *the question was bad*, a
    member means *the question was fine and the answer is not a number*.
    """

    EMPTY = enum.auto()
    """The backend PROVED the contig holds no records.

    There is nothing to read, so nothing to split and nothing to validate.
    """

    UNDETERMINED = enum.auto()
    """No length is available, and the contig may well hold records.

    Distinct from ``EMPTY`` because those records still have to be read: a
    length is what SPLITTING a contig needs, not what READING one needs.
    """


class GenomicPositionTable(abc.ABC):
    """Abstraction over genomic scores table."""

    # Whether get_all_records/get_records_in_region yield records -- the plain
    # six-slot tuples of the record contract (see ``record.py``).  Every
    # in-tree backend does, and every one of them overrides this to True.  The
    # False below is the base class's starting value, NOT a supported steady
    # state for a backend: since #239 removed the line adapters and the
    # ``ScoreLine`` that read them, there is no second line shape left for a
    # False to select.
    #
    # So the flag's remaining job is to catch a new backend that has not
    # migrated.  ``GenomicScore.open`` routes on it -- ``RecordScoreLine`` when
    # it is True, and a ``TypeError`` refusing to open the score when it is
    # False, rather than route the table to a score line that would misread
    # whatever it does yield.  (A VCF table is routed to ``VCFScoreLine`` ahead
    # of this check, by type; it sets the flag too, inheriting the tabix
    # backend's True.)  A backend author overrides this to True *and* yields
    # records -- the claim and the yielded shape are held together by
    # test_backend_record_contract.py, which fails a backend that leaves it
    # False as much as one whose records do not match its claim.
    yields_records: ClassVar[bool] = False

    # Whether this backend serves :meth:`get_region_value_arrays` -- the bulk
    # column-array region read that never builds a record.  Unlike
    # ``yields_records`` this one has a real False state: it is an optional
    # fast path, and a backend that does not implement it is in no way broken.
    #
    # It exists because the capability is NOT answerable from the class alone.
    # ``VCFGenomicPositionTable`` subclasses ``TabixGenomicPositionTable`` and
    # so *inherits* its implementation, but cannot honour the contract: a VCF
    # record's PAYLOAD is ``(variant, allele index)`` rather than a raw row,
    # and a VCF score addresses its column by INFO *name*, not by the integer
    # payload index the arrays contract passes.  So the VCF backend sets this
    # back to False explicitly -- the one declaration that replaces the
    # ``isinstance(Tabix) and not isinstance(VCF)`` every caller used to have
    # to know to write.
    #
    # A backend sets this True *and* implements the method; the two are held
    # together by test_backend_record_contract.py, which fails a backend whose
    # claim and behaviour disagree in either direction.
    supports_value_arrays: ClassVar[bool] = False

    # Whether :meth:`find_chromosome_length` answers are EXACT contig
    # lengths (an ``int``, or a raise) rather than probed upper bounds.
    # A caller that needs a true denominator -- e.g. a coverage fraction
    # -- may only trust a backend that declares this; the tabix probe's
    # answer is guaranteed LARGER than the actual length and stays False.
    chrom_lengths_are_exact: ClassVar[bool] = False

    CHROM = "chrom"
    POS_BEGIN = "pos_begin"
    POS_END = "pos_end"
    REF = "reference"
    ALT = "alternative"

    # The spellings a column definition may address its column by, the two
    # deprecated ones included.  :meth:`get_column_key` implements what each
    # one MEANS; this names them, for the one caller that has to know whether
    # a definition addresses a column without yet being able to resolve it.
    COLUMN_KEY_SPELLINGS: ClassVar[tuple[str, ...]] = (
        "index", "column_index", "name", "column_name")

    def __init__(
            self, genomic_resource: GenomicResource, table_definition: dict):
        self.genomic_resource = genomic_resource

        self.definition = Box(table_definition)
        self.chrom_map: dict[str, str] | None = None
        self.chrom_order: list[str] | None = None
        self.rev_chrom_map: dict[str, str] | None = None

        # Per-instance memo for get_file_chromosomes; see that method for why
        # it is not a functools cache.  Reset by _build_chrom_mapping, so a
        # table reopened over changed data re-reads its contigs.
        self._file_chromosomes: list[str] | None = None

        self.chrom_key: int
        self.pos_begin_key: int
        self.pos_end_key: int
        self.ref_key: int | None = None
        self.alt_key: int | None = None

        self.header: tuple | None = None

        self.header_mode = self.definition.get("header_mode", "file")
        if self.header_mode == "list":
            self.header = tuple(self.definition.header)
            for hindex, hcolumn in enumerate(self.header):
                if not isinstance(hcolumn, str):
                    raise TypeError(
                        f"The {hindex}-th header {hcolumn} in the table "
                        f"definition is not a string.")
        elif self.header_mode in {"file", "none"}:
            self.header = None
        else:
            raise ValueError(
                f"The 'header_mode' property in a table definition "
                f"must be 'file' [by default], 'none', or 'list'."
                f" The current value {self.header_mode}"
                f"does not meet these requirements.")

    def _build_chrom_mapping(self) -> None:
        self.chrom_map = None
        # Called from every backend's open(), and so the point at which a
        # reopened table must forget what the previous open() read.
        self._file_chromosomes = None
        file_chromosomes = self.get_file_chromosomes()
        self.chrom_order = file_chromosomes
        if "chrom_mapping" not in self.definition:
            return

        mapping = self.definition.chrom_mapping
        if "filename" in mapping:
            self.chrom_map = self._read_chrom_mapping_file(mapping)
        else:
            self.chrom_map = self._build_prefix_chrom_mapping(
                mapping, file_chromosomes)
        self.chrom_order = list(self.chrom_map)
        self.rev_chrom_map = {
            fch: ch for ch, fch in self.chrom_map.items()}

    def _read_chrom_mapping_file(self, mapping: Box) -> dict[str, str]:
        """Read the ``chrom -> file_chrom`` map the resource ships as a file.

        The map must be a bijection: a chromosome listed twice states two
        file contigs for one chromosome, and two chromosomes on one file
        contig make ``rev_chrom_map`` -- the map read backwards, which is
        what turns a record's file contig back into a chromosome -- lose one
        of them.  Both are user-supplied configuration, so both raise.
        """
        chrom_map: dict[str, str] = {}
        owner_of: dict[str, str] = {}
        with self.genomic_resource.open_raw_file(
                mapping["filename"], "rt") as infile:
            hcs = infile.readline().strip("\n\r").split("\t")
            if hcs != ["chrom", "file_chrom"]:
                raise ValueError(
                    f"{self._mapping_file_prefix(mapping)} "
                    f"expected to have the two columns "
                    f"'chrom' and 'file_chrom'")
            for line in infile:
                chrom, fchrom = line.strip("\n\r").split("\t")
                if chrom in chrom_map:
                    raise ValueError(
                        f"{self._mapping_file_prefix(mapping)} "
                        f"expected to list each chromosome once; "
                        f"{chrom!r} is listed more than once")
                if fchrom in owner_of:
                    raise ValueError(
                        f"{self._mapping_file_prefix(mapping)} "
                        f"expected to map each chromosome onto a distinct "
                        f"file chromosome; {owner_of[fchrom]!r} and "
                        f"{chrom!r} are both mapped onto {fchrom!r}")
                owner_of[fchrom] = chrom
                chrom_map[chrom] = fchrom
        return chrom_map

    def _mapping_file_prefix(self, mapping: Box) -> str:
        """Open every mapping-file complaint the same way.

        Names the file and the resource it belongs to, and ends on ``is`` so
        each caller appends only what it found wrong.
        """
        return (
            f"The chromosome mapping file {mapping['filename']} "
            f"in resource {self.genomic_resource.get_id()} is")

    def _build_prefix_chrom_mapping(
        self, mapping: Box, file_chromosomes: list[str],
    ) -> dict[str, str]:
        """Derive the ``chrom -> file_chrom`` map from the prefix transforms.

        Applies ``del_prefix`` and then ``add_prefix``, and requires the
        composed transform to be injective on THIS file's contigs.
        ``removeprefix`` strips only where the prefix is present, so on a
        file mixing prefixed and unprefixed contigs it collides two of them
        onto one name; the map would then keep whichever came last and
        silently drop the other's records.  A following ``add_prefix``
        renames such a collision but does not undo it, which is why the
        check runs on the final names rather than after either transform.
        """
        chromosomes: list[str] = file_chromosomes

        if "del_prefix" in mapping:
            pref = mapping.del_prefix
            chromosomes = [ch.removeprefix(pref) for ch in chromosomes]

        if "add_prefix" in mapping:
            pref = mapping.add_prefix
            chromosomes = [f"{pref}{chrom}" for chrom in chromosomes]

        chrom_map: dict[str, str] = {}
        for chrom, fchrom in zip(
                chromosomes, file_chromosomes, strict=True):
            if chrom in chrom_map:
                raise ValueError(
                    f"The chromosome mapping in resource "
                    f"{self.genomic_resource.get_id()} maps the file "
                    f"chromosomes {chrom_map[chrom]!r} and {fchrom!r} onto "
                    f"the same chromosome {chrom!r}")
            chrom_map[chrom] = fchrom
        return chrom_map

    def would_resolve_column(self, col: str) -> bool:
        """Whether ``col`` will have a key once this table is open.

        The question :meth:`get_column_key` answers, asked of a table that
        may not be open yet -- and asked HERE, because which spellings
        address a column, and that a bare header column counts as
        addressing one, is this class's knowledge and not its callers'.

        With a header in hand -- ``header_mode: list`` names it in the
        config, and an opened table has read it -- this IS
        :meth:`get_column_key`, so the two cannot disagree.  Without one,
        the config is the only evidence there is, and the answer is whether
        the definition addresses the column at all: a ``col_def`` carrying
        none of :attr:`COLUMN_KEY_SPELLINGS` is not an address, and
        ``get_column_key`` would fall past it to the header fallback and
        resolve ``None``.  Answering on the mere PRESENCE of the block would
        promise a column an empty ``reference:`` never delivers.
        """
        if self.header is not None:
            return self.get_column_key(col) is not None
        col_def = self.definition.get(col)
        return col_def is not None and any(
            spelling in col_def for spelling in self.COLUMN_KEY_SPELLINGS)

    def get_column_key(self, col: str) -> int | None:
        """Find the index of a column in the table.

        Reads the definition; never writes to it.  The resolved index used to
        be memoised back as ``definition[col]["column_index"]`` (and the
        deprecated ``index``/``name`` spellings canonicalised there the same
        way), but a table's definition is configuration that outlives this
        call and is read by more than this table:
        ``GenomicScoreImplementation.calc_statistics_hash`` serialises it.
        Writing to it made a resource's statistics hash depend on whether its
        score had been opened in the current process -- and ``repo-repair``
        computes that hash on both sides of the rebuild it is deciding, in a
        process that has opened the score and in one that has not.  Every
        fragment score in the deployed GRR was rebuilt on every run because of
        it (#502).
        """
        col_def = self.definition.get(col)
        if col_def is not None:
            if "index" in col_def:
                logger.debug(
                    "%s: Using 'index' to configure columns is outdated,"
                    " use 'column_index' instead.",
                    self.genomic_resource.get_full_id(),
                )
                return cast(int, col_def["index"])
            if "column_index" in col_def:
                return cast(int, col_def["column_index"])
            if "name" in col_def:
                logger.debug(
                    "%s: Using 'name' to configure columns is outdated,"
                    " use 'column_name' instead.",
                    self.genomic_resource.get_full_id(),
                )
                assert self.header is not None
                return self.header.index(col_def["name"])
            if "column_name" in col_def:
                assert self.header is not None
                return self.header.index(col_def["column_name"])
        if self.header is not None and col in self.header:
            return self.header.index(col)
        return None

    def _set_core_column_keys(self) -> None:
        # A resolved key of 0 is a column, not a missing one: ``or`` cannot
        # tell the FIRST column from ``get_column_key``'s ``None``, and used
        # to discard a pos_begin configured at index 0 in favour of the
        # default 1 -- silently reading start positions out of the wrong
        # column (#240).  Only ``is None`` asks the question these defaults
        # mean to ask.

        # chrom is the first column by default (index 0)
        key = self.get_column_key(self.CHROM)
        self.chrom_key = key if key is not None else 0

        # pos_begin is the second column by default (index 1)
        key = self.get_column_key(self.POS_BEGIN)
        self.pos_begin_key = key if key is not None else 1

        key = self.get_column_key(self.POS_END)
        if key is not None:
            self.pos_end_key = key
        else:
            # Reachable, though it reads as dead: a null-valued 'index:' /
            # 'column_index:' (an empty YAML value) is returned AS the key,
            # so get_column_key can answer None with pos_end named in the
            # header -- which is the one way past the check above.
            if self.header and self.POS_END in self.header:
                self.pos_end_key = 2
            else:
                self.pos_end_key = self.pos_begin_key

        self.ref_key = self.get_column_key(self.REF)
        self.alt_key = self.get_column_key(self.ALT)

    def __enter__(self) -> GenomicPositionTable:
        self.open()
        return self

    def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc_value: BaseException | None,
            exc_tb: TracebackType | None) -> None:
        self.close()

    @abc.abstractmethod
    def open(self) -> GenomicPositionTable:
        pass

    def close(self) -> None:
        """Close the file and release everything read out of it.

        THE RELEASE POLICY, for every backend: **after ``close()`` a table
        holds only what ``open()`` does not rebuild** -- its resource, its
        definition, and its configured parameters (the header when it is
        configured rather than read from the file, and the core column keys
        resolved from it).  Everything derived from the open file is given up:
        the handle, the parser built around the file's header and contigs, any
        buffered or fully-loaded records, and the chromosome state below.

        Stated once, here, because the alternative is deciding it per field --
        and per field the answer always looks like "this one is small".  It is
        not about tidiness: a closed table is not necessarily a dropped one.
        A holder may keep a score, and so its table, long after closing it --
        an annotation pipeline holds its scores for a whole run -- so whatever
        a closed table retains is retained for as long as that holder lives,
        and retained for nothing, since ``open()`` rebuilds all of it from the
        file rather than reusing it.

        A closed table stays **reopenable**: ``open()`` re-establishes
        everything released here, and answers exactly as a table that was never
        closed.  Until it is reopened it **refuses the reads that depend on
        what it read out of the file** -- that is the contract, and it is what
        releasing the state above amounts to at the call site.  Four of those
        reads refuse in one stated way, ``ValueError``, on all four backends:
        :meth:`get_chromosomes` once ``chrom_order`` is released, and
        :meth:`get_file_chromosomes` and :meth:`find_chromosome_length` off the
        handle their ``open()`` establishes and this ``close()`` drops -- plus
        :meth:`get_chromosome_length`, which refuses by relaying what the hook
        beneath it raises.  Those four are what a caller may write an
        ``except ValueError`` around.  The hook is the one that must guard, and
        its stakes are the higher: an unguarded closed table would reach its
        no-records branch and answer ``ContigExtent.EMPTY``, which is not an
        error at all (gain#509).

        **The record reads refuse too, but not in one way, and their exception
        type is not part of the contract.**  Neither ``get_all_records`` nor
        ``get_records_in_region`` carries a not-open guard of its own: measured
        on a closed table, some backend/method pairs raise the same
        ``ValueError`` on their way through :meth:`get_chromosomes`, and the
        rest run into a pre-existing ``assert`` in the fetch path
        (``assert self._bw_file is not None``, ``assert isinstance(
        self.pysam_file, pysam.TabixFile | pysam.VariantFile)``,
        ``assert self.parser is not None``) and hand the caller a message-less
        ``AssertionError`` -- or, under ``python -O`` which strips asserts,
        whatever the next line makes of the released state (``AttributeError``
        on ``None``, ``KeyError`` off an emptied contig dict).  Those asserts
        are there for a different case, a scan already in flight when the close
        lands; do not catch on them.  This whole paragraph used to claim the
        opposite of all of it -- that reading a closed table was unchanged --
        which was never true of the code it documents (gain#358).  No in-tree
        caller reads a table it has not opened: every read sits behind
        ``GenomicScore.is_open()``.

        **The one read that does not refuse is chromosome mapping, and it is
        left that way deliberately.**  :meth:`map_chromosome` and
        :meth:`unmap_chromosome` return their argument unchanged when
        ``rev_chrom_map``/``chrom_map`` are ``None`` -- which is how a table
        that configures no ``chrom_mapping`` answers, and is exactly the state
        this method leaves behind.  So a closed *mapped* table passes
        reference-space names through as if they were the file's, silently, and
        nothing left on the table can tell the two apart:
        :meth:`_build_chrom_mapping` sets ``chrom_map = None`` on an OPEN table
        with no mapping configured, so the field does not distinguish closed
        from mapping-free, and there is no open/closed flag to consult.  Adding
        one was considered and rejected (gain#358): it is an invariant every
        backend would have to maintain, bought at the price of a new way for
        the read path to fail -- which is what the release policy above set out
        not to introduce.  Recorded rather than fixed, here and in this
        package's ``__init__`` ledger, so that a reader who finds a closed
        table mapping a name through knows it is a decision and not an
        oversight.

        Released here is the base class's own file-derived state: the
        ``get_file_chromosomes`` memo and the chromosome mapping
        :meth:`_build_chrom_mapping` derives from it, which that method rebuilds
        -- memo included -- on every ``open()``.  **A backend's ``close()`` must
        call up into this one**; what each backend releases on top of it is its
        own, and ``test_table_lifetime.py`` holds all four to the policy: it
        opens a table, *reads* through it, closes it, and then requires both
        that everything the open rebound was given up and that nothing the
        closed table still holds has anything in it -- the second of which is
        what catches a container filled in place, and the read is what reaches
        the buffers a fetch establishes.
        """
        self.chrom_map = None
        self.chrom_order = None
        self.rev_chrom_map = None
        self._file_chromosomes = None

    @abc.abstractmethod
    def get_all_records(self) -> Generator[Record, None, None]:
        """Return generator of all records in the table."""

    @abc.abstractmethod
    def get_records_in_region(
        self,
        chrom: str,
        pos_begin: int | None = None,
        pos_end: int | None = None,
    ) -> Generator[Record, None, None]:
        """Return an iterable over the records in the specified range.

        The interval is closed on both sides and 1-based.  ``pos_begin`` and
        ``pos_end`` are optional and default to the contig's own bounds;
        ``chrom`` is **required**.

        It used to be optional, and passing ``None`` meant "every record in
        the table" -- each backend opened with ``if chrom is None: yield from
        self.get_all_records()``.  That made the default argument list a
        legal call (``get_records_in_region()``) that quietly scanned a whole
        genome, and it gave one method two jobs whose only shared code was
        the delegation.  :meth:`get_all_records` is that second job, is not
        going anywhere, and says what it does in its name.  Callers that
        passed no contig call it directly instead.

        **A caller may stop iterating at any point.**  A backend that carries
        state between queries must therefore release it from a ``finally``
        rather than after the yield loop, which an abandoned generator never
        reaches: what it retains may not grow with the number of abandoned
        reads, and the reads that follow must answer as though none had been
        abandoned.  :meth:`buffered_record_count` is how a backend reports
        what it is holding, and
        ``test_backend_record_contract.py`` holds every backend to both
        halves (gain#1120).
        """

    def buffered_record_count(self) -> int:
        """How many records this table is holding from PREVIOUS reads.

        Not the table's contents, and not the size of the region last read:
        the records retained *between* queries, which is the quantity a lazy
        consumer can make grow without bound by walking away from its reads.

        Zero is the honest answer for a backend that carries nothing across
        queries, and that is most of them -- an in-memory table holds every
        one of its records and buffers none of them, so it reports zero while
        holding the lot.  Only :class:`TabixGenomicPositionTable`, which
        serves what it can from a warm ``LineBuffer``, has anything to
        report.

        It exists to be *asserted on*: without it the contract test would
        have to reach into a backend's internals and know which ones have a
        buffer, which is the ``isinstance(Tabix)`` that the capability
        declarations on this class exist to replace.
        """
        return 0

    def get_region_value_arrays(
        self,
        chrom: str,  # ruff: ignore[unused-method-argument]
        start: int | None,  # ruff: ignore[unused-method-argument]
        end: int | None,  # ruff: ignore[unused-method-argument]
        value_columns: Iterable[int],  # ruff: ignore[unused-method-argument]
        batch_size: int,  # ruff: ignore[unused-method-argument]
    ) -> Generator[
            tuple[np.ndarray, np.ndarray, dict[int, np.ndarray]], None, None]:
        """Yield a region's rows as column arrays, without building records.

        The region bounds are named ``start``/``end`` rather than the
        ``pos_begin``/``pos_end`` of :meth:`get_records_in_region`, because an
        implementation of this method builds ``pos_begin``/``pos_end`` *arrays*
        in its body -- the scalar bounds need names that do not shadow them.

        An OPTIONAL fast path: a backend that serves it sets
        :attr:`supports_value_arrays` and overrides this; the base refuses.
        Ask before calling -- do not probe by catching the exception.

        Each batch is ``(pos_begin, pos_end, {column index: raw cells})``: the
        parsed one-based position arrays, plus the raw cells of each requested
        payload column.  Cells are NOT parsed and rows are NOT clipped to the
        region -- both stay with the caller, exactly as on the record path.
        ``batch_size`` is a hint a backend may ignore when its read granularity
        is fixed by its own windowing.
        """
        raise TypeError(
            f"{type(self).__name__} does not serve get_region_value_arrays "
            f"(resource {self.genomic_resource.resource_id}); it leaves "
            f"supports_value_arrays False -- check that before calling.")

    def get_chromosomes(self) -> list[str]:
        """Return list of contigs in the genomic position table."""
        if self.chrom_order is None:
            raise ValueError(
                f"genomic table not open: "
                f"{self.genomic_resource.resource_id}: "
                f"{self.definition}")
        assert self.chrom_order is not None
        return self.chrom_order

    def _map_file_chrom(self, chrom: str) -> str:
        """Transfrom chromosome name to the chromosomes from score file."""
        if self.chrom_map:
            return self.chrom_map[chrom]
        return chrom

    def map_chromosome(self, chromosome: str) -> str | None:
        """Map a file contig to its reference genome chromosome.

        The inverse of :meth:`unmap_chromosome`.  Returns ``None`` when the
        table configures a ``chrom_mapping`` that does not cover ``chromosome``,
        and ``chromosome`` unchanged when it configures none.
        """
        if self.rev_chrom_map is not None:
            if chromosome in self.rev_chrom_map:
                return self.rev_chrom_map[chromosome]
            return None

        return chromosome

    def unmap_chromosome(self, chromosome: str) -> str | None:
        """Map a reference genome chromosome to its file contig.

        The inverse of :meth:`map_chromosome`.  Named for what it undoes: the
        mapping a caller sees is reference-facing, so *un*\\ mapping goes back
        to the file's own name -- which is why every caller spells the result
        ``fchrom``.  Returns ``None`` when the table configures a
        ``chrom_mapping`` that does not cover ``chromosome``, and ``chromosome``
        unchanged when it configures none.
        """
        if self.chrom_map is not None:
            if chromosome in self.chrom_map:
                return self.chrom_map[chromosome]
            return None

        return chromosome

    @abc.abstractmethod
    def find_chromosome_length(
            self, chrom: str,
            step: int = 100_000_000) -> int | ContigExtent:
        """Return the length of a contig, or why there is not one.

        The hook every backend implements; :meth:`get_chromosome_length` is
        built on it.  A returned length is guaranteed to be LARGER than the
        actual contig length -- callers rely on that to split a contig into
        regions without dropping its tail.

        Returns a :class:`ContigExtent` member instead of a number when the
        backend has no length to give, and the member says WHY: ``EMPTY`` when
        the backend can prove the contig holds no records, ``UNDETERMINED``
        when a length simply could not be established and the contig may hold
        records after all.  A caller that splits contigs into regions must treat
        those two oppositely -- skip the first, read the second whole -- which
        is why this hook reports them apart rather than collapsing both into
        ``None`` (gain#509).

        Raises ``ValueError`` when the QUESTION is bad rather than the answer
        absent: a table that is not open, or a contig not in
        :meth:`get_chromosomes`.  Implementations must guard the closed table
        FIRST, before any read that a closed table refuses -- including
        ``get_chromosomes()``, which the contig-naming diagnostics interpolate
        (gain#358).
        """

    def get_chromosome_length(
            self, chrom: str, step: int = 100_000_000) -> int:
        """Return the length of a chromosome (or contig).

        Returned value is guarnteed to be larget than the actual contig length.

        The raising view of :meth:`find_chromosome_length`, for the callers --
        most of them -- that have nothing useful to do with a contig whose
        length is unavailable and want to be told rather than handed a value
        they must classify.  Concrete here rather than per backend so the two
        cannot drift: every reason the hook has no number becomes a
        ``ValueError``, whichever backend produced it, and a new backend gets
        this behaviour by implementing the hook alone.
        """
        length = self.find_chromosome_length(chrom, step)
        # The two members are named apart rather than collapsed into one "no
        # length" message, because they are different facts about the resource
        # and an operator reading a failed statistics build acts on them
        # differently: an empty contig is usually a chrom_mapping naming
        # something the file does not carry, while an undetermined length is a
        # probe that could not answer for a contig that may well hold records.
        # Both name the contig asked about and the contigs the table does have
        # -- reads that are safe here, because the hook has already refused a
        # closed table.
        if length is ContigExtent.EMPTY:
            raise ValueError(
                f"contig {chrom} has no records in the table's contigs: "
                f"{self.get_chromosomes()}")
        if length is ContigExtent.UNDETERMINED:
            raise ValueError(
                f"could not determine the length of contig {chrom} "
                f"in the table's contigs: {self.get_chromosomes()}")
        return length

    # Memoised PER INSTANCE, and deliberately not with functools.cache: that
    # decorator keeps its memo on the class-level function object and keys it
    # by the call arguments, self included, so it is a strong reference to
    # every table it is ever called on -- held for the life of the process,
    # with no eviction.  _build_chrom_mapping calls this from every backend's
    # open(), so it pinned EVERY table that was ever opened, and a whole-genome
    # `grr_manage resource-repair` opens one per region task (gain#345).
    #
    # It also bought nothing there: each task builds a fresh table, so a
    # class-level memo keyed by self never saw a hit.  What a memo is actually
    # for here is the repeated calls WITHIN one table's life -- get_chromosomes
    # and find_chromosome_length both call this -- and an instance attribute
    # serves those and dies with the instance.
    #
    # Pinned by test_table_lifetime.py, which asserts both that a closed and
    # dropped table is collected and that no table method carries a
    # class-level memo at all.
    def get_file_chromosomes(self) -> list[str]:
        """Return the chromosomes in the table file, in the file's own order.

        The result is cached for the lifetime of the open table; reopening
        re-reads it.
        """
        if self._file_chromosomes is None:
            self._file_chromosomes = self._load_file_chromosomes()
        return self._file_chromosomes

    @abc.abstractmethod
    def _load_file_chromosomes(self) -> list[str]:
        """Read the chromosomes out of the table file.

        This is to be overwritten by the subclass. It should return a list of
        the chromosomes in the file in the order determinted by the file.

        Called at most once per open table -- :meth:`get_file_chromosomes`
        holds the result -- so an implementation may read the open handle
        directly and need not memoise on its own.

        **An implementation with no open handle raises ``ValueError``**, in the
        shape the four in-tree backends use (``"<backend> table not open:
        <resource id>: <definition>"``).  That is the closed-table contract
        stated where a backend meets it: the memo in front of this method
        caches whatever it returns, so a backend that answers a closed table
        with what is left of its released state hands that answer out for the
        rest of the table's life -- and an empty contig list is a legitimate
        answer from an OPEN table, so it cannot double as the refusal
        (gain#358; see :meth:`close`).
        """
