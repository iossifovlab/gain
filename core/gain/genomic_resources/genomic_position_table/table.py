from __future__ import annotations

import abc
from collections.abc import Generator
from types import TracebackType
from typing import ClassVar, cast

from box import Box

from gain import logging
from gain.genomic_resources.repository import GenomicResource

from .record import Record

logger = logging.getLogger(__name__)


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

    CHROM = "chrom"
    POS_BEGIN = "pos_begin"
    POS_END = "pos_end"
    REF = "reference"
    ALT = "alternative"

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
        self.chrom_order = self.get_file_chromosomes()
        if "chrom_mapping" in self.definition:
            mapping = self.definition.chrom_mapping
            if "filename" in mapping:
                self.chrom_map = {}
                self.chrom_order = []
                with self.genomic_resource.open_raw_file(
                        mapping["filename"], "rt") as infile:
                    hcs = infile.readline().strip("\n\r").split("\t")
                    if hcs != ["chrom", "file_chrom"]:
                        raise ValueError(
                            f"The chromosome mapping file "
                            f"{mapping['filename']} in resource "
                            f"{self.genomic_resource.get_id()} is "
                            f"expected to have the two columns "
                            f"'chrom' and 'file_chrom'")
                    for line in infile:
                        chrom, fchrom = line.strip("\n\r").split("\t")
                        assert chrom not in self.chrom_map
                        self.chrom_map[chrom] = fchrom
                        self.chrom_order.append(chrom)
                    assert len(set(self.chrom_map.values())) == \
                        len(self.chrom_map)
            else:
                chromosomes = self.chrom_order
                new_chromosomes: list[str] = chromosomes

                if "del_prefix" in mapping:
                    pref = mapping.del_prefix
                    new_chromosomes = [
                        ch.removeprefix(pref)
                        for ch in new_chromosomes
                    ]

                if "add_prefix" in mapping:
                    pref = mapping.add_prefix
                    new_chromosomes = [
                        f"{pref}{chrom}" for chrom in new_chromosomes]
                self.chrom_map = dict(
                    zip(new_chromosomes, chromosomes, strict=True))
                self.chrom_order = new_chromosomes
            self.rev_chrom_map = {
                fch: ch for ch, fch in self.chrom_map.items()}

    def get_column_key(self, col: str) -> int | None:
        """Find the index of a column in the table."""
        if col in self.definition:
            if "index" in self.definition[col]:
                self.definition[col]["column_index"] = \
                    self.definition[col]["index"]
                logger.debug(
                    "%s: Using 'index' to configure columns is outdated,"
                    " use 'column_index' instead.",
                    self.genomic_resource.get_full_id(),
                )
            if "name" in self.definition[col]:
                self.definition[col]["column_name"] = \
                    self.definition[col]["name"]
                logger.debug(
                    "%s: Using 'name' to configure columns is outdated,"
                    " use 'column_name' instead.",
                    self.genomic_resource.get_full_id(),
                )

            if "column_index" in self.definition[col]:
                return cast(int, self.definition[col]["column_index"])
            if "column_name" in self.definition[col]:
                assert self.header is not None
                col_index = self.header.index(
                    self.definition[col]["column_name"])
                self.definition[col]["column_index"] = col_index
                return col_index
        if self.header is not None and col in self.header:
            return self.header.index(col)
        return None

    def _set_core_column_keys(self) -> None:
        # chrom is the first column by default (index 0)
        self.chrom_key = self.get_column_key(self.CHROM) or 0

        # pos_begin is the second column by default (index 1)
        self.pos_begin_key = self.get_column_key(self.POS_BEGIN) or 1

        key = self.get_column_key(self.POS_END)
        if key is not None:
            self.pos_end_key = key
        else:
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
        not about tidiness: closed tables are deliberately kept alive.
        ``_INMEMORY_CNV_CACHE`` holds ``CnvCollection`` scores process-wide
        while an annotation pipeline's teardown closes them, so whatever a
        closed table retains is retained for the life of the process -- and
        retained for nothing, since ``open()`` rebuilds all of it from the file
        rather than reusing it (gain#350).

        A closed table stays **reopenable**: ``open()`` re-establishes
        everything released here, and answers exactly as a table that was never
        closed.  Until it is reopened it **refuses the reads that depend on
        what it read out of the file** -- that is the contract, and it is what
        releasing the state above amounts to at the call site:
        :meth:`get_chromosomes` raises once ``chrom_order`` is released,
        :meth:`get_file_chromosomes` raises on every backend (each guarding the
        handle its ``open()`` establishes and this ``close()`` drops), and
        ``get_chromosome_length`` and the record reads go the same way.  This
        paragraph used to claim the opposite -- that reading a closed table was
        unchanged -- which was never true of the code it documents (gain#358).
        No in-tree caller reads a table it has not opened: every read sits
        behind ``GenomicScore.is_open()``, and the asserts in the fetch paths
        (``assert self._bw_file is not None``) are there for the other case, a
        scan already in flight when the close lands.

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
        chrom: str | None = None,
        pos_begin: int | None = None,
        pos_end: int | None = None,
    ) -> Generator[Record, None, None]:
        """Return an iterable over the records in the specified range.

        The interval is closed on both sides and 1-based.
        """

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
    def get_chromosome_length(
            self, chrom: str, step: int = 100_000_000) -> int:
        """Return the length of a chromosome (or contig).

        Returned value is guarnteed to be larget than the actual contig length.
        """

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
    # and get_chromosome_length both call this -- and an instance attribute
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
        shape its four siblings use (``"<backend> table not open: <resource
        id>: <definition>"``).  That is the closed-table contract stated where
        a backend meets it: the memo in front of this method caches whatever it
        returns, so a backend that answers a closed table with what is left of
        its released state hands that answer out for the rest of the table's
        life -- and an empty contig list is a legitimate answer from an OPEN
        table, so it cannot double as the refusal (gain#358; see
        :meth:`close`).
        """
