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

from dataclasses import replace

import pytest
from gain.genomic_resources.gene_models.parsers import (
    infer_gene_models_format,
)

from tests.small.genomic_resources.gene_models.columnar_formats import (
    BAD_NAME,
    BAD_RECORD,
    CHROM,
    DEFAULT,
    GOOD_NAME,
    READ_PATH_IDS,
    READ_PATHS,
    REFSEQ,
    ColumnarFormat,
)

#: One case per bound column per format.
FORMAT_BOUNDS = [
    (fmt, column) for fmt in READ_PATHS for column in fmt.bound_columns
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


@pytest.mark.parametrize("fmt", READ_PATHS, ids=READ_PATH_IDS)
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


@pytest.mark.parametrize("fmt", READ_PATHS, ids=READ_PATH_IDS)
def test_a_blank_transcript_name_names_the_record_by_position(
    fmt: ColumnarFormat,
) -> None:
    """The name is the other half of how a record is named.

    It used to reach serialization as the literal token ``nan``, and the
    five UCSC-derived layouts suffixed it into a transcript id of
    ``nan_1`` -- an identifier no file ever carried. Refusing the record
    is what makes that id unconstructible: the parser returns nothing at
    all, so there is no model left to carry a fabricated name.
    """
    with pytest.raises(
            ValueError,
            match=(
                f"gene models record {BAD_RECORD} at {CHROM} "
                f"has a blank {fmt.name_column} column"
            )):
        fmt.parse(fmt.file_with(fmt.name_column, ""))


@pytest.mark.parametrize("fmt", READ_PATHS, ids=READ_PATH_IDS)
def test_a_blank_strand_names_the_record(fmt: ColumnarFormat) -> None:
    """A strand is load-bearing the way a coordinate is.

    It reaches `update_frames()`, so a record with no strand does not
    merely carry an odd value -- its exon frames are computed as if from
    a strand, and the output is quietly wrong rather than missing.

    Both identifying columns are readable by the time this is reached, so
    it is reported the way the coordinate columns are.
    """
    with pytest.raises(
            ValueError,
            match=(
                f"transcript {BAD_NAME} at {CHROM} "
                f"has a blank strand column"
            )):
        fmt.parse(fmt.file_with("strand", ""))


def test_a_blank_original_transcript_name_names_the_record() -> None:
    """`trOrigId` is a transcript-name column too.

    Only gain's own format carries it, and only when the file was written
    with one; the parser fills it from `trID` otherwise. A blank cell in
    a file that does carry the column reaches serialization as the token
    `nan`, exactly as a blank `trID` would.
    """
    columns = (*DEFAULT.columns, "trOrigId")
    fields = (*DEFAULT.fields, GOOD_NAME)
    with_orig_id = replace(DEFAULT, columns=columns, fields=fields)

    with pytest.raises(
            ValueError,
            match=(
                f"transcript {BAD_NAME} at {CHROM} "
                f"has a blank trOrigId column"
            )):
        with_orig_id.parse(with_orig_id.file_with("trOrigId", ""))


@pytest.mark.parametrize("fmt", READ_PATHS, ids=READ_PATH_IDS)
def test_mismatched_exon_columns_name_the_record(
    fmt: ColumnarFormat,
) -> None:
    """A record with more exon starts than ends is malformed too.

    Every parser checked this with a bare `assert`, which carries no
    message -- and the gain#856 ledger, which renders whatever a parser
    raised, therefore offered the reader `AssertionError (no message)` as
    the reason a format was rejected.
    """
    with pytest.raises(
            ValueError,
            match=(
                f"transcript {BAD_NAME} at {CHROM} has mismatched exon "
                f"columns: exonStarts has 2, exonEnds has 1"
            )):
        fmt.parse(fmt.file_with("exonEnds", "200"))


@pytest.mark.parametrize("fmt", READ_PATHS, ids=READ_PATH_IDS)
def test_an_out_of_range_bound_names_the_record(fmt: ColumnarFormat) -> None:
    """`inf` is a coordinate pandas accepts and `int()` cannot.

    On the headerless path pandas types such a column as float, so the
    conversion raises `OverflowError` rather than `ValueError` -- past
    the guard, and back to a message naming neither record nor column,
    which is the whole of what this issue is about.
    """
    column = fmt.bound_columns[0]

    with pytest.raises(
            ValueError,
            match=(
                f"transcript {BAD_NAME} at {CHROM} has an unparsable "
                f"{column} column: 'inf'"
            )):
        fmt.parse(fmt.file_with(column, "inf"))


@pytest.mark.parametrize("fmt", READ_PATHS, ids=READ_PATH_IDS)
def test_an_unreadable_bound_keeps_its_cause(fmt: ColumnarFormat) -> None:
    """The offending token must survive the message's truncation."""
    with pytest.raises(ValueError) as excinfo:
        fmt.parse(fmt.file_with(fmt.bound_columns[0], "twelve"))

    assert excinfo.value.__cause__ is not None
    assert "twelve" in str(excinfo.value.__cause__)


@pytest.mark.parametrize("fmt", READ_PATHS, ids=READ_PATH_IDS)
def test_a_whitespace_only_cell_counts_as_blank(
    fmt: ColumnarFormat,
) -> None:
    """A cell holding only spaces names nothing either.

    This is deliberately stricter than the code was: a name of " "
    parsed before, and produced a transcript whose id was a space.
    """
    with pytest.raises(
            ValueError,
            match=f"has a blank {fmt.name_column} column"):
        fmt.parse(fmt.file_with(fmt.name_column, "   "))


@pytest.mark.parametrize("fmt", READ_PATHS, ids=READ_PATH_IDS)
def test_a_cell_that_parses_keeps_the_value_the_file_gave(
    fmt: ColumnarFormat,
) -> None:
    """Refusing a bad cell must not re-type a good one.

    The guard reads a cell to decide whether to reject it and hands back
    what it was given, so a chromosome spelled `17` keeps whatever the
    read made of it.

    That used to differ by read path: the headerless one inferred a
    numeric chromosome column as int, which is the wrong type -- a
    transcript index keyed by the int 17 is unreachable by a query for
    "17". gain#931 settled it at the read boundary, where it belonged,
    by pinning both paths to text; this now reads the same on either.
    The type itself is pinned by the cell-type suite.
    """
    numeric_chrom = fmt.parse(fmt._file(
        fmt.row_with(fmt.chrom_column, "17")))

    assert numeric_chrom is not None
    chroms = [model.chrom for model in numeric_chrom.values()]
    assert chroms == ["17"], (
        f"{fmt.name}: chromosome came back as {chroms}"
    )


@pytest.mark.parametrize(
    ("column", "value", "names_by"),
    [
        # the two identifying columns name the record by its position...
        ("chrom", "", f"record {BAD_RECORD}"),
        ("name", "", f"record {BAD_RECORD}"),
        # ...everything else by the transcript, which is readable by then
        ("strand", "", BAD_NAME),
        ("txStart", "", BAD_NAME),
        ("cdsEnd", "not-a-coordinate", BAD_NAME),
        ("exonEnds", "200", BAD_NAME),
    ],
    ids=["chrom", "name", "strand", "txStart", "cdsEnd", "exonEnds"],
)
def test_the_rejection_ledger_never_says_no_message(
    column: str, value: str, names_by: str,
) -> None:
    """The reader meets these messages through the gain#856 ledger.

    Format inference catches whatever a parser raises and reports it as
    the reason that format lost, so a message-less exception reaches the
    reader as `AssertionError (no message)` -- which reads as a bug in
    gain rather than as a malformed file. Every refusal added here has to
    survive that rendering, so it is checked there rather than only at
    the parser.
    """
    inference = infer_gene_models_format(REFSEQ.file_with(column, value))

    assert inference.file_format is None
    reason = dict(inference.rejected)["refseq"]
    assert "no message" not in reason
    assert reason.startswith("ValueError: ")
    assert names_by in reason
    assert "no message" not in inference.report()
