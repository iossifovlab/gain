# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""A columnar record whose exon-position column is blank names the record.

The columnar formats each read their exon positions by splitting the cell
on commas. pandas delivers a blank cell as a float ``NaN``, so the split
escaped as a bare ``AttributeError`` naming a float -- the columnar half
of gain#907. Every format is covered here: the offending line was copied
between the parsers, so fixing one proved nothing about the rest.
"""

from collections.abc import Callable
from dataclasses import dataclass
from io import StringIO
from typing import IO

import pytest
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
    exon_columns: tuple[str, ...] = ("exonStarts", "exonEnds")
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
    # gain's own output format: read by column name rather than by
    # position, named by trID/chr, and carrying a third exon column.
    ColumnarFormat(
        "default", parse_default_gene_models_format,
        ("chr", "trID", "gene", "strand", "tsBeg", "txEnd", "cdsStart",
         "cdsEnd", "exonStarts", "exonEnds", "exonFrames", "atts"),
        (CHROM, GOOD_NAME, "TP53", "+", "100", "400", "150", "350",
         "100,300", "200,400", "0,0", ""),
        first_exon_start=100,
        name_column="trID",
        exon_columns=("exonStarts", "exonEnds", "exonFrames"),
        header=True,
    ),
]

FORMAT_IDS = [fmt.name for fmt in FORMATS]

#: One case per exon column per format -- the default format has three.
FORMAT_COLUMNS = [
    (fmt, column) for fmt in FORMATS for column in fmt.exon_columns
]
FORMAT_COLUMN_IDS = [
    f"{fmt.name}-{column}" for fmt, column in FORMAT_COLUMNS
]


@pytest.mark.parametrize(
    ("fmt", "column"), FORMAT_COLUMNS, ids=FORMAT_COLUMN_IDS)
def test_a_blank_exon_position_cell_names_the_record(
    fmt: ColumnarFormat, column: str,
) -> None:
    with pytest.raises(
            ValueError,
            match=(
                f"transcript {BAD_NAME} at {CHROM} has an unparsable "
                f"{column} column: ''"
            )):
        fmt.parse(fmt.file_with(column, ""))


@pytest.mark.parametrize("fmt", FORMATS, ids=FORMAT_IDS)
def test_a_well_formed_file_still_parses(fmt: ColumnarFormat) -> None:
    """Without this the guard above could be rejecting everything.

    It also pins the layouts themselves: a row this test could not parse
    would make the guard tests pass for the wrong reason.
    """
    transcript_models = fmt.parse(fmt.good_file())

    assert transcript_models is not None
    assert [
        model.exons[0].start for model in transcript_models.values()
    ] == [fmt.first_exon_start]


@pytest.mark.parametrize("fmt", FORMATS, ids=FORMAT_IDS)
def test_an_unreadable_exon_position_cell_names_the_record(
    fmt: ColumnarFormat,
) -> None:
    """A cell that is present but not a coordinate list is the same case.

    It leaves the reader exactly as stuck as a blank one -- the bare
    ``invalid literal for int()`` names no record either -- so the guard
    reports both, quoting back what the file actually said.
    """
    column = fmt.exon_columns[0]

    with pytest.raises(
            ValueError,
            match=(
                f"transcript {BAD_NAME} at {CHROM} has an unparsable "
                f"{column} column: 'not,coordinates,'"
            )):
        fmt.parse(fmt.file_with(column, "not,coordinates,"))


def test_a_long_unreadable_cell_is_truncated_but_keeps_its_cause() -> None:
    """The message reaches a log line and the inference ledger.

    A transcript has one coordinate per exon, so quoting the cell whole
    would put hundreds of them into both. The truncation is what the
    surrounding module already does with quoted file text; the ``int``
    failure stays on the exception chain so that the offending token is
    not what gets truncated away.
    """
    long_cell = ",".join(str(position) for position in range(200)) + ",oops,"

    with pytest.raises(ValueError) as excinfo:
        REFSEQ.parse(REFSEQ.file_with("exonStarts", long_cell))

    assert len(str(excinfo.value)) < len(long_cell)
    assert "oops" in str(excinfo.value.__cause__)
