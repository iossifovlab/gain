"""What the read boundary hands the parsers, and what it must not.

The two branches of `parse_raw` read with different dtypes: the headered
one pins every column to `str`, the headerless one lets pandas infer.
Inference is per column and depends on the whole of it, so what a cell
becomes is not a property of the cell -- which is the root of gain#931.

Pinning the headerless branch to `str` too settles that, and settles the
typing gain#929 left behind: a chromosome column of bare digits was
handed over as the int `17`, and a transcript index keyed by the int 17
is unreachable by a query for `"17"`.

It also puts a coordinate through `int()` on its text rather than on a
number pandas already made, and those are not the same function: a
column spelled `100.0` throughout was read as a float and `int(100.0)`
kept it parsing, where `int("100.0")` does not. The bounds here are
therefore pinned by type as well as by value -- a string coordinate has
the right value and the wrong type, and no assertion about the value
would notice.
"""

from dataclasses import replace

import pytest

from tests.small.genomic_resources.gene_models.columnar_formats import (
    BAD_NAME,
    GOOD_NAME,
    OPTIONAL_CELL_CASE_IDS,
    OPTIONAL_CELLS,
    READ_PATH_IDS,
    READ_PATHS,
    REFSEQ,
    TWO_PATH_FORMATS,
    TWO_PATH_IDS,
    ColumnarFormat,
)


@pytest.mark.parametrize("fmt", READ_PATHS, ids=READ_PATH_IDS)
def test_a_transcript_bound_is_an_int(fmt: ColumnarFormat) -> None:
    """Bounds reach the model as integers, on every read path.

    A coordinate that arrives as text would compare and sort against the
    integer coordinates every other record carries, and reach the
    transcript interval index, as a string.
    """
    models = fmt.parse(fmt.good_file())
    assert models is not None
    transcript = next(iter(models.values()))

    bounds = (*transcript.tx, *transcript.cds)

    assert all(isinstance(bound, int) for bound in bounds), \
        [type(bound).__name__ for bound in bounds]


@pytest.mark.parametrize("fmt", READ_PATHS, ids=READ_PATH_IDS)
def test_a_bare_digit_chromosome_is_text(fmt: ColumnarFormat) -> None:
    """A chromosome named `17` is the text "17", not the number 17.

    Nothing pinned this column on the headerless path, so a file naming
    its chromosomes without a `chr` prefix -- Ensembl's spelling -- had
    them inferred as integers. gain#929 left this alone deliberately: a
    guard meant only to reject must not re-type a cell that parses.
    """
    models = fmt.parse(fmt.file_with_column(fmt.chrom_column, "17"))
    assert models is not None

    chroms = {transcript.chrom for transcript in models.values()}

    assert chroms == {"17"}


@pytest.mark.parametrize("fmt", READ_PATHS, ids=READ_PATH_IDS)
def test_a_bare_digit_gene_label_is_text(fmt: ColumnarFormat) -> None:
    """A gene labelled `17` is the text "17", not the number 17.

    The same defect as the chromosome above, on the axis gain#929 did
    not reach. Three layouts take their gene label from a column that is
    not the transcript name -- refSeq and genePredExt from the alternate
    name, refFlat from its leading gene-name column -- and none of those
    was pinned on the headerless path. The other three are safe only
    incidentally: their gene label *is* the transcript name, which the
    identifying pin already covered.

    An integer gene label is not merely oddly typed. It is the key of
    the gene index, so the gene it names is unreachable through a lookup
    by name, exactly as an integer chromosome was unreachable by
    location (gain#963).

    Comparing against the text rather than asserting `isinstance` pins
    the value too, and so is evidence that the table names the right
    column: `17 == "17"` is false, and a gene read from some other
    column would not equal either.

    Three of these layouts label a record by its transcript name, which
    the identifying pin already covered, so they were never going to
    fail here. They are parametrized anyway: what pins them is a pin
    made for a different reason, and this is what would notice if it
    were narrowed.
    """
    models = fmt.parse(fmt.file_with_column(fmt.gene_column, "17"))
    assert models is not None

    genes = {transcript.gene for transcript in models.values()}

    assert genes == {"17"}


@pytest.mark.parametrize("spelling", ["0", "17", "007"])
@pytest.mark.parametrize("fmt", TWO_PATH_FORMATS, ids=TWO_PATH_IDS)
def test_the_same_rows_give_the_same_gene_label_either_way(
    fmt: ColumnarFormat, spelling: str,
) -> None:
    """A header decides how the file is recognised, not what it says.

    The property the test above cannot express. Pinning the gene column
    settles its *type*, but the label a record ends up with also turned
    on that type: a gene column of `0` was falsy once inferred, so the
    layout with a fallback took the transcript name on the headerless
    path and kept `"0"` on the headered one -- two different genes from
    one set of rows, both of them strings, so no assertion about the
    type would have noticed.

    `007` is here for the other half of it: inference does not merely
    retype that cell, it loses two characters of what the file said.
    """
    headered = replace(fmt, header=True)

    plain_models = fmt.parse(fmt.file_with_column(fmt.gene_column, spelling))
    headered_models = headered.parse(
        headered.file_with_column(headered.gene_column, spelling))

    assert plain_models is not None
    assert headered_models is not None
    assert [(tm.tr_name, tm.gene) for tm in plain_models.values()] == \
        [(tm.tr_name, tm.gene) for tm in headered_models.values()]


