"""What a VCF score named in ``scores:`` WITH a ``type:`` reads (gain#1233).

A ``scores:`` entry over a VCF may state a ``type:`` -- and an author
documenting a field commonly restates the one its ``##INFO`` header already
declares.  For a field pysam decodes to a SCALAR that is harmless: the builtin
parsers are idempotent, so stating the header's own type changes nothing.

For a field pysam decodes to a TUPLE it was not.  The ``|``-join that gives
such a field its single value lives in the converter the header-derived
definition installs as ``value_parser``, and a stated ``type:`` displaced it --
handing ``int``/``float``/``str`` the raw tuple.  That read ``None`` plus one
logged ``unable to parse value (1, 2)`` per row for the numeric types, and the
Python tuple repr ``"('a', 'b')"`` -- silently -- for a fixed-arity ``String``.

The contract these tests hold: a stated ``type:`` selects the parser only for
the shapes that reach it as a scalar (``Number`` of ``0``, ``1``, ``A`` or
``R``).  Every other shape keeps the header's join, so an entry that restates
the header's type reads exactly what the header-only resource reads.
"""
import pathlib
import textwrap

import pytest
from gain.genomic_resources.genomic_scores import (
    AlleleScore,
    build_score_from_resource,
)
from gain.genomic_resources.score_def import ScoreValue
from gain.genomic_resources.testing import (
    build_filesystem_test_resource,
    setup_directories,
    setup_vcf,
)

# Every INFO shape ``extract_vcf_value`` distinguishes, in both the types that
# separate them: the ``Flag`` and the ``Number=1`` scalars, the per-allele
# ``Number=A``/``Number=R`` (hence the biallelic row), and the two tuple
# shapes -- unbounded ``Number=.`` and fixed-arity ``Number=2`` -- each in a
# numeric type and in ``String``, which are joined in different places.
#
# The scalar shapes and the tuple shapes carry their values on DIFFERENT rows
# only so that neither data line runs past the line length limit; nothing about
# the behaviour depends on the split.
_SCALAR_POS = 5
_TUPLE_POS = 6

_VCF = textwrap.dedent("""
##fileformat=VCFv4.1
##INFO=<ID=RV,Number=0,Type=Flag,Description="a flag">
##INFO=<ID=CNT,Number=1,Type=Integer,Description="a count">
##INFO=<ID=PA,Number=A,Type=Integer,Description="one per ALT allele">
##INFO=<ID=PR,Number=R,Type=Float,Description="one per allele, REF first">
##INFO=<ID=MANY,Number=.,Type=Integer,Description="an unbounded list">
##INFO=<ID=FMANY,Number=.,Type=Float,Description="unbounded decimals">
##INFO=<ID=TAGS,Number=.,Type=String,Description="unbounded text">
##INFO=<ID=TWO,Number=2,Type=Integer,Description="exactly two numbers">
##INFO=<ID=PAIR,Number=2,Type=String,Description="exactly two labels">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1 5 . A T,G . . RV;CNT=3;PA=7,8;PR=1.5,2.5,3.5
chr1 6 . A T . . MANY=1,2;FMANY=1.5,2.5;TAGS=x,y;TWO=1,2;PAIR=a,b
""")

#: Every field, with the ``type:`` its own ``##INFO`` line declares.
_HEADER_TYPES = {
    "RV": "bool", "CNT": "int", "PA": "int", "PR": "float",
    "MANY": "int", "FMANY": "float", "TAGS": "str",
    "TWO": "int", "PAIR": "str",
}

#: The fields pysam hands over as a scalar, and the ones it hands over whole.
_SCALAR_FIELDS = ["RV", "CNT", "PA", "PR"]
_TUPLE_FIELDS = ["MANY", "FMANY", "TAGS", "TWO", "PAIR"]

#: A ``scores:`` block naming every field and restating the header's own type.
_TYPED_BLOCK = "scores:\n" + "".join(
    f"- id: {field}\n  name: {field}\n  type: {value_type}\n"
    for field, value_type in _HEADER_TYPES.items())

#: The same block with no ``type:`` at all -- the gain#1221 shape, which reads
#: the header's type and the header's parser.
_UNTYPED_BLOCK = "scores:\n" + "".join(
    f"- id: {field}\n  name: {field}\n" for field in _HEADER_TYPES)


def _vcf_score(tmp_path: pathlib.Path, scores_block: str = "") -> AlleleScore:
    """An opened allele score over ``_VCF``, with the ``scores:`` block given.

    Hand-rolled rather than built with ``a_vcf_info_score()``, which emits no
    ``scores:`` block at all -- and the block is what these tests are about.
    An empty ``scores_block`` is the header-only resource.
    """
    setup_directories(tmp_path, {
        "genomic_resource.yaml": textwrap.dedent("""
            type: allele_score
            table:
                filename: data.vcf.gz
        """) + scores_block,
    })
    setup_vcf(tmp_path / "data.vcf.gz", _VCF)
    score = build_score_from_resource(build_filesystem_test_resource(tmp_path))
    assert isinstance(score, AlleleScore)
    return score.open()


