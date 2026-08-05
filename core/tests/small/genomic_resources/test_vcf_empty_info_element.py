"""An EMPTY element inside a multi-valued VCF INFO field.

``ORIGIN=1,`` (or ``a,,b``, or ``,b``) is a malformed row: the field claims a
value it does not carry, and pysam decodes that element as ``None``.  Two
places join such a tuple on '|' -- :func:`extract_vcf_value` for
``Number=.``/``Type=String``, and the ``converter`` installed as
``value_parser`` for every other unbounded shape -- and neither used to
tolerate it, so the SAME malformed row failed in two different ways depending
only on the field's declared ``Type``: a ``TypeError`` that took the whole
fetch down for ``String``, and a silent ``'3|None'`` annotated into the output
for ``Integer``.

These tests pin the one rule both now follow (#630), which is the rule #256
and #289 already settled for the per-allele shapes -- no applicable value, no
score:

* an empty element contributes nothing to the join, and a tuple of nothing
  but empty elements is a null score rather than an empty string;
* a ``.`` element is NOT an empty element in a ``String`` field: pysam
  decodes it as the literal ``'.'``, dbSNP's ``CAF``/``TOPMED`` rely on it,
  and it stays;
* the malformed row is reported -- once per field per table, not per row --
  because only the resource's author can fix the data.
"""
from __future__ import annotations

import logging
import pathlib

import pytest
from gain.genomic_resources.genomic_scores import AlleleScore
from gain.genomic_resources.testing.builders import (
    a_grr,
    a_vcf_info_score,
)


def _open_vcf_score(tmp_path: pathlib.Path, data: str) -> AlleleScore:
    """Realize a one-resource GRR from VCF text and open its allele score."""
    repo = (
        a_grr()
        .with_resource("vcf", a_vcf_info_score().with_data(data))
        .build_repo(tmp_path)
    )
    return AlleleScore(repo.get_resource("vcf")).open()


def _score_value(
    tmp_path: pathlib.Path, data: str, score_id: str,
) -> object:
    """Read ``score_id`` off the single record of a one-row VCF."""
    score = _open_vcf_score(tmp_path, data)
    with score:
        values = [
            score.get_score_value_from_record(record, score_id)
            for record in score.fetch_records("chr1", 5, 5)
        ]
    assert len(values) == 1
    return values[0]


def test_a_trailing_empty_element_of_a_string_field_is_dropped(
    tmp_path: pathlib.Path,
) -> None:
    """``ORIGIN=1,`` reads ``'1'`` -- the empty element adds no value.

    This is the row from the report, and the ``Number=.``/``Type=String``
    half of the bug: the bare ``"|".join(value)`` raised ``TypeError`` on the
    ``None`` pysam decodes the empty element to, and that call sits outside
    the narrow ``try`` in ``parse_value`` (deliberately -- #256's crash guard
    depends on the placement), so the error escaped the read and took the
    whole fetch with it.
    """
    value = _score_value(tmp_path, """
##fileformat=VCFv4.1
##INFO=<ID=ORIGIN,Number=.,Type=String,Description="allele origin">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   5   .  A   T   .    .      ORIGIN=1,
""", "ORIGIN")

    assert value == "1"


def test_a_mid_value_empty_element_of_a_string_field_is_dropped(
    tmp_path: pathlib.Path,
) -> None:
    """``ORIGIN=a,,b`` reads ``'a|b'``: the trigger is not a trailing comma.

    An empty element is an empty element wherever it sits, so the fix cannot
    be "strip a trailing comma" -- the values around it are well-formed and
    keep their places and their order.
    """
    value = _score_value(tmp_path, """
##fileformat=VCFv4.1
##INFO=<ID=ORIGIN,Number=.,Type=String,Description="allele origin">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   5   .  A   T   .    .      ORIGIN=a,,b
""", "ORIGIN")

    assert value == "a|b"


def test_a_leading_empty_element_of_a_string_field_is_dropped(
    tmp_path: pathlib.Path,
) -> None:
    """``ORIGIN=,b`` reads ``'b'`` -- the leading position is no different."""
    value = _score_value(tmp_path, """
##fileformat=VCFv4.1
##INFO=<ID=ORIGIN,Number=.,Type=String,Description="allele origin">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   5   .  A   T   .    .      ORIGIN=,b
""", "ORIGIN")

    assert value == "b"


