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
    PositionScore,
    build_score_from_resource,
)
from gain.genomic_resources.score_def import (
    GenomicScoreDef,
    _parse_column_address,
)
from gain.genomic_resources.testing import (
    build_inmemory_test_resource,
    convert_to_tab_separated,
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
    # ...and its OTHER spelling. normalize_na_values stores both forms
    # of a sentinel ("-1" and -1.0), so a vectorized NA test that lets
    # numpy coerce that mixed set to one dtype stringifies the float and
    # starts matching "-1.0" too -- which parse_value never does.
    "-1.0",
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


def test_parse_array_agrees_with_parse_value_on_float_cells(
    tmp_path: pathlib.Path,
) -> None:
    """The float-dtype branch honours na_values too.

    A bigWig payload arrives as float64, not as text, so parse_array takes a
    different branch -- one the object-dtype fuzz above cannot reach.  A
    sentinel must still be a non-value there: matching it against the text
    form of na_values only would make the branch a permanent no-op, and the
    value the config declares "absent" would be binned as real data.
    """
    score_def = _float_def(tmp_path, na_values="-1")
    cells = np.array([-1.0, 0.5, 2.0, np.nan])

    got = score_def.parse_array(cells)
    want = _as_floats([score_def.parse_value(float(c)) for c in cells])

    assert np.array_equal(got, np.array(want), equal_nan=True), (got, want)
    assert np.isnan(got[0]), "the configured NA sentinel must not survive"


# --- column addressing ------------------------------------------------------
#
# A score is addressed by column NAME or by column INDEX.  Index 0 is a legal
# address and used to be silently discarded; see _parse_column_address.

@pytest.mark.parametrize("key", ["column_index", "index"])
@pytest.mark.parametrize("index", [0, 1, 3])
def test_column_index_zero_is_a_real_address(key: str, index: int) -> None:
    """Index 0 must survive parsing, under either spelling.

    It is the only integer that is falsy, and the parsing this replaced tested
    the value for truth twice -- once in an ``or`` between the modern key and
    its legacy alias, once in a ternary -- so ``column_index: 0`` came back as
    "no index configured" and the resource could not be opened at all.
    """
    col_name, col_index = _parse_column_address(
        {"id": "s", "type": "float", key: index})

    assert col_index == index
    assert col_name is None


def test_a_score_at_column_zero_can_be_read() -> None:
    """End to end: a table whose score really is its first column.

    Legal by construction -- chrom/pos_begin/pos_end are addressed
    independently, so nothing reserves column 0 for the contig -- and the
    validator explicitly permits ``0 <= column_index``.  Before the fix this
    raised a message-less AssertionError out of ``open()``.
    """
    res = build_inmemory_test_resource({
        "genomic_resource.yaml": """
            type: position_score
            table:
                header_mode: none
                filename: data.mem
                chrom:
                    index: 1
                pos_begin:
                    index: 2
                pos_end:
                    index: 3
            scores:
            - id: s
              column_index: 0
              type: float""",
        "data.mem": convert_to_tab_separated("""
            0.5   chr1  10  12
            0.75  chr1  20  22
        """),
    })
    score = build_score_from_resource(res)
    with score.open():
        assert score.score_definitions["s"].col_index == 0
        assert score.score_definitions["s"].score_index == 0
        values = list(score.fetch_region_values("chr1", 10, 22, ["s"]))

    assert [value[2] for value in values] == [[0.5], [0.75]]


def test_a_score_addressing_nothing_is_refused_by_name() -> None:
    """No address at all is a config error, reported as one.

    This used to be a bare ``assert score_def.col_name is not None`` -- no
    message, no resource, no score id -- and under ``python -O`` no check at
    all, leaving ``header.index(None)`` to fail later and less clearly.
    """
    res = build_inmemory_test_resource({
        "genomic_resource.yaml": """
            type: position_score
            table:
                header_mode: none
                filename: data.mem
                chrom:
                    index: 0
                pos_begin:
                    index: 1
                pos_end:
                    index: 2
            scores:
            - id: s
              type: float""",
        "data.mem": convert_to_tab_separated("""
            chr1  10  12  0.5
        """),
    })
    with pytest.raises(ValueError, match="configures neither column_name"):
        build_score_from_resource(res).open()


def test_a_score_addressing_two_things_is_refused() -> None:
    """Name and index together is refused by the SCHEMA, before open().

    The resource schema declares ``column_name`` and ``column_index``
    mutually exclusive (``excludes``), so a config carrying both never
    reaches the resolution block -- which is why the guard there is stated in
    terms of the score def and is reachable only by building one in code.
    Both halves are worth pinning: this one is what a user with a bad yaml
    actually gets, and the sibling below is what protects the resolution
    itself.
    """
    res = build_inmemory_test_resource({
        "genomic_resource.yaml": """
            type: position_score
            table:
                header_mode: none
                filename: data.mem
                chrom:
                    index: 0
                pos_begin:
                    index: 1
                pos_end:
                    index: 2
            scores:
            - id: s
              column_name: s
              column_index: 3
              type: float""",
        "data.mem": convert_to_tab_separated("""
            chr1  10  12  0.5
        """),
    })
    with pytest.raises(ValueError, match="Invalid configuration"):
        build_score_from_resource(res)


def test_the_resolution_guard_refuses_a_doubly_addressed_score_def() -> None:
    """The open()-time half of the rule above, reachable only in code.

    The schema stops a doubly-addressed *config*; this stops a doubly-
    addressed *definition*, which is what the resolution block actually reads.
    Without it the index would silently win and the name be ignored.
    """
    res = build_inmemory_test_resource({
        "genomic_resource.yaml": """
            type: position_score
            table:
                header_mode: none
                filename: data.mem
                chrom:
                    index: 0
                pos_begin:
                    index: 1
                pos_end:
                    index: 2
            scores:
            - id: s
              column_index: 3
              type: float""",
        "data.mem": convert_to_tab_separated("""
            chr1  10  12  0.5
        """),
    })
    score = build_score_from_resource(res)
    # Score defs are built in __init__, so this is before any resolution.
    score.score_definitions["s"].col_name = "s"

    with pytest.raises(ValueError, match="configures both a column name"):
        score.open()


def test_score_index_does_not_exist_until_the_score_is_opened() -> None:
    """"Unresolved" is the attribute's absence, not a sentinel value.

    ``score_index`` is ``field(init=False)`` with no default, so a def that
    has not been through ``GenomicScore.open`` has no such attribute and
    reading it says so.  A sentinel would have to be a real int, and every
    candidate is a valid payload index -- ``-1`` most treacherously, since it
    would read the last column instead of failing.
    """
    res = build_inmemory_test_resource({
        "genomic_resource.yaml": """
            type: position_score
            table:
                header_mode: none
                filename: data.mem
                chrom:
                    index: 0
                pos_begin:
                    index: 1
                pos_end:
                    index: 2
            scores:
            - id: s
              column_index: 3
              type: float""",
        "data.mem": convert_to_tab_separated("""
            chr1  10  12  0.5
        """),
    })
    score = build_score_from_resource(res)
    score_def = score.score_definitions["s"]

    # Built in __init__, so the configured address is already known...
    assert score_def.col_index == 3
    # ...but nothing has resolved it to a payload column yet.
    with pytest.raises(AttributeError, match="score_index"):
        _ = score_def.score_index

    with score.open():
        assert score_def.score_index == 3
