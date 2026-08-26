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
    CHROM,
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
