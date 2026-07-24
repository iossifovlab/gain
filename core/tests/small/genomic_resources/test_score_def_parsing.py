"""``GenomicScoreDef`` owns turning a raw cell into a value (gain#405).

Both read paths parse: the per-record one a cell at a time, the bulk one a
column at a time.  They must agree bit-for-bit, and before this they were two
implementations in two layers with nothing holding them together -- which is
how ``pd.to_numeric`` silently diverged from ``float()``.  Both forms now hang
off the definition that owns ``value_parser`` and ``na_values``, and
test_parse_array_agrees_with_parse_value_fuzz holds them to each other.
"""
# pylint: disable=C0116,W0212,W0621
import logging
import pathlib

import numpy as np
import pytest
from gain.genomic_resources.genomic_scores import (
    GenomicScoreDef,
    PositionScore,
)
from gain.genomic_resources.testing.builders import a_position_score


def _float_def(
    tmp_path: pathlib.Path, na_values: str | None = None,
) -> GenomicScoreDef:
    builder = a_position_score().with_score("s", "float")
    if na_values is not None:
        builder = builder.with_na_values(na_values)
    resource = builder.with_data(
        """
        chrom  pos_begin  pos_end  s
        chr1   1          1        0.5
        """).build_resource(tmp_path)
    return PositionScore(resource).score_definitions["s"]


def test_score_def_parses_a_raw_cell(tmp_path: pathlib.Path) -> None:
    score_def = _float_def(tmp_path, na_values=".")

    assert score_def.parse_value("0.5") == 0.5
    # A configured NA sentinel is a non-value, not a parse failure.
    assert score_def.parse_value(".") is None
    # So is a cell that cannot be parsed -- the scan carries on without it.
    assert score_def.parse_value("oops") is None


# Tokens chosen to break a parser that is merely "close enough".  The first
# four are the ones that caught pd.to_numeric: it is not correctly rounded and
# truncates long decimals to ~10 significant digits.  The next two are what
# made the old bulk parser need a re-parse loop -- pandas rejects them, Python
# float() and numpy both accept them.  The rest are NA sentinels, junk and
# whitespace, i.e. the non-values.
_TOKENS = [
    "1e-25", "96.43868415975565", "0.00000071009127180852", "6.754841e-20",
    "1_000", "١٢٣",
    "0.1", "0.95", "1.0", "0", "-0.0", "-3.5", "1e308", "1e-308",
    "nan", "NaN", "inf", "-inf",
    ".", "", "NA", "na", "oops", "1,5", "0x10", " 0.25 ", "1.5\n",
    # A sentinel that is itself a valid number: the only kind that can
    # tell "treated as NA" apart from "parsed". Without it the
    # na_values="-1" config below exercises nothing at all, since no
    # other token matches it (found by mutation testing).
    "-1",
]


def _as_floats(values: list) -> list[float]:
    """Map the scalar contract's None onto the array contract's nan.

    A float64 array has no None, so ``parse_array`` says "no value" the only
    way it can.  The conflation is inherent to the contract, not slack in the
    test: for every consumer a non-value and a nan are the same skip.
    """
    return [float("nan") if value is None else float(value)
            for value in values]


def test_parse_array_agrees_with_parse_value_fuzz(
    tmp_path: pathlib.Path,
) -> None:
    """The whole point: the column parse IS the cell parse, repeated.

    Run for several ``na_values`` configs and several array widths -- numpy
    dispatches short arrays to a scalar loop and longer ones to a SIMD kernel,
    so a vectorized parser can agree at one width and not another.
    """
    for index, na_values in enumerate([None, ".", "NA", "-1"]):
        score_def = _float_def(tmp_path / f"na{index}", na_values=na_values)
        for width in (1, 2, 3, 7, 8, 16, 33, len(_TOKENS)):
            cells = np.array(
                (_TOKENS * 4)[:width], dtype=object)
            got = score_def.parse_array(cells)
            want = _as_floats(
                [score_def.parse_value(cell) for cell in cells])
            assert np.array_equal(got, np.array(want), equal_nan=True), (
                na_values, width,
                [c for c, g, w in zip(cells, got, want, strict=True)
                 if not (g == w or (np.isnan(g) and np.isnan(w)))])


def test_parse_array_logs_one_summary_per_batch(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A corrupt column is reported once, with a count -- not once per cell.

    The bulk path used to say nothing at all here, so a systematically
    unparseable column produced an empty histogram with no diagnostic, in the
    path that now runs by default.  Reporting it per cell, as the per-record
    path does, would mean one traceback per row of a corrupt file.
    """
    score_def = _float_def(tmp_path, na_values=".")
    cells = np.array(["0.5", "oops", "bad", ".", "0.7"], dtype=object)

    with caplog.at_level(logging.WARNING):
        # Only what the parse itself emits: building the resource above
        # warns about an unrelated omitted 'zero_based'.
        caplog.clear()
        values = score_def.parse_array(cells)

    assert len(caplog.records) == 1, [r.getMessage() for r in caplog.records]
    # Two unparseable cells; the configured NA "." is a non-value, not a
    # failure, and is not counted.
    assert "unable to parse 2 of 5 values" in caplog.records[0].getMessage()
    assert np.array_equal(
        values, np.array([0.5, np.nan, np.nan, np.nan, 0.7]), equal_nan=True)