def test_an_empty_element_of_an_integer_field_is_dropped_not_stringified(
    tmp_path: pathlib.Path,
) -> None:
    """``NUM=3,`` reads ``'3'``, not ``'3|None'`` -- the silent half.

    Nothing but the declared ``Type`` separates this row from the ``String``
    one above, and it used to decide between a crash and a corruption: this
    tuple never reaches ``extract_vcf_value``'s join, it is handed to the
    ``converter`` installed as ``value_parser``, whose ``"|".join(map(str,
    val))`` renders the empty element as the four-character STRING ``None``.
    No exception, no warning -- the text ``3|None`` straight into the
    annotated output, which is worse than the crash because nothing reports
    it.
    """
    value = _score_value(tmp_path, """
##fileformat=VCFv4.1
##INFO=<ID=NUM,Number=.,Type=Integer,Description="unbounded ints">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   5   .  A   T   .    .      NUM=3,
""", "NUM")

    assert value == "3"


def test_an_empty_element_of_a_float_field_is_dropped_not_stringified(
    tmp_path: pathlib.Path,
) -> None:
    """``FL=0.5,`` reads ``'0.5'``: every non-``String`` type, one rule.

    ``Float`` takes the same ``converter`` path as ``Integer`` and corrupted
    the same way (``'0.5|None'``).  Pinned separately because the two are
    reached through different pysam decodings, and a fix that filtered inside
    one type's parse would leave the other.
    """
    value = _score_value(tmp_path, """
##fileformat=VCFv4.1
##INFO=<ID=FL,Number=.,Type=Float,Description="unbounded floats">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   5   .  A   T   .    .      FL=0.5,
""", "FL")

    assert value == "0.5"


def test_a_string_field_of_nothing_but_empty_elements_reads_null(
    tmp_path: pathlib.Path,
) -> None:
    """``ORIGIN=,,`` is a null score, not the empty string.

    Dropping every element leaves nothing to join, and what a join of
    nothing produces -- ``''`` -- is a VALUE: it aggregates, it bins into a
    histogram, it lands in an annotated column as a present-but-blank cell.
    A field that carries no value at all reads the same ``None`` an absent
    INFO key does.
    """
    value = _score_value(tmp_path, """
##fileformat=VCFv4.1
##INFO=<ID=ORIGIN,Number=.,Type=String,Description="allele origin">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   5   .  A   T   .    .      ORIGIN=,,
""", "ORIGIN")

    assert value is None


def test_an_integer_field_of_nothing_but_empty_elements_reads_null(
    tmp_path: pathlib.Path,
) -> None:
    """``NUM=,`` is a null score too: the other path answers the same.

    The whole point of #630 is that the declared ``Type`` stops deciding the
    outcome, so the all-empty tuple cannot read ``None`` down one path and
    ``''`` down the other.
    """
    value = _score_value(tmp_path, """
##fileformat=VCFv4.1
##INFO=<ID=NUM,Number=.,Type=Integer,Description="unbounded ints">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   5   .  A   T   .    .      NUM=,
""", "NUM")

    assert value is None


