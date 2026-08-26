"""The six columnar gene-models layouts, as one table.

gain#907 needed the same one-line fix in all six parsers, and gain#929
needs the same guard in all six again. A test that covers one of them
proves nothing about the other five, so both suites drive every format
through this table rather than picking a representative.

The table lives here rather than in either suite because it describes the
layouts, not the defect: it is shared by the exon-position suite
(gain#907) and the load-bearing-cell suite (gain#929). Production has no
such table -- that it exists only in the tests is the observation
gain#941 is about.
"""

from collections.abc import Callable
from dataclasses import dataclass
from io import StringIO
from typing import IO

from gain.genomic_resources.gene_models.parsers import (
    parse_ccds_gene_models_format,
    parse_default_gene_models_format,
    parse_known_gene_models_format,
    parse_ref_flat_gene_models_format,
    parse_ref_seq_gene_models_format,
    parse_ucscgenepred_models_format,
)
from gain.genomic_resources.gene_models.transcript_models import (
    TranscriptModel,
)

Parser = Callable[..., dict[str, TranscriptModel] | None]

#: The transcript name carried by the well-formed row every file leads
#: with, and by the offending row that follows it. They differ so that a
#: message naming the record cannot be confused with one naming the row
#: that parsed fine.
GOOD_NAME = "NM_000546"
BAD_NAME = "NM_001126"
CHROM = "chr17"

#: Which data row the offending record is, counting the well-formed row
#: that has to come first. Messages that name a record by its position in
#: the file quote this.
BAD_RECORD = 2


@dataclass(frozen=True)
class ColumnarFormat:
    """One columnar layout: its column names, and one well-formed row."""

    name: str
    parse: Parser
    columns: tuple[str, ...]
    fields: tuple[str, ...]
    #: Where the parser puts the first exon's start. The five UCSC-derived
    #: layouts are half-open and shift by one; the default format, being
    #: gain's own output, is already in gain's coordinates.
    first_exon_start: int
    name_column: str = "name"
    chrom_column: str = "chrom"
    exon_columns: tuple[str, ...] = ("exonStarts", "exonEnds")
    #: The transcript and coding bounds, in this layout's spelling. The
    #: default format calls the transcript start `tsBeg`; the five
    #: UCSC-derived layouts call it `txStart`.
    bound_columns: tuple[str, ...] = (
        "txStart", "txEnd", "cdsStart", "cdsEnd")
    #: The default format is read by column name, so it needs its header.
    header: bool = False

    def __post_init__(self) -> None:
        assert len(self.columns) == len(self.fields), self.name

    def _row(self, fields: tuple[str, ...] | list[str]) -> str:
        return "\t".join(fields) + "\n"

    def good_row(self) -> str:
        return self._row(self.fields)

    def row_with(self, column: str, value: str) -> str:
        """The second row, named `BAD_NAME`, with one cell replaced."""
        fields = list(self.fields)
        fields[self.columns.index(self.name_column)] = BAD_NAME
        fields[self.columns.index(column)] = value
        return self._row(fields)

    def _file(self, *rows: str) -> IO:
        header = self._row(self.columns) if self.header else ""
        return StringIO(header + "".join(rows))

    def good_file(self) -> IO:
        return self._file(self.good_row())

    def file_with(self, column: str, value: str) -> IO:
        """A well-formed row first -- the column-count probe reads it to
        recognise the layout at all -- then the offending one."""
        return self._file(self.good_row(), self.row_with(column, value))


UCSC_COORDINATES = ("100", "400", "150", "350", "2", "100,300,", "200,400,")

#: refSeq and CCDS share a layout exactly; they differ only in which
#: column each takes its gene label from, which is not what is under test
#: here, so they are fed the same bytes.
REFSEQ_COLUMNS = (
    "#bin", "name", "chrom", "strand", "txStart", "txEnd", "cdsStart",
    "cdsEnd", "exonCount", "exonStarts", "exonEnds", "score", "name2",
    "cdsStartStat", "cdsEndStat", "exonFrames",
)
REFSEQ_FIELDS = (
    ("0", GOOD_NAME, CHROM, "+", *UCSC_COORDINATES,
     "0", "TP53", "cmpl", "cmpl", "0,0")
)

#: genePred is the ten-column core; knownGene is the same with two
#: trailing identifier columns, so it is spelled as that extension.
GENEPRED_COLUMNS = (
    "name", "chrom", "strand", "txStart", "txEnd", "cdsStart", "cdsEnd",
    "exonCount", "exonStarts", "exonEnds",
)
GENEPRED_FIELDS = (GOOD_NAME, CHROM, "+", *UCSC_COORDINATES)

REFSEQ = ColumnarFormat(
    "refseq", parse_ref_seq_gene_models_format,
    REFSEQ_COLUMNS, REFSEQ_FIELDS, first_exon_start=101,
)

#: gain's own output format: read by column name rather than by
#: position, named by trID/chr, and carrying a third exon column.
DEFAULT = ColumnarFormat(
    "default", parse_default_gene_models_format,
    ("chr", "trID", "gene", "strand", "tsBeg", "txEnd", "cdsStart",
     "cdsEnd", "exonStarts", "exonEnds", "exonFrames", "atts"),
    (CHROM, GOOD_NAME, "TP53", "+", "100", "400", "150", "350",
     "100,300", "200,400", "0,0", ""),
    first_exon_start=100,
    name_column="trID",
    chrom_column="chr",
    exon_columns=("exonStarts", "exonEnds", "exonFrames"),
    bound_columns=("tsBeg", "txEnd", "cdsStart", "cdsEnd"),
    header=True,
)

FORMATS = [
    REFSEQ,
    ColumnarFormat(
        "ccds", parse_ccds_gene_models_format,
        REFSEQ_COLUMNS, REFSEQ_FIELDS, first_exon_start=101,
    ),
    ColumnarFormat(
        "refflat", parse_ref_flat_gene_models_format,
        ("#geneName", *GENEPRED_COLUMNS),
        ("TP53", *GENEPRED_FIELDS), first_exon_start=101,
    ),
    ColumnarFormat(
        "knowngene", parse_known_gene_models_format,
        (*GENEPRED_COLUMNS, "proteinID", "alignID"),
        (*GENEPRED_FIELDS, "P04637", "uc002gig.1"), first_exon_start=101,
    ),
    ColumnarFormat(
        "ucscgenepred", parse_ucscgenepred_models_format,
        GENEPRED_COLUMNS, GENEPRED_FIELDS, first_exon_start=101,
    ),
    DEFAULT,
]

FORMAT_IDS = [fmt.name for fmt in FORMATS]
