import collections
from collections.abc import Generator
from functools import cache
from operator import itemgetter
from typing import IO, ClassVar, cast

from gain.genomic_resources.repository import GenomicResource

from .line import LineBase
from .record import (
    ALT,
    CHROM,
    POS_BEGIN,
    POS_END,
    REF,
    Record,
    build_tabular_parser,
)
from .table import GenomicPositionTable


class InmemoryGenomicPositionTable(GenomicPositionTable):
    """In-memory genomic position table.

    Loads the whole file into memory as immutable record tuples (the record
    contract), keyed by their reference contig.  The row->record parser is
    built once when the table is opened, since resolving the column keys and
    the chromosome map needs the header/file contigs, which are only known
    then.
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
        self._file_chromosomes: list[str] = []
        self.zero_based = table_definition.get("zero_based", False)
        super().__init__(genomic_resource, table_definition)

    def open(self) -> "InmemoryGenomicPositionTable":
        compression = None
        if self.definition.filename.endswith(".gz"):
            compression = "gzip"
        self.str_stream = self.genomic_resource.open_raw_file(
            self.definition.filename, mode="rt", compression=compression)
        assert self.str_stream is not None
        clmn_sep, strip_chars, space_replacement = \
            InmemoryGenomicPositionTable.FORMAT_DEF[self.format]
        if self.header_mode == "file":
            hcs = None
            for row in self.str_stream:
                row = row.strip(strip_chars)
                if not row:
                    continue
                hcs = row.split(clmn_sep)
                break
            if not hcs:
                raise ValueError("No header found")

            self.header = tuple(hcs)
        col_number = len(self.header) if self.header else None

        self._set_core_column_keys()

        # First read the raw rows so the file contigs are known; the
        # chromosome map -- and hence the parser -- can only be built once we
        # have them (a del_prefix/add_prefix chrom_mapping derives the
        # reference contigs from the observed file contigs).  This buffers all
        # N raw rows transiently: it is a single extra list of row pointers
        # that is freed when this method returns, leaving only records_by_chr
        # live.  A one-pass alternative is not available -- the parser cannot
        # be built before its inputs are known.
        raw_rows: list[tuple[str, ...]] = []
        file_chromosomes: list[str] = []
        seen_chromosomes: set[str] = set()
        for row in self.str_stream:
            row = row.strip(strip_chars)
            if not row:
                continue
            columns = tuple(row.split(clmn_sep))
            if col_number and len(columns) != col_number:
                raise ValueError("Inconsistent number of columns")

            col_number = len(columns)
            if space_replacement:
                columns = tuple("" if v == "EMPTY" else v for v in columns)
            raw_rows.append(columns)
            fchrom = columns[self.chrom_key]
            if fchrom not in seen_chromosomes:
                seen_chromosomes.add(fchrom)
                file_chromosomes.append(fchrom)

        self._file_chromosomes = sorted(file_chromosomes)
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

        # Sort each contig's records explicitly on the five core fields, never
        # on the whole tuple, so the opaque payload (slot PAYLOAD) is never
        # compared.
        sort_key = itemgetter(CHROM, POS_BEGIN, POS_END, REF, ALT)
        self.records_by_chr = {
            c: sorted(recs, key=sort_key)
            for c, recs in records_by_chr.items()
        }
        return self

    @cache  # pylint: disable=method-cache-max-size-none
    def get_file_chromosomes(self) -> list[str]:
        return self._file_chromosomes

    def get_all_records(self) -> Generator[LineBase, None, None]:
        for chrom in self.get_chromosomes():
            for record in self.records_by_chr.get(chrom, []):
                yield cast(LineBase, record)

    def get_records_in_region(
        self,
        chrom: str | None = None,
        pos_begin: int | None = None,
        pos_end: int | None = None,
    ) -> Generator[LineBase, None, None]:

        if chrom is None:
            yield from self.get_all_records()
            return

        if chrom not in self.records_by_chr:
            raise ValueError(
                f"The chromosome {chrom} is not present in the table")

        for record in self.records_by_chr[chrom]:
            if pos_begin and pos_begin > record[POS_END]:
                continue
            if pos_end and pos_end < record[POS_BEGIN]:
                continue
            yield cast(LineBase, record)

    def get_chromosome_length(
        self, chrom: str,
        step: int = 0,  # noqa: ARG002
    ) -> int:
        if chrom not in self.get_chromosomes():
            raise ValueError(
                f"contig {chrom} not present in the table's contigs: "
                f"{self.get_chromosomes()}")
        return cast(
            int,
            max(record[POS_END] for record in self.records_by_chr[chrom]),
        ) + 1

    def close(self) -> None:
        if self.str_stream is not None:
            self.str_stream.close()
