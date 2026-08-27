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

import pytest

from tests.small.genomic_resources.gene_models.columnar_formats import (
    READ_PATH_IDS,
    READ_PATHS,
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
