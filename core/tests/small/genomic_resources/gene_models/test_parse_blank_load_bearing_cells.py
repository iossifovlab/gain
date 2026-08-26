# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""A blank load-bearing cell is refused, and the record is named.

gain#907 gave the *exon* columns a guard that names its record. This is
the rest of the class (gain#929): the columns that identify a record and
the four transcript/coding bounds.

Two failures live here, and they are not the same one:

* The default format put its bounds into the model with no conversion at
  all, so a blank cell became a transcript starting at ``NaN`` -- no
  error, no warning, wrong coordinates.
* The five UCSC-derived layouts wrapped theirs in ``int()``, so a blank
  cell did raise -- but as a bare ``cannot convert float NaN to
  integer``, naming neither the record nor the column, which the gain#856
  inference ledger then reports as the reason a format was rejected.

Both are covered for every format, because the offending lines are
copies of one another (gain#941).
"""

import pytest

from tests.small.genomic_resources.gene_models.columnar_formats import (
    BAD_NAME,
    BAD_RECORD,
    CHROM,
    FORMAT_IDS,
    FORMATS,
    ColumnarFormat,
)

#: One case per bound column per format.
FORMAT_BOUNDS = [
    (fmt, column) for fmt in FORMATS for column in fmt.bound_columns
]
FORMAT_BOUND_IDS = [f"{fmt.name}-{column}" for fmt, column in FORMAT_BOUNDS]


@pytest.mark.parametrize(
    ("fmt", "column"), FORMAT_BOUNDS, ids=FORMAT_BOUND_IDS)
def test_a_blank_transcript_bound_names_the_record(
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
def test_a_blank_chromosome_names_the_record_by_position(
    fmt: ColumnarFormat,
) -> None:
    """A record with no chromosome is not placed anywhere.

    It used to be accepted: the chromosome went into the model as a float
    ``NaN``, which keys the transcript index all by itself, so the record
    became unreachable by every location query -- absent from annotation
    rather than visibly wrong.

    The chromosome is half of how the other messages name a record, so
    this one falls back to the record's position in the file, and adds
    the transcript name because that much is readable.
    """
    with pytest.raises(
            ValueError,
            match=(
                f"gene models record {BAD_RECORD} "
                f"\\(transcript {BAD_NAME}\\) "
                f"has a blank {fmt.chrom_column} column"
            )):
        fmt.parse(fmt.file_with(fmt.chrom_column, ""))


@pytest.mark.parametrize("fmt", FORMATS, ids=FORMAT_IDS)
def test_a_blank_transcript_name_names_the_record_by_position(
    fmt: ColumnarFormat,
) -> None:
    """The name is the other half of how a record is named.

    It used to reach serialization as the literal token ``nan``, and the
    five UCSC-derived layouts suffixed it into a transcript id of
    ``nan_1`` -- an identifier no file ever carried.
    """
    with pytest.raises(
            ValueError,
            match=(
                f"gene models record {BAD_RECORD} at {CHROM} "
                f"has a blank {fmt.name_column} column"
            )):
        fmt.parse(fmt.file_with(fmt.name_column, ""))


@pytest.mark.parametrize("fmt", FORMATS, ids=FORMAT_IDS)
def test_no_transcript_id_is_invented_from_a_blank_name(
    fmt: ColumnarFormat,
) -> None:
    """The id, not just the message, is what this is protecting.

    Naming the record in an error is worth little if the record is built
    anyway; nothing the parser returns may carry an id derived from a
    cell the file left empty.
    """
    try:
        transcript_models = fmt.parse(fmt.file_with(fmt.name_column, "")) or {}
    except ValueError:
        transcript_models = {}

    assert [tr_id for tr_id in transcript_models if "nan" in tr_id] == []
