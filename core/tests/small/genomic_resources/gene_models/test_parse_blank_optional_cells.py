"""A blank optional cell is carried faithfully, and carries nothing else.

An optional cell is one the record parses without: the six refSeq/CCDS
columns copied straight into `TranscriptModel.attributes`, and
knownGene's two identifier columns. Because a blank one does not stop the
parse, what it *became* went unnoticed -- and it became two things it
should not have (gain#931):

* pandas delivers a blank cell as a float `NaN`, which reaches
  serialization as `str(nan)` and is written as the literal token `nan` --
  a value the source file never carried, which parses back as the string
  `"nan"`.
* A single blank re-types the whole column. The bare-digit columns are
  read as integers, so one blank floats them, and a *different*,
  well-formed record's `0` is serialized as `0.0`.

The second is why this is not cosmetic: a record's output depends on
whether some other row was blank.

Both read paths of `parse_raw` are covered. They read with different
dtypes, so the defect does not present identically on them: the headered
path pins `dtype=str` and so never floats a column, yet still writes
`nan`.
"""

import pytest
from gain.genomic_resources.gene_models.default_attributes import (
    format_default_attributes,
    parse_default_attributes,
)
from gain.genomic_resources.gene_models.transcript_models import (
    TranscriptModel,
)

from tests.small.genomic_resources.gene_models.columnar_formats import (
    BAD_NAME,
    GOOD_NAME,
    OPTIONAL_CELL_CASE_IDS,
    OPTIONAL_CELLS,
    ColumnarFormat,
)


def transcript_named(
    models: dict[str, TranscriptModel], name: str,
) -> TranscriptModel:
    """The parsed record carrying `name`, whatever id the layout gave it.

    The UCSC-derived layouts suffix an occurrence counter onto the
    transcript name to build the id, so the id is not the name.
    """
    matching = [tm for tm in models.values() if tm.tr_name == name]
    assert len(matching) == 1, f"{name} not parsed exactly once"
    return matching[0]


def serialized_attribute(
    models: dict[str, TranscriptModel], name: str, column: str,
) -> str:
    """What this record's `column` reads back as, once serialized.

    Serializing and re-reading is what the value has to survive, and it
    is how a fabricated `nan` reaches the next reader as a string.
    """
    attributes = transcript_named(models, name).attributes
    return parse_default_attributes(
        format_default_attributes(attributes))[column]


@pytest.mark.parametrize(
    ("fmt", "column"), OPTIONAL_CELLS, ids=OPTIONAL_CELL_CASE_IDS)
def test_a_blank_optional_cell_is_not_serialized_as_nan(
    fmt: ColumnarFormat, column: str,
) -> None:
    """The file said nothing, so the output must not say `nan`."""
    models = fmt.parse(fmt.file_with(column, ""))
    assert models is not None

    assert serialized_attribute(models, BAD_NAME, column) == ""


@pytest.mark.parametrize(
    ("fmt", "column"), OPTIONAL_CELLS, ids=OPTIONAL_CELL_CASE_IDS)
def test_a_blank_cell_leaves_another_record_untouched(
    fmt: ColumnarFormat, column: str,
) -> None:
    """One record's blank must not reach a different record's output.

    The two files here differ in exactly one cell, and it belongs to the
    record this test does not look at. A bare-digit column is read as
    integers, so the blank floats the column and the *well-formed*
    record's `0` is serialized as `0.0` -- which is what makes "existing
    behaviour is unchanged" already false: what a record serializes as
    depends on whether some other row was blank.
    """
    unblemished = fmt.fields[fmt.columns.index(column)]
    baseline = fmt.parse(fmt.file_with(column, unblemished))
    with_a_blank = fmt.parse(fmt.file_with(column, ""))
    assert baseline is not None
    assert with_a_blank is not None

    assert serialized_attribute(with_a_blank, GOOD_NAME, column) == \
        serialized_attribute(baseline, GOOD_NAME, column)