def _read(
    score: AlleleScore, pos: int, fields: list[str], alt: str = "T",
) -> dict[str, ScoreValue]:
    """The scores of the ``A>alt`` allele at ``pos``, which must exist."""
    scores = score.fetch_allele_scores("chr1", pos, "A", alt, fields)
    assert scores is not None
    return scores


def test_an_unbounded_integer_field_typed_int_reads_the_joined_text(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """The row from the report: ``MANY=1,2`` under ``type: int``.

    ``int((1, 2))`` raises, and ``parse_value`` turns a failed parse into a
    logged non-value rather than an exception, so the defect was a ``None``
    per row plus one ``unable to parse`` line per row.  Both halves are
    asserted: a fix that silenced the log while still reading nothing would
    pass the log check alone, and one that read the value while still
    logging would pass the value check alone.
    """
    score = _vcf_score(tmp_path, _TYPED_BLOCK)

    with caplog.at_level("ERROR"):
        value = _read(score, _TUPLE_POS, ["MANY"])["MANY"]

    assert value == "1|2"
    assert "unable to parse" not in caplog.text


def test_an_unbounded_float_field_typed_float_reads_the_joined_text(
    tmp_path: pathlib.Path,
) -> None:
    """``float`` fails on a tuple exactly as ``int`` does.

    The defect was never about the integer parser: it is every parser that
    cannot take a tuple, so the numeric sibling is pinned too.
    """
    score = _vcf_score(tmp_path, _TYPED_BLOCK)

    value = _read(score, _TUPLE_POS, ["FMANY"])["FMANY"]

    assert value == "1.5|2.5"


def test_a_fixed_arity_integer_field_typed_int_reads_the_joined_text(
    tmp_path: pathlib.Path,
) -> None:
    """``Number=2`` is a tuple too -- the shape is not only ``Number=.``.

    pysam decodes any arity above one to a tuple, so a field whose header
    FIXES its arity fails the same way an unbounded one does.  A fix keyed
    on ``Number == "."`` alone would leave this reading ``None``.
    """
    score = _vcf_score(tmp_path, _TYPED_BLOCK)

    value = _read(score, _TUPLE_POS, ["TWO"])["TWO"]

    assert value == "1|2"


def test_a_fixed_arity_string_field_typed_str_reads_the_joined_text(
    tmp_path: pathlib.Path,
) -> None:
    """The silent half: ``str`` does not raise on a tuple, it reprs it.

    ``PAIR=a,b`` under ``type: str`` read the eleven-character string
    ``"('a', 'b')"`` straight into the annotated output -- no exception, no
    log, nothing to notice.  That is worse than the numeric shapes' logged
    non-value, and it is why the rule cannot be "keep the config's parser
    unless it would raise".  ``Number=.``/``Type=String`` escapes it only
    because :func:`extract_vcf_value` joins that one shape itself.
    """
    score = _vcf_score(tmp_path, _TYPED_BLOCK)

    value = _read(score, _TUPLE_POS, ["PAIR"])["PAIR"]

    assert value == "a|b"


def test_an_unbounded_string_field_typed_str_is_unchanged(
    tmp_path: pathlib.Path,
) -> None:
    """The one tuple shape that was never broken stays as it was.

    :func:`extract_vcf_value` joins ``Number=.``/``Type=String`` before any
    parser sees it, so this field read correctly even with the config's
    ``str`` in place.  It is asserted because the fix moves which parser it
    carries, and a regression here would be a silent change to the only
    multi-valued shape deployed resources actually type (ClinVar's twenty,
    dbSNP's ``CAF``/``TOPMED``).
    """
    score = _vcf_score(tmp_path, _TYPED_BLOCK)

    value = _read(score, _TUPLE_POS, ["TAGS"])["TAGS"]

    assert value == "x|y"


def test_a_flag_typed_int_reads_the_number_not_the_bool(
    tmp_path: pathlib.Path,
) -> None:
    """dbSNP's ``GNO`` shape -- and what actually pins ``0`` as a scalar.

    ``GNO`` is declared ``Number=0,Type=Flag`` by the file and ``type: int``
    by the resource, so the config's parser is the only thing that can make
    it read ``1``/``0`` rather than ``True``/``False``.  Drop ``0`` from the
    scalar numbers and this field takes the header's converter, which is the
    identity on a ``bool`` -- so the value changes type, and the categorical
    histogram of a deployed score changes with it.

    ``type(...) is int`` rather than ``== 1``: ``True == 1`` and ``bool`` IS
    a subclass of ``int``, so neither equality nor ``isinstance`` can tell
    the two answers apart.
    """
    score = _vcf_score(tmp_path, textwrap.dedent("""
        scores:
        - id: RV
          name: RV
          type: int
    """))

    value = _read(score, _SCALAR_POS, ["RV"])["RV"]

    assert type(value) is int


def test_a_per_allele_field_takes_the_config_type_over_the_header(
    tmp_path: pathlib.Path,
) -> None:
    """What pins ``A``/``R`` as scalars: a config type the header disagrees on.

    ``PA`` is ``Number=A,Type=Integer``; typed ``float`` it must read
    ``7.0``.  Restating ``int`` would prove nothing -- the value pysam
    decoded is already an ``int``, so the header's ``None`` parser passes it
    through unchanged and the two candidate parsers agree.  Only a type the
    header does NOT declare can tell which parser ran.
    """
    score = _vcf_score(tmp_path, textwrap.dedent("""
        scores:
        - id: PA
          name: PA
          type: float
    """))

    value = _read(score, _SCALAR_POS, ["PA"])["PA"]

    assert type(value) is float


def test_a_scalar_field_still_takes_the_config_type_over_the_header(
    tmp_path: pathlib.Path,
) -> None:
    """A stated ``type:`` still OVERRIDES on a scalar -- it is not ignored.

    The fix narrows where the config's parser applies, and the narrowing
    must not reach the shape the feature exists for: ``CNT`` is declared
    ``Integer`` by the header and ``float`` by the config, and the config
    wins, value and type.  Asserting ``3.0 == 3`` would not show it -- the
    type is the assertion.
    """
    score = _vcf_score(tmp_path, textwrap.dedent("""
        scores:
        - id: CNT
          name: CNT
          type: float
    """))

    value = _read(score, _SCALAR_POS, ["CNT"])["CNT"]

    assert isinstance(value, float)


def test_per_allele_fields_typed_int_still_select_by_allele(
    tmp_path: pathlib.Path,
) -> None:
    """``Number=A``/``R`` reach the parse already indexed to one element.

    They arrive at the config as tuples but never reach a parser as one, so
    a stated ``type:`` is honoured and each ALT allele keeps reading its own
    value -- ``R`` counting the reference at offset 0.
    """
    score = _vcf_score(tmp_path, _TYPED_BLOCK)

    assert _read(score, _SCALAR_POS, ["PA"], "T")["PA"] == 7
    assert _read(score, _SCALAR_POS, ["PA"], "G")["PA"] == 8
    assert _read(score, _SCALAR_POS, ["PR"], "G")["PR"] == 3.5


def _values_and_types(
    score: AlleleScore, pos: int, fields: list[str], alt: str,
) -> list[tuple[ScoreValue, type]]:
    """A read, paired with each value's Python type.

    The type travels with the value because ``True == 1.0`` and ``3 == 3.0``:
    an equality check on values alone cannot see a ``bool`` read as a float,
    which is the very confusion gain#1221 fixed next door.
    """
    return [(value, type(value))
            for value in _read(score, pos, fields, alt).values()]


def _declared_types(score: AlleleScore, fields: list[str]) -> list[str | None]:
    """What each score DEFINITION says its type is.

    The definition's own ``value_type``, not the Python type of a value read
    through it -- restating a field's type must leave the resource describing
    itself the way the header-only resource describes it, and a joined
    ``Number=.``/``Type=Integer`` field is the case where the two differ
    (it declares ``int`` and reads text, on every route).
    """
    return [score.score_definitions[field].value_type for field in fields]


@pytest.mark.parametrize(
    ("pos", "fields", "alt"),
    [
        (_SCALAR_POS, _SCALAR_FIELDS, "T"),
        (_SCALAR_POS, _SCALAR_FIELDS, "G"),
        (_TUPLE_POS, _TUPLE_FIELDS, "T"),
    ],
    ids=["scalar-shapes-first-alt", "scalar-shapes-second-alt",
         "tuple-shapes"],
)
def test_restating_the_header_type_reads_what_the_header_only_resource_reads(
    tmp_path: pathlib.Path, pos: int, fields: list[str], alt: str,
) -> None:
    """The contract in one line, over every field shape at once.

    The tests above each name what the defect emitted for one shape.  This
    one pins the rule they follow from -- restating a field's own type
    changes nothing -- by reading the same VCF three ways: through no
    ``scores:`` block, through a block naming every field without a
    ``type:`` (gain#1221's shape), and through one restating every header
    type.  All three must agree, so neither side can drift alone.  The
    per-allele shapes are compared at both ALT indices.
    """
    header_only = _vcf_score(tmp_path / "header_only")
    untyped = _vcf_score(tmp_path / "untyped", _UNTYPED_BLOCK)
    typed = _vcf_score(tmp_path / "typed", _TYPED_BLOCK)

    expected = _values_and_types(header_only, pos, fields, alt)
    expected_types = _declared_types(header_only, fields)

    assert _values_and_types(untyped, pos, fields, alt) == expected
    assert _values_and_types(typed, pos, fields, alt) == expected
    assert _declared_types(untyped, fields) == expected_types
    assert _declared_types(typed, fields) == expected_types
