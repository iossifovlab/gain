"""What a ``bool`` score reads out of a text cell (gain#1192).

``SCORE_TYPE_PARSERS["bool"]`` used to be Python's ``bool``, which is a
TRUTHINESS test, not a parse: every non-empty cell answered ``True``, so the
literal ``False`` in a table answered ``True`` and so did ``0`` and ``yes``.
No deployed resource was reading wrong -- the only ``type: bool`` scores in
the published GRRs are dbSNP's flags, and those are VCF-backed, where pysam
decodes a ``Flag`` to a real ``True`` -- but no text-table bool column could
be read at all.

The two halves this file holds to each other are why the bug is easy to
reintroduce: a ``bool`` score's parser is shared between the text tables it
is fixed for and the VCF path, which must keep reading presence-as-true.
That sharing is not the loose coupling it looks like -- see
test_a_vcf_flag_declared_bool_in_config_still_reads_presence_as_true.
"""
# pylint: disable=C0116,W0212,W0621
import pathlib
import textwrap

import pytest
from gain.genomic_resources.fsspec_protocol import build_fsspec_protocol
from gain.genomic_resources.genomic_scores import (
    AlleleScore,
    PositionScore,
    build_score_from_resource,
)
from gain.genomic_resources.testing import setup_directories, setup_vcf
from gain.genomic_resources.testing.builders import a_position_score


def _bool_score(tmp_path: pathlib.Path, data: str) -> PositionScore:
    """A one-column ``bool`` position score over the authored rows."""
    resource = (
        a_position_score()
        .with_score("flag", "bool")
        .with_data(data)
        .build_resource(tmp_path)
    )
    score = PositionScore(resource)
    score.open()
    return score


def test_a_bool_score_reads_the_text_false_as_false(
    tmp_path: pathlib.Path,
) -> None:
    """The reported defect: the literal ``False`` answered ``True``."""
    score = _bool_score(tmp_path, """
        chrom  pos_begin  pos_end  flag
        chr1   10         19       True
        chr1   20         29       False
        """)

    assert score.fetch_position_scores("chr1", 12, ["flag"]) == [True]
    assert score.fetch_position_scores("chr1", 22, ["flag"]) == [False]


@pytest.mark.parametrize("text,expected", [
    ("True", True), ("true", True), ("TRUE", True), ("1", True),
    ("False", False), ("false", False), ("FALSE", False), ("0", False),
])
def test_the_accepted_bool_spellings(
    tmp_path: pathlib.Path, text: str, expected: bool,
) -> None:
    """The closed set, read end to end rather than off the parser.

    ``0`` and ``1`` are in it because a machine-written table spells a flag
    that way at least as often as it spells it ``True``; every other numeric
    text is not a bool and is refused.
    """
    score = _bool_score(tmp_path, f"""
        chrom  pos_begin  pos_end  flag
        chr1   10         19       {text}
        """)

    assert score.fetch_position_scores("chr1", 12, ["flag"]) == [expected]