def test_a_malformed_row_warns_once_per_table_not_once_per_record(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """The dropped element is reported -- once for the table, not per row.

    Silently dropping it would trade a crash for a quiet loss of data: the
    row says a value is there and the annotation says it is not, and only the
    resource's author can reconcile the two.  So it is logged, naming the
    field and the locus -- the offending row of the offending file, which is
    what an author has to open.

    ONCE, on the model of #289's arity report: this is the per-record score
    read (#237), and a field malformed on one row is normally malformed on
    many, so per-row logging would bury a run in identical lines.
    """
    score = _open_vcf_score(tmp_path, """
##fileformat=VCFv4.1
##INFO=<ID=ORIGIN,Number=.,Type=String,Description="allele origin">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   5   .  A   T   .    .      ORIGIN=1
chr1   6   .  A   T   .    .      ORIGIN=1,
chr1   7   .  A   T   .    .      ORIGIN=2,
""")
    with caplog.at_level(logging.WARNING), score:
        values = [
            score.get_score_value_from_record(record, "ORIGIN")
            for record in score.fetch_records("chr1", 5, 7)
        ]

    assert values == ["1", "1", "2"]
    warnings = [
        record for record in caplog.records
        if record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    # Actionable on its own: which field, and which row to open.
    assert "INFO field ORIGIN" in message
    assert "chr1:6" in message


def test_two_malformed_fields_of_one_table_are_each_reported(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """"Once" is per FIELD per table -- one silenced field cannot hide another.

    The flag lives on the score DEFINITION, one per INFO field per resource,
    so a table with two broken fields says so twice.  A per-table flag would
    report whichever field happened to be read first and leave the author
    fixing one of two defects.
    """
    score = _open_vcf_score(tmp_path, """
##fileformat=VCFv4.1
##INFO=<ID=ORIGIN,Number=.,Type=String,Description="allele origin">
##INFO=<ID=NOTE,Number=.,Type=String,Description="unbounded strings">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   5   .  A   T   .    .      ORIGIN=1,;NOTE=a,
chr1   6   .  A   T   .    .      ORIGIN=2,;NOTE=b,
""")
    with caplog.at_level(logging.WARNING), score:
        for record in score.fetch_records("chr1", 5, 6):
            score.get_score_value_from_record(record, "ORIGIN")
            score.get_score_value_from_record(record, "NOTE")

    messages = [
        record.getMessage() for record in caplog.records
        if record.levelno == logging.WARNING
    ]
    assert len(messages) == 2
    assert any("INFO field ORIGIN" in message for message in messages)
    assert any("INFO field NOTE" in message for message in messages)


def test_an_arity_warning_and_an_empty_element_warning_coexist(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """The two malformations of one field are reported independently.

    ``S`` is broken twice over -- a ``Number=A`` field with fewer values than
    ALT alleles (#289) AND an empty element among them (#630) -- and the two
    reports run off SEPARATE flags.  Sharing one would let whichever row came
    first silence the other defect for the rest of the table, so an author
    who fixed the reported one would never learn of the second.
    """
    score = _open_vcf_score(tmp_path, """
##fileformat=VCFv4.1
##INFO=<ID=S,Number=A,Type=String,Description="one value per ALT">
##INFO=<ID=ORIGIN,Number=.,Type=String,Description="allele origin">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   5   .  A   T,G  .    .      S=d11;ORIGIN=1,
""")
    with caplog.at_level(logging.WARNING), score:
        for record in score.fetch_records("chr1", 5, 5):
            score.get_score_value_from_record(record, "S")
            score.get_score_value_from_record(record, "ORIGIN")

    messages = [
        record.getMessage() for record in caplog.records
        if record.levelno == logging.WARNING
    ]
    assert len(messages) == 2
    assert any("INFO field S" in message and "allele(s)" in message
               for message in messages)
    assert any("INFO field ORIGIN" in message and "empty element" in message
               for message in messages)


def test_a_dot_element_is_a_value_and_is_neither_dropped_nor_reported(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """``CAF=.,1`` still reads ``'.|1'``: ``.`` is not an empty element.

    pysam decodes ``.`` in a ``String`` field as the literal one-character
    string, not as ``None``, and dbSNP's ``CAF``/``TOPMED`` mean something by
    it -- "this allele's frequency is not reported" is data, and the position
    of the value that follows depends on it being kept.  This is why the drop
    tests ``is None`` and not falsiness: ``'.'``, ``'0'`` and ``''`` are all
    values, and a blanket falsy-filter would silently rewrite this field.
    """
    score = _open_vcf_score(tmp_path, """
##fileformat=VCFv4.1
##INFO=<ID=CAF,Number=.,Type=String,Description="allele frequencies">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   5   .  A   T   .    .      CAF=.,1
""")
    with caplog.at_level(logging.WARNING), score:
        values = [
            score.get_score_value_from_record(record, "CAF")
            for record in score.fetch_records("chr1", 5, 5)
        ]

    assert values == [".|1"]
    assert [
        record for record in caplog.records
        if record.levelno == logging.WARNING
    ] == []


def test_a_well_formed_table_reads_unchanged_and_says_nothing(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """The check must not touch, or cry wolf over, well-formed fields.

    Multi-valued fields of every shape the drop passes through -- the
    ``String`` join, the ``converter``'s join for ``Integer``, and a
    single-valued field that never becomes a tuple at all -- come back
    exactly as they did before #630, with nothing logged.  A check that fired
    here would put a warning on every legitimate multi-valued VCF in the
    tree.
    """
    score = _open_vcf_score(tmp_path, """
##fileformat=VCFv4.1
##INFO=<ID=ORIGIN,Number=.,Type=String,Description="allele origin">
##INFO=<ID=NUM,Number=.,Type=Integer,Description="unbounded ints">
##INFO=<ID=ONE,Number=1,Type=Integer,Description="a single value">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   5   .  A   T   .    .      ORIGIN=a,b;NUM=3,4;ONE=7
chr1   6   .  A   T   .    .      ORIGIN=c;NUM=5;ONE=8
""")
    with caplog.at_level(logging.WARNING), score:
        results = [
            (score.get_score_value_from_record(record, "ORIGIN"),
             score.get_score_value_from_record(record, "NUM"),
             score.get_score_value_from_record(record, "ONE"))
            for record in score.fetch_records("chr1", 5, 6)
        ]

    assert results == [("a|b", "3|4", 7), ("c", "5", 8)]
    assert [
        record for record in caplog.records
        if record.levelno == logging.WARNING
    ] == []


def test_a_malformed_row_costs_only_its_own_cell_in_a_region_scan(
    tmp_path: pathlib.Path,
) -> None:
    """A scan over the malformed row still returns every well-formed row.

    The reported shape of the bug: ``fetch_region_values`` yielded the first
    row and then died on the second, so the well-formed row AFTER it was
    never reached -- one bad cell cost the whole fetch, and a caller that saw
    two rows where three exist had no way to tell.  (The generator has to be
    consumed for that to show, which is what ``list`` here is for.)
    """
    score = _open_vcf_score(tmp_path, """
##fileformat=VCFv4.1
##INFO=<ID=ORIGIN,Number=.,Type=String,Description="allele origin">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   5   .  A   T   .    .      ORIGIN=1
chr1   6   .  A   T   .    .      ORIGIN=1,
chr1   7   .  A   T   .    .      ORIGIN=2
""")
    with score:
        values = list(score.fetch_region_values("chr1", 1, 100, ["ORIGIN"]))

    assert values == [(5, 5, ["1"]), (6, 6, ["1"]), (7, 7, ["2"])]


def _read_with_warnings(
    score: AlleleScore, caplog: pytest.LogCaptureFixture,
    score_id: str, begin: int = 5, end: int = 5,
) -> tuple[list[object], list[str]]:
    """Read ``score_id`` over a region, returning its values and warnings."""
    with caplog.at_level(logging.WARNING), score:
        values = [
            score.get_score_value_from_record(record, score_id)
            for record in score.fetch_records("chr1", begin, end)
        ]
    return values, [
        record.getMessage() for record in caplog.records
        if record.levelno == logging.WARNING
    ]


def test_a_dot_in_a_numeric_field_is_dropped_without_being_called_malformed(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """``AF=0.5,.`` is spec-legal, so it is dropped in silence.

    ``.`` is the VCF spec's own missing-value token and is legal in any
    field, but only a ``String`` field survives the round trip as the literal
    ``'.'``; in a numeric one pysam decodes it to the same ``None`` an absent
    element gives.  The two cannot be told apart here, and this shape is
    ordinary in real data -- it is what bcftools writes for an unknown
    element of a vector -- so reporting it would put a warning claiming a
    malformed row on files that are correct.
    """
    values, warnings = _read_with_warnings(_open_vcf_score(tmp_path, """
##fileformat=VCFv4.1
##INFO=<ID=AF,Number=.,Type=Float,Description="allele frequencies">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   5   .  A   T   .    .      AF=0.5,.
"""), caplog, "AF")

    assert values == ["0.5"]
    assert warnings == []


def test_an_empty_element_of_a_numeric_field_is_dropped_without_a_report(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """``NUM=3,`` IS malformed, and is still dropped in silence.

    The cost of the line above: an empty element and a spec-legal ``.``
    arrive at this layer as one value, so a numeric field cannot report the
    malformed one without also crying wolf over the legal one.  Silence on
    both is the side that does not send an author after a defect that is not
    there.  The VALUE is corrected either way -- which is the half of #630
    that changes what gets annotated.
    """
    values, warnings = _read_with_warnings(_open_vcf_score(tmp_path, """
##fileformat=VCFv4.1
##INFO=<ID=NUM,Number=.,Type=Integer,Description="unbounded ints">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   5   .  A   T   .    .      NUM=3,
"""), caplog, "NUM")

    assert values == ["3"]
    assert warnings == []


def test_an_empty_element_of_a_number_a_field_is_reported(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """``ALT=T,G  S=d1,`` reports, though its arity is right.

    A per-allele field needs no drop -- the empty element IS the allele's
    null -- but the row still declared a value it does not carry, and the
    arity check cannot see it: one value per allele is exactly what
    ``Number=A`` asks for, so nothing else in the read has anything to say
    about it.  Without this the second allele reads null with no account of
    why.
    """
    values, warnings = _read_with_warnings(_open_vcf_score(tmp_path, """
##fileformat=VCFv4.1
##INFO=<ID=S,Number=A,Type=String,Description="one value per ALT">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   5   .  A   T,G  .    .      S=d1,
"""), caplog, "S")

    assert values == ["d1", None]
    assert len(warnings) == 1
    assert "INFO field S" in warnings[0]
    assert "empty element" in warnings[0]
    assert "chr1:5" in warnings[0]


def test_an_empty_element_of_a_number_r_field_is_reported(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """``Number=R`` has the identical exposure and the identical answer."""
    values, warnings = _read_with_warnings(_open_vcf_score(tmp_path, """
##fileformat=VCFv4.1
##INFO=<ID=RV,Number=R,Type=String,Description="ref and each ALT">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   5   .  A   T   .    .      RV=ref,
"""), caplog, "RV")

    assert values == [None]
    assert len(warnings) == 1
    assert "INFO field RV" in warnings[0]
    assert "empty element" in warnings[0]


def test_a_dot_in_a_numeric_per_allele_field_is_not_reported(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """The per-allele report answers to the same type test as the drop.

    ``AF=0.5,.`` under ``Number=A`` is the legal shape again, one allele
    deep: the second allele's frequency is simply not reported.  It reads
    null, and says nothing.
    """
    values, warnings = _read_with_warnings(_open_vcf_score(tmp_path, """
##fileformat=VCFv4.1
##INFO=<ID=AF,Number=A,Type=Float,Description="one frequency per ALT">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   5   .  A   T,G  .    .      AF=0.5,.
"""), caplog, "AF")

    assert values == [0.5, None]
    assert warnings == []


def test_a_string_field_present_with_no_value_reads_null_and_is_reported(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """``ORIGIN=`` names a key and supplies nothing, and is told so.

    pysam decodes it to the EMPTY TUPLE rather than to a tuple of ``None``,
    so there is no empty element to find and the drop never fires; the score
    reads the null a field carrying no value should.  It is reported by its
    own message, because "carries an empty element" is not what is wrong
    with it.
    """
    values, warnings = _read_with_warnings(_open_vcf_score(tmp_path, """
##fileformat=VCFv4.1
##INFO=<ID=ORIGIN,Number=.,Type=String,Description="allele origin">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   5   .  A   T   .    .      ORIGIN=
"""), caplog, "ORIGIN")

    assert values == [None]
    assert len(warnings) == 1
    assert "INFO field ORIGIN" in warnings[0]
    assert "no value at all" in warnings[0]
    assert "chr1:5" in warnings[0]


def test_a_numeric_field_present_with_no_value_reads_null_without_a_report(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """``AF=`` reads null in silence: pysam gives it as a whole-field ``.``.

    A numeric field with nothing after the ``=`` decodes to ``(None,)`` --
    the very tuple ``AF=.`` gives -- so it arrives as the legal
    "value missing" and is answered like one.
    """
    values, warnings = _read_with_warnings(_open_vcf_score(tmp_path, """
##fileformat=VCFv4.1
##INFO=<ID=AF,Number=.,Type=Float,Description="allele frequencies">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   5   .  A   T   .    .      AF=
"""), caplog, "AF")

    assert values == [None]
    assert warnings == []
