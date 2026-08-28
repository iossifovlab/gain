import collections
from collections.abc import Generator
from typing import IO, ClassVar, cast

from gain.genomic_resources.repository import GenomicResource
from gain.utils.fs_utils import endswith_ci

from .record import (
    CHROM,
    POS_BEGIN,
    POS_END,
    Record,
    build_tabular_parser,
    sort_key,
)
from .table import ContigExtent, GenomicPositionTable


class InmemoryGenomicPositionTable(GenomicPositionTable):
    """In-memory genomic position table.

    Loads the whole file into memory as immutable record tuples (the record
    contract), keyed by their reference contig.  The row->record parser is
    built once when the table is opened, since resolving the column keys and
    the chromosome map needs the header/file contigs, which are only known
    then.

    Empty/unknown-contig policy (consistent across the four read methods).
    A contig can be in ``get_chromosomes()`` yet have no records -- e.g. a
    ``chrom_mapping`` file that maps a reference contig onto a file contig
    with no data rows.  Such a contig is *known* but *empty*:

    * :meth:`get_all_records` skips a known-but-empty contig (yields nothing
      for it, the other contigs still stream);
    * :meth:`get_records_in_region` raises ``ValueError`` when the contig is
      not in ``get_chromosomes()``, and yields nothing for a known-but-empty
      one;
    * :meth:`find_chromosome_length` raises ``ValueError`` when the contig is
      unknown, and returns ``ContigExtent.EMPTY`` for a known-but-empty one.
      Because this backend holds the whole file, having no records PROVES the
      contig has none, which is a fact a caller can act on rather than an
      error -- so the two cases this method used to conflate are now apart
      (gain#509);
    * ``get_chromosome_length``, inherited from the base class, is the raising
      view of that hook: it still raises ``ValueError`` for the unknown *and*
      the known-but-empty contig, with the same message as before, so a caller
      of it sees no change.

    A CLOSED table is not a case of that policy and is refused ahead of it:
    ``close()`` empties ``records_by_chr`` and releases the contig list, so
    every contig would otherwise look known-but-empty and no diagnostic naming
    the table's contigs could be built at all.  That mattered for the message
    before and matters more now, because the known-but-empty answer is no
    longer an exception: a closed table falling through would report every
    contig as proven-empty, and a caller would skip the whole genome without
    being told.  Both :meth:`find_chromosome_length` and
    :meth:`_load_file_chromosomes` therefore check ``str_stream`` first and say
    the table is not open, as the other three backends do (gain#358; the
    contract is stated on :meth:`GenomicPositionTable.close`).
    """

    # This backend yields and stores records rather than line adapters.
    yields_records: ClassVar[bool] = True

    FORMAT_DEF: ClassVar[dict] = {
        # parameters are <column separator>, <strip_chars>, <space replacement>
        "mem": (None, " \t\n\r", True),
        "tsv": ("\t", "\n\r", False),
        "csv": (",", "\n\r", False),
    }

    def __init__(
        self,
        genomic_resource: GenomicResource,
        table_definition: dict,
        file_format: str,
    ):
        self.format = file_format
        self.str_stream: IO | None = None
        self.records_by_chr: dict[str, list[Record]] = {}
        self._scanned_chromosomes: list[str] = []
        self.zero_based = table_definition.get("zero_based", False)
        super().__init__(genomic_resource, table_definition)

    def _not_text_error(self) -> ValueError:
        """Describe a payload this backend was given but cannot decode.

        Left alone this surfaces as a bare ``UnicodeDecodeError`` from
        whichever row the decoder choked on, naming neither the resource, the
        file, nor the format decision that routed it here: the reader lands
        several layers below the mistake with nothing to act on.  Naming all
        three is what turns it into a one-line fix (gain#348).

        The message states the two causes and does not pick between them,
        because the backend cannot tell: a binary payload routed here by its
        suffix is the common one, but a genuinely textual file in some other
        encoding fails identically, and telling THAT reader to change the
        table's ``format:`` would be advice that cannot work.

        Raised around the row loops rather than at ``open_raw_file`` because
        decoding is lazy -- the handle opens fine and the failure only
        arrives once a row is pulled.
        """
        return ValueError(
            f"the table of resource "
            f"<{self.genomic_resource.get_full_id()}> selected the "
            f"'{self.format}' format, which reads "
            f"{self.definition.filename} as text, but that file could not be "
            f"decoded as UTF-8. Either it is a binary payload the format "
            f"auto-detection did not recognise (a bigWig, or a compressed "
            f"file) -- set an explicit 'format:' on the table, or give the "
            f"file a suffix the auto-detection knows; or it is text in "
            f"another encoding, and must be converted to UTF-8")

    def open(self) -> "InmemoryGenomicPositionTable":
        compression = None
        # Case-insensitive, like the suffix rule that routes a file here in
        # the first place (``build_genomic_position_table``, gain#348).  The
        # two are one decision split across two modules: a ``.TXT.GZ`` that
        # now resolves to this backend has to be recognised as gzipped once
        # it arrives, or it reaches the text parser as raw deflate bytes.
        if endswith_ci(self.definition.filename, ".gz"):
            compression = "gzip"
        self.str_stream = self.genomic_resource.open_raw_file(
            self.definition.filename, mode="rt", compression=compression)
        assert self.str_stream is not None
        clmn_sep, strip_chars, space_replacement = \
            InmemoryGenomicPositionTable.FORMAT_DEF[self.format]
        if self.header_mode == "file":
            hcs = None
            try:
                for row in self.str_stream:
                    row = row.strip(strip_chars)
                    if not row:
                        continue
                    hcs = row.split(clmn_sep)
                    break
            except UnicodeDecodeError as exc:
                raise self._not_text_error() from exc
            if not hcs:
                raise ValueError("No header found")

            self.header = tuple(hcs)
        col_number = len(self.header) if self.header else None

        self._set_core_column_keys()

        # Buffer the raw rows so the file contigs are known before the parser
        # is built.  This two-pass read is needed ONLY for a del_prefix /
        # add_prefix chrom_mapping, whose reverse map derives the reference
        # contigs from the observed file contigs -- so the map, and hence the
        # parser, cannot be built until the file has been scanned.  With no
        # chrom_mapping, or a chrom_mapping.filename (both give a rev_chrom_map
        # that does not depend on the file contigs), the parser could be built
        # up front and applied streaming; we keep the single code path here
        # because for an in-memory table the transient buffer is harmless (a
        # list of row pointers, freed on return, leaving only records_by_chr
        # live).  The tabix migration must NOT buffer -- see #236-#238.
        raw_rows: list[tuple[str, ...]] = []
        seen_chromosomes: set[str] = set()
        try:
            for row in self.str_stream:
                row = row.strip(strip_chars)
                if not row:
                    continue
                columns = tuple(row.split(clmn_sep))
                if col_number and len(columns) != col_number:
                    raise ValueError("Inconsistent number of columns")

                col_number = len(columns)
                if space_replacement:
                    columns = tuple(
                        "" if v == "EMPTY" else v for v in columns)
                raw_rows.append(columns)
                seen_chromosomes.add(columns[self.chrom_key])
        except UnicodeDecodeError as exc:
            raise self._not_text_error() from exc

        self._scanned_chromosomes = sorted(seen_chromosomes)
        self._build_chrom_mapping()

        parser = build_tabular_parser(
            self.chrom_key,
            self.pos_begin_key,
            self.pos_end_key,
            self.ref_key,
            self.alt_key,
            self.rev_chrom_map,
            zero_based=self.zero_based,
        )

        records_by_chr: dict[str, list[Record]] = collections.defaultdict(list)
        for columns in raw_rows:
            record = parser(columns)
            if record is None:
                # contig absent from the chromosome map -- dropped, exactly as
                # the transform does today
                continue
            records_by_chr[record[CHROM]].append(record)

        self.records_by_chr = {
            c: sorted(recs, key=sort_key)
            for c, recs in records_by_chr.items()
        }
        return self

    def _load_file_chromosomes(self) -> list[str]:
        """Return the contigs ``open()`` scanned out of the rows.

        Scanned by ``open()``, which is the only place this backend ever sees
        the file; kept under a name of its own so it is not confused with the
        base class's ``get_file_chromosomes`` memo (gain#345).

        **The guard is the stream, not the emptiness of the list.**  A closed
        table refuses this read, like its three siblings (see
        :meth:`GenomicPositionTable.close`) -- and the only signal that says so
        is the open handle, which ``open()`` establishes before it calls
        ``_build_chrom_mapping`` (the in-open call that reaches this method) and
        ``close()`` drops.  ``self._scanned_chromosomes`` cannot say it:
        ``close()`` empties it, but so does an *open* table over a file with no
        data rows, and answering that one with a ``ValueError`` -- or the closed
        one with ``[]``, which is what this used to do -- confuses two different
        states through one overloaded value (gain#358).
        """
        if self.str_stream is None:
            raise ValueError(
                f"in-memory table not open: "
                f"{self.genomic_resource.resource_id}: "
                f"{self.definition}")
        return self._scanned_chromosomes

    def get_all_records(self) -> Generator[Record, None, None]:
        # A known contig with no records (e.g. mapped onto an empty file
        # contig) is skipped -- see the class docstring's policy.
        for chrom in self.get_chromosomes():
            # A scan that outlives close() must not look like a complete,
            # shorter result set.  The contig list is evaluated once, into this
            # loop, and so survives a close; records_by_chr is re-read per
            # contig and close() empties it -- so without this check the
            # remainder of an interrupted scan yields nothing, cleanly, and the
            # caller cannot tell it from a finished one.  The stream is what
            # says the table is still usable, exactly as the handle is for the
            # bigWig backend (gain#350).  A scan *started* after close already
            # raises in get_chromosomes(), whose chrom_order close() releases.
            assert self.str_stream is not None, \
                "in-memory table closed while a scan was in flight"
            yield from self.records_by_chr.get(chrom, [])

    def get_records_in_region(
        self,
        chrom: str,
        pos_begin: int | None = None,
        pos_end: int | None = None,
    ) -> Generator[Record, None, None]:

        # An unknown contig is an error; a known-but-empty one yields nothing.
        # Probe the dict FIRST: this runs once per annotated variant, and
        # ``get_chromosomes()`` is a list -- membership in it is O(n_contigs).
        # The populated-contig case (the hot one) therefore pays a single O(1)
        # dict lookup, and only the miss falls back to the list scan to tell an
        # unknown contig from a known-but-empty one.
        records = self.records_by_chr.get(chrom)
        if records is None:
            if chrom not in self.get_chromosomes():
                raise ValueError(
                    f"The chromosome {chrom} is not present in the table")
            return

        for record in records:
            if pos_begin and pos_begin > record[POS_END]:
                continue
            if pos_end and pos_end < record[POS_BEGIN]:
                continue
            yield record

    def find_chromosome_length(
        self, chrom: str,
        step: int = 0,  # noqa: ARG002
    ) -> int | ContigExtent:
        # The closed table FIRST, for the reason gain#358 gives on the raising
        # wrapper -- close() empties records_by_chr, so every contig, populated
        # ones included, reaches the no-records branch below.  The stakes are
        # higher here than for a wrong message: that branch's answer is
        # ContigExtent.EMPTY, which is not an error, so a closed table would
        # report the whole genome as holding no records and a caller would skip
        # all of it and call the scan a success.
        if self.str_stream is None:
            raise ValueError(
                f"in-memory table not open: "
                f"{self.genomic_resource.resource_id}: "
                f"{self.definition}")
        records = self.records_by_chr.get(chrom)
        if not records:
            # An unknown contig and a known-but-empty one both arrive here with
            # nothing to take a maximum over, and they are DIFFERENT answers:
            # only a contig this table lists is PROVEN empty.  Probe the dict
            # first and fall back to the contig list only on the miss, as
            # get_records_in_region does, so the populated case stays a single
            # O(1) lookup rather than an O(n_contigs) scan.
            if chrom not in self.get_chromosomes():
                raise ValueError(
                    f"contig {chrom} not present in the table's contigs: "
                    f"{self.get_chromosomes()}")
            return ContigExtent.EMPTY
        return cast(
            int,
            max(record[POS_END] for record in records),
        ) + 1

    def close(self) -> None:
        super().close()
        if self.str_stream is not None:
            self.str_stream.close()
        self.str_stream = None
        # The whole file, held as records.  open() re-reads the raw file and
        # rebuilds this from scratch, so nothing ever read the retained copy --
        # and a closed table that keeps it costs one record per row of the file
        # for as long as anything holds it (gain#350).
        self.records_by_chr = {}
        # Scanned off the rows by open(), and re-scanned by the next one.
        self._scanned_chromosomes = []
