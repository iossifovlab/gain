"""The six columnar gene-models layouts, as one table.

gain#907 needed the same one-line fix in all six parsers, gain#929 the
same guard in all six again, and gain#931 the same read for all of them
a third time. A test that covers one layout proves nothing about the
others, so every suite drives the whole table rather than picking a
representative.

The table lives here rather than in any one suite because it describes
the layouts, not the defect: it is shared by the exon-position suite
(gain#907), the load-bearing-cell suite (gain#929) and the cell-type and
optional-cell suites (gain#931). Production has no such table -- that it
exists only in the tests is the observation gain#941 is about.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
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
    #: The columns this layout copies straight into
    #: `TranscriptModel.attributes`, and so out through serialization. A
    #: blank one is not load-bearing -- the record parses without it --
    #: which is exactly why what a blank one serializes as went unnoticed
    #: (gain#931). The three layouts that carry none leave this empty.
    optional_columns: tuple[str, ...] = ()

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

    def file_with_column(self, column: str, value: str) -> IO:
        """Both rows carrying `value` in `column`.

        What dtype a read infers is a property of the whole column, so a
        test about the inferred type has to spell every cell of it the
        same way: one odd cell leaves the column an object column, which
        is a different read from the one under test.
        """
        fields = list(self.fields)
        fields[self.columns.index(column)] = value
        second = list(fields)
        second[self.columns.index(self.name_column)] = BAD_NAME
        return self._file(self._row(fields), self._row(second))


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

#: refSeq and CCDS copy these six out of the record and into the model's
#: attributes; `score`, `#bin` and `exonCount` are spelled as bare digits,
#: which is what lets one blank cell re-type the whole column.
REFSEQ_OPTIONAL = (
    "#bin", "score", "exonCount", "cdsStartStat", "cdsEndStat", "exonFrames",
)

REFSEQ = ColumnarFormat(
    "refseq", parse_ref_seq_gene_models_format,
    REFSEQ_COLUMNS, REFSEQ_FIELDS, first_exon_start=101,
    optional_columns=REFSEQ_OPTIONAL,
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
        optional_columns=REFSEQ_OPTIONAL,
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
        optional_columns=("proteinID", "alignID"),
    ),
    ColumnarFormat(
        "ucscgenepred", parse_ucscgenepred_models_format,
        GENEPRED_COLUMNS, GENEPRED_FIELDS, first_exon_start=101,
    ),
    # genePredExt: the same parser's second attempt, five columns wider.
    # Those five are the ones it copies into attributes, so the layout
    # the narrow entry above covers has no optional cell at all.
    ColumnarFormat(
        "ucscgenepredext", parse_ucscgenepred_models_format,
        (*GENEPRED_COLUMNS, "score", "name2", "cdsStartStat",
         "cdsEndStat", "exonFrames"),
        (*GENEPRED_FIELDS, "0", "TP53", "cmpl", "cmpl", "0,0"),
        first_exon_start=101,
        optional_columns=("score", "cdsStartStat", "cdsEndStat",
                          "exonFrames"),
    ),
    DEFAULT,
]

FORMAT_IDS = [fmt.name for fmt in FORMATS]

#: The same layouts read through the other branch of `parse_raw`. A
#: headerless file is recognised by counting columns and a headered one
#: by matching their names -- two separate reads, so a guard proven on
#: one is not proven on the other. The default format is already read by
#: name and has no second path.
HEADERED_FORMATS = [
    replace(fmt, name=f"{fmt.name}-headered", header=True)
    for fmt in FORMATS if fmt is not DEFAULT
]

#: Every layout on every read path.
READ_PATHS = [*FORMATS, *HEADERED_FORMATS]
READ_PATH_IDS = [fmt.name for fmt in READ_PATHS]

#: The read paths whose layout carries optional cells. Both branches of
#: `parse_raw` are here deliberately: they are separate code, recognising
#: the file by different means, so what a cell becomes on one says
#: nothing about the other.
OPTIONAL_CELL_PATHS = [fmt for fmt in READ_PATHS if fmt.optional_columns]

#: One (layout, column) pair per optional cell, so a failure names the
#: column rather than reporting the layout as a whole.
OPTIONAL_CELLS = [
    (fmt, column)
    for fmt in OPTIONAL_CELL_PATHS
    for column in fmt.optional_columns
]
OPTIONAL_CELL_CASE_IDS = [
    f"{fmt.name}-{column}" for fmt, column in OPTIONAL_CELLS
]
