# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""A columnar record whose exon-position column is blank names the record.

The columnar formats each read their exon positions by splitting the cell
on commas. pandas delivers a blank cell as a float ``NaN``, so the split
escaped as a bare ``AttributeError`` naming a float -- the columnar half
of gain#907. Every format is covered here: the offending line was copied
between the parsers, so fixing one proved nothing about the rest.

The layouts themselves live in `columnar_formats`, shared with the
load-bearing-cell suite (gain#929).
"""

import pytest

from tests.small.genomic_resources.gene_models.columnar_formats import (
    BAD_NAME,
    CHROM,
    FORMAT_IDS,
    FORMATS,
    REFSEQ,
    ColumnarFormat,
)

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