def test_an_unreadable_bool_cell_is_a_logged_non_value(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """A spelling outside the set is reported and skipped, not guessed at.

    No new refusal mechanism: this is the same broad guard in
    ``parse_value`` that turns a malformed number into a logged non-value,
    and it is deliberately what a bad bool cell gets too -- one bad row
    should not abort a whole scan.  ``yes`` is the interesting case because
    it is exactly what the old truthiness parser answered ``True`` for.
    """
    score = _bool_score(tmp_path, """
        chrom  pos_begin  pos_end  flag
        chr1   10         19       yes
        """)

    with caplog.at_level("ERROR"):
        assert score.fetch_position_scores("chr1", 12, ["flag"]) == [None]

    assert "flag" in caplog.text
    assert "yes" in caplog.text


def test_a_missing_bool_cell_is_a_SILENT_non_value(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """``.`` means "no value here", and saying so is not a defect to report.

    A ``bool`` score had NO default NA sentinels, where every numeric type
    has ``""``/``nan``/``.``/``NA``.  With the parser now refusing what it
    cannot read, that absence would turn each missing cell into a logged
    parse failure -- a table with a sparse flag column would report one
    error per row and drown the real ones.  So ``bool`` takes the same
    default sentinel set the numeric types have, and a missing cell is a
    non-value in the same silence.

    (Before gain#1192 this cell answered ``True`` -- ``bool(".")`` is
    ``True``, like every other non-empty text.  A genuinely empty cell was
    the other wrong answer available: ``bool("")`` is ``False``, a present
    false datum where there was no datum.)
    """
    score = _bool_score(tmp_path, """
        chrom  pos_begin  pos_end  flag
        chr1   10         19       EMPTY
        """)

    with caplog.at_level("DEBUG"):
        assert score.fetch_position_scores("chr1", 12, ["flag"]) == [None]

    # Not "no logging at all" -- the table itself warns about an unstated
    # ``zero_based``, which is a different fixture's business.
    assert "unable to parse" not in caplog.text


def _dbsnp_shaped_flag_score(tmp_path: pathlib.Path) -> AlleleScore:
    """A VCF ``Flag`` that the resource's ``scores:`` block types ``bool``.

    dbSNP's shape, and the one that makes this parser shared rather than
    text-only: the config-override branch of ``parse_vcf_scoredefs`` takes
    ``value_parser`` from the CONFIG-derived definition, not from the
    header-derived one.  So a VCF score declared ``type: bool`` runs the
    very parser the text tables use, over whatever pysam decoded -- for a
    ``Flag``, a real Python ``bool``.
    """
    setup_directories(tmp_path, {
        "genomic_resource.yaml": textwrap.dedent("""
            type: allele_score
            table:
                filename: data.vcf.gz
            scores:
            - id: RV
              name: RV
              type: bool
              desc: RS orientation is reversed
        """),
    })
    setup_vcf(tmp_path / "data.vcf.gz", textwrap.dedent("""
##fileformat=VCFv4.1
##INFO=<ID=RV,Number=0,Type=Flag,Description="RS orientation is reversed">
#CHROM POS ID REF ALT QUAL FILTER  INFO
chr1   5   .  A   T   .    .       RV
chr1   6   .  A   T   .    .       .
    """))
    proto = build_fsspec_protocol("testing", str(tmp_path))
    score = build_score_from_resource(proto.get_resource(""))
    assert isinstance(score, AlleleScore)
    return score


def test_a_vcf_flag_declared_bool_in_config_still_reads_presence_as_true(
    tmp_path: pathlib.Path,
) -> None:
    """The constraint the text fix must not break.

    A ``Flag`` says what it means by BEING THERE, and pysam decodes that to
    a real ``True`` rather than to any text.  The parser therefore has to
    take a value that is already a ``bool`` and hand it back, which the old
    ``bool`` builtin did for free and a table of accepted spellings does
    not: ``{"1": True}.get(True)`` finds nothing, because ``True`` is not
    the string ``"1"``.

    This is the whole of the VCF exposure.  There is no separate flag
    parser to leave alone -- protecting this path means handling a non-text
    value inside the one parser.
    """
    score = _dbsnp_shaped_flag_score(tmp_path).open()

    assert score.fetch_allele_scores("chr1", 5, "A", "T", ["RV"]) == \
        {"RV": True}


def test_an_absent_vcf_flag_reads_false(tmp_path: pathlib.Path) -> None:
    """A row that does not carry the flag reads ``False``, not a non-value.

    Pinned because it is the half of the ``Flag`` contract that is easy to
    assume wrong: pysam does not leave an absent ``Flag`` out of the INFO
    mapping to be defaulted, it decodes it to a real ``False`` -- so the
    value reaching the parser is a ``bool`` on BOTH branches, and the
    passthrough above is what carries each of them.

    This behaviour predates gain#1192 and is unchanged by it (``bool(False)``
    was also ``False``).  It is pinned here anyway: the presence test alone
    cannot tell a passthrough from a parser that answers ``True`` for
    anything bool-ish, and nothing else in the suite holds this end.
    """
    score = _dbsnp_shaped_flag_score(tmp_path).open()

    assert score.fetch_allele_scores("chr1", 6, "A", "T", ["RV"]) == \
        {"RV": False}
