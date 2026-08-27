"""What each columnar layout contributes to a transcript model.

The five UCSC-derived layouts differ on three axes and no others: the
columns they accept, which column the gene label comes from, and which
columns are copied into `TranscriptModel.attributes` (gain#941). The
first two axes are pinned per format elsewhere -- `test_parsers` feeds
each format a row whose candidate gene columns hold different values, so
a format reading the wrong one is caught there.

The third axis was pinned only by membership: a suite asserting
`"score" in attributes` stays green for a parser that also copies over
every other column of the record. This suite pins the set exactly, in
both directions, so that driving the layouts from one table cannot
quietly widen or narrow what any of them carries.
"""

import pytest
from gain.genomic_resources.gene_models.transcript_models import (
    TranscriptModel,
)

from tests.small.genomic_resources.gene_models.columnar_formats import (
    DEFAULT,
    READ_PATHS,
    ColumnarFormat,
)

#: The UCSC-derived layouts, which are what the table drives, on both
#: read paths -- a headerless file is recognised by counting columns and
#: a headered one by matching their names, and they pin different columns
#: to text. gain's own output format is read by column name and builds
#: its attributes by parsing a dedicated column rather than by copying
#: whole cells, so it is not on this axis at all.
COLUMNAR_FORMATS = [fmt for fmt in READ_PATHS if fmt is not DEFAULT]
COLUMNAR_IDS = [fmt.name for fmt in COLUMNAR_FORMATS]

#: genePred is the one layout whose gene label has a fallback: it reads
#: the alternate-name column when there is one, and the transcript name
#: otherwise. Only its wide form carries that column at all, so this is
#: the one entry that has to be picked out by name -- the headered
#: variants are `replace()` copies, which have no identity to test.
GENEPRED_EXT_PATHS = [
    fmt for fmt in COLUMNAR_FORMATS
    if fmt.name.startswith("ucscgenepredext")
]
GENEPRED_EXT_IDS = [fmt.name for fmt in GENEPRED_EXT_PATHS]

# Selecting by name goes quiet rather than red when a name changes, and a
# parametrization over nothing passes. Both read paths must be here.
assert len(GENEPRED_EXT_PATHS) == 2, GENEPRED_EXT_IDS


def _sole_model(fmt: ColumnarFormat) -> TranscriptModel:
    result = fmt.parse(fmt.good_file())
    assert result is not None, fmt.name
    assert len(result) == 1, fmt.name
    return next(iter(result.values()))


@pytest.mark.parametrize("fmt", COLUMNAR_FORMATS, ids=COLUMNAR_IDS)
def test_layout_carries_exactly_its_own_attribute_columns(
    fmt: ColumnarFormat,
) -> None:
    """A layout copies its attribute columns, and nothing else."""
    model = _sole_model(fmt)

    assert set(model.attributes) == set(fmt.optional_columns), fmt.name


@pytest.mark.parametrize("fmt", COLUMNAR_FORMATS, ids=COLUMNAR_IDS)
def test_layout_keeps_its_attribute_columns_in_layout_order(
    fmt: ColumnarFormat,
) -> None:
    """Attributes keep the order the layout lists them in.

    The order is not incidental: attributes are written back out in
    iteration order, so a table that rebuilt the subset as a set would
    reorder a serialized record without changing any value.
    """
    model = _sole_model(fmt)

    assert tuple(model.attributes) == fmt.optional_columns, fmt.name


@pytest.mark.parametrize("fmt", COLUMNAR_FORMATS, ids=COLUMNAR_IDS)
def test_layout_takes_each_attribute_from_its_own_column(
    fmt: ColumnarFormat,
) -> None:
    """Each attribute holds the cell its own column carried.

    Compared as text, because what type a cell arrives as is a property
    of the read path rather than of the layout: the headerless branch
    pins only the identifying columns to text, so a column of bare
    digits like `score` arrives as an int there and as a string on the
    headered branch (gain#929, gain#931). Pinning the type here would
    pin that trade-off in the wrong suite; what this is about is that
    the value came from the right column.
    """
    model = _sole_model(fmt)

    expected = {
        column: fmt.fields[fmt.columns.index(column)]
        for column in fmt.optional_columns
    }
    as_text = {
        column: str(value) for column, value in model.attributes.items()
    }
    assert as_text == expected, fmt.name


@pytest.mark.parametrize("fmt", GENEPRED_EXT_PATHS, ids=GENEPRED_EXT_IDS)
def test_genepred_falls_back_to_transcript_name_when_alternate_is_blank(
    fmt: ColumnarFormat,
) -> None:
    """A blank alternate name falls back the way an absent one does.

    genePred's gene label reads the alternate-name column when it holds
    something and the transcript name otherwise, and "otherwise" covers
    two different files: the narrow layout, which has no such column at
    all, and the wide one carrying a blank cell in it.

    Only the first was pinned. The second turns on the fallback testing
    the cell rather than its presence, which is one token in the layout
    table -- and a table that tested presence instead would leave every
    other test green while quietly labelling these records with the
    empty string.
    """
    result = fmt.parse(fmt.file_with_column("name2", ""))

    assert result is not None, fmt.name
    assert len(result) == 2, fmt.name
    for model in result.values():
        assert model.gene == model.tr_name, fmt.name