@pytest.mark.parametrize(
    ("fmt", "column"), OPTIONAL_CELLS, ids=OPTIONAL_CELL_CASE_IDS)
def test_a_bare_digit_attribute_keeps_what_the_cell_said(
    fmt: ColumnarFormat, column: str,
) -> None:
    """An attribute spelled `007` is the text "007", not the number 7.

    `007` for the reason the gene-label test above uses it: inference
    does not merely retype the cell, it drops two of the characters the
    file carried -- and an attribute's value is written back out, where
    nothing downstream can say afterwards what was lost.

    Every read path is driven, and every attribute column including the
    symbol-valued ones that were never going to differ. Both are
    controls: the property is that a cell survives the read, not that
    one branch was taught a trick or that today's failing columns were
    special-cased.
    """
    models = fmt.parse(fmt.file_with_column(column, "007"))
    assert models is not None

    values = {tm.attributes[column] for tm in models.values()}

    assert values == {"007"}


#: Spellings pandas reads as a missing value when left to itself. A
#: gene-models column is text the file chose, so each of these is a value
#: and none of them is an absence.
MISSING_VALUE_SPELLINGS = ["NA", "NULL", "N/A", "nan", "NaN", "None"]

#: `-` is deliberately not in the list above: it is not one of pandas'
#: default `na_values`, so it survived the read before this change too.
#: It is here as the control -- if it ever started failing, the test
#: would be measuring something other than the filtering.
SURVIVES_EITHER_WAY = "-"


@pytest.mark.parametrize(
    "spelling", [*MISSING_VALUE_SPELLINGS, SURVIVES_EITHER_WAY])
def test_a_cell_spelling_a_missing_value_keeps_what_it_said(
    spelling: str,
) -> None:
    """`NA` is the text "NA", and is not the same as a blank cell.

    pandas took each of `MISSING_VALUE_SPELLINGS` for a missing value
    and handed back the same float `NaN`, so the record could not say
    `NULL` and be heard, and an error message quoting the cell could not
    tell any of them from a genuinely empty one -- which is what
    `cell_text`'s docstring recorded as unfixable while the read still
    filtered them.
    """
    fmt = REFSEQ
    models = fmt.parse(fmt.file_with("score", spelling))
    assert models is not None

    scores = {tm.tr_name: tm.attributes["score"] for tm in models.values()}

    assert scores[BAD_NAME] == spelling
    assert scores[GOOD_NAME] == "0"


@pytest.mark.parametrize("fmt", READ_PATHS, ids=READ_PATH_IDS)
def test_a_float_spelled_bound_reads_as_the_same_coordinate(
    fmt: ColumnarFormat,
) -> None:
    """`100.0` and `100` are the same coordinate, however read.

    A column spelled with a trailing `.0` throughout used to be inferred
    as a float on the headerless path, where `int(100.0)` kept it
    parsing; the headered path pinned it to text, where `int("100.0")`
    does not. So the same file parsed or failed depending on which
    branch recognised it -- and reading both as text would have made it
    fail on both.

    Comparing the two spellings against each other rather than against a
    literal keeps this indifferent to the half-open shift the
    UCSC-derived layouts apply and the default format does not.
    """
    column = fmt.bound_columns[0]

    plain = fmt.parse(fmt.file_with_column(column, "100"))
    floated = fmt.parse(fmt.file_with_column(column, "100.0"))

    assert plain is not None
    assert floated is not None
    assert [tm.tx for tm in floated.values()] == \
        [tm.tx for tm in plain.values()]


@pytest.mark.parametrize("fmt", READ_PATHS, ids=READ_PATH_IDS)
@pytest.mark.parametrize("spelling", ["100.7", "0.5", "-3.9"])
def test_a_fractional_bound_names_the_record(
    fmt: ColumnarFormat, spelling: str,
) -> None:
    """A coordinate between two bases is not a coordinate.

    Accepting `100.0` must not mean accepting `100.7`. Truncating it
    would put the record a base away from where the file said, silently
    -- and reading a bound is exactly where gain#856, gain#907 and
    gain#929 all agreed the reader has to be told which record is wrong.
    """
    column = fmt.bound_columns[0]

    with pytest.raises(
            ValueError,
            match=f"has an unparsable {column} column: '{spelling}'"):
        fmt.parse(fmt.file_with_column(column, spelling))
