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
from gain.genomic_resources.gene_models.gene_models_factory import (
    build_gene_models_from_resource,
)
from gain.genomic_resources.gene_models.transcript_models import (
    TranscriptModel,
)
from gain.genomic_resources.testing import build_inmemory_test_resource

from tests.small.genomic_resources.gene_models.columnar_formats import (
    BAD_NAME,
    DEFAULT,
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


def test_a_blank_gene_mapping_cell_is_not_a_float() -> None:
    """The gene mapping is a third read, and it had the same defect.

    A resource may carry a `gene_mapping` file relabelling transcripts,
    and it is read separately from the models themselves -- so the fix
    to the two model reads did not reach it. A blank replacement label
    became the float `NaN` and was written into the `gene` column as the
    token `nan`, and a label the file spelled `NA` was rewritten as
    `nan` too: a value the file did give, replaced by one it never did.
    """
    genes = "".join(
        "\t".join([
            "0", name, "chr17", "+", "100", "400", "150", "350", "2",
            "100,300,", "200,400,", "0", name, "cmpl", "cmpl", "0,0",
        ]) + "\n"
        for name in ("NM_000546", "NM_001126")
    )
    resource = build_inmemory_test_resource(content={
        "genomic_resource.yaml":
            "{type: gene_models, filename: genes.txt, format: refseq,"
            " gene_mapping: map.txt}",
        "genes.txt": genes,
        # the first label is blank, the second is the word NA
        "map.txt": "name\tsym\nNM_000546\t\nNM_001126\tNA\n",
    })
    gene_models = build_gene_models_from_resource(resource)
    gene_models.load()

    genes_by_name = {
        tm.tr_name: tm.gene
        for tm in gene_models.transcript_models.values()
    }

    assert genes_by_name == {"NM_000546": "", "NM_001126": "NA"}


def test_a_blank_gene_in_the_default_format_is_not_a_float() -> None:
    """The default format has its own read, and its own blank to lose.

    `gene` is the one cell of gain's own output format that is neither
    load-bearing -- gain#929 guards the four that are -- nor an
    attribute, so it is the one this issue's other tests do not reach. A
    blank one became the float `NaN`, which is truthy, so serialization
    wrote it out as the token `nan` rather than as the nothing the file
    held.
    """
    models = DEFAULT.parse(DEFAULT.file_with("gene", ""))
    assert models is not None

    assert transcript_named(models, BAD_NAME).gene == ""
