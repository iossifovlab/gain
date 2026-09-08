"""What a VCF score named in ``scores:`` WITHOUT ``type:`` reads (gain#1221).

A ``scores:`` entry over a VCF may leave ``type:`` unstated so that the
score takes the type its ``##INFO`` header line declares.  The definition's
``value_type`` honoured that; its ``value_parser`` did not -- it was the
``float`` parser an unstated type gets on a text table, run over a value
pysam had already decoded.  A ``Flag`` therefore read ``1.0``/``0.0``, and a
``String`` field read a logged non-value on every row.

The contract these tests hold: an entry that states no ``type:`` reads
exactly what the header-only path reads for the same field.  The parser
follows the same source as the type.
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

# Every INFO shape ``extract_vcf_value`` distinguishes: a ``Flag``,
# ``Number=1`` in two types, the per-allele ``Number=A`` and ``Number=R``
# (hence the biallelic row), and ``Number=.`` in both the type the
# converter joins and the ``String`` the extractor joins itself.  The
# second row carries nothing, so the flag is absent there.
_VCF = textwrap.dedent("""
##fileformat=VCFv4.1
##INFO=<ID=RV,Number=0,Type=Flag,Description="a flag">
##INFO=<ID=CNT,Number=1,Type=Integer,Description="a count">
##INFO=<ID=LAB,Number=1,Type=String,Description="a label">
##INFO=<ID=PA,Number=A,Type=Integer,Description="one per ALT allele">
##INFO=<ID=PR,Number=R,Type=Float,Description="one per allele, REF first">
##INFO=<ID=MANY,Number=.,Type=Integer,Description="an unbounded list">
##INFO=<ID=TAGS,Number=.,Type=String,Description="unbounded text">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1 5 . A T,G . . RV;CNT=3;LAB=foo;PA=7,8;PR=1.5,2.5,3.5;MANY=1,2;TAGS=x,y
chr1 6 . A T . . .
""")

_FIELDS = ["RV", "CNT", "LAB", "PA", "PR", "MANY", "TAGS"]

#: A ``scores:`` block naming every field, none with a ``type:``.
_UNTYPED_BLOCK = "scores:\n" + "".join(
    f"- id: {field}\n  name: {field}\n" for field in _FIELDS)


def _vcf_score(tmp_path: pathlib.Path, scores_block: str = "") -> AlleleScore:
    """An opened allele score over ``_VCF``, with the ``scores:`` block given.

    Hand-rolled rather than built with ``a_vcf_info_score()``, which cannot
    emit a ``scores:`` block -- and the block is what these tests are about.
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


def test_a_vcf_flag_named_without_type_reads_presence_as_true(
    tmp_path: pathlib.Path,
) -> None:
    """The dbSNP shape minus its ``type: bool`` line.

    ``1.0`` is what the defect answered here: the ``float`` parser over the
    ``True`` pysam decodes.  ``True == 1.0`` in Python, so the assertion
    names the type as well as the value.
    """
    score = _vcf_score(tmp_path, _UNTYPED_BLOCK)

    value = _read(score, 5, ["RV"])["RV"]

    assert value is True


def test_a_vcf_flag_named_without_type_reads_absence_as_false(
    tmp_path: pathlib.Path,
) -> None:
    """The other half of the Flag contract: the defect read ``0.0`` here."""
    score = _vcf_score(tmp_path, _UNTYPED_BLOCK)

    value = _read(score, 6, ["RV"])["RV"]

    assert value is False


def test_an_integer_field_named_without_type_reads_an_int(
    tmp_path: pathlib.Path,
) -> None:
    """The defect read ``3.0``: the float parser over pysam's ``3``."""
    score = _vcf_score(tmp_path, _UNTYPED_BLOCK)

    value = _read(score, 5, ["CNT"])["CNT"]

    assert value == 3
    assert isinstance(value, int)


def test_a_string_field_named_without_type_reads_its_text(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """The worst shape: ``float("foo")`` failed on every row.

    ``parse_value`` turns a failed parse into a logged non-value rather
    than an exception, so the defect was a ``None`` per row plus one
    ``unable to parse`` line per row.  Both halves are asserted: a fix
    that swallowed the log but still read nothing would pass the value
    check alone in reverse, and vice versa.
    """
    score = _vcf_score(tmp_path, _UNTYPED_BLOCK)

    with caplog.at_level("ERROR"):
        value = _read(score, 5, ["LAB"])["LAB"]

    assert value == "foo"
    assert "unable to parse" not in caplog.text


def test_an_unbounded_field_named_without_type_reads_the_joined_text(
    tmp_path: pathlib.Path,
) -> None:
    """``Number=.`` is the shape whose header-side parser is not ``None``.

    The header-derived definition installs the ``|``-joining converter
    for it, so inheriting the header's parser has to mean inheriting THAT,
    not merely dropping the config's.  The defect read ``None`` here
    (``float((1, 2))`` fails).
    """
    score = _vcf_score(tmp_path, _UNTYPED_BLOCK)

    value = _read(score, 5, ["MANY"])["MANY"]

    assert value == "1|2"


def test_an_untyped_entry_reads_what_the_header_only_resource_reads(
    tmp_path: pathlib.Path,
) -> None:
    """The contract in one line, over every field shape at once.

    The five tests above each name what the defect emitted.  This one
    pins the rule they follow from -- silence on ``type:`` means "as the
    header says" -- by reading the same VCF through a ``scores:`` block
    that names every field without a type and through no block at all,
    and demanding the two reads agree, value and type (``True == 1.0``).
    It cannot drift on one side alone.  Read once per ALT allele, so the
    per-allele shapes are compared at both indices.
    """
    header_only = _vcf_score(tmp_path / "header_only")
    untyped = _vcf_score(tmp_path / "untyped", _UNTYPED_BLOCK)

    for alt in ("T", "G"):
        expected = _read(header_only, 5, _FIELDS, alt)
        actual = _read(untyped, 5, _FIELDS, alt)

        assert [(v, type(v)) for v in actual.values()] == \
            [(v, type(v)) for v in expected.values()], alt
