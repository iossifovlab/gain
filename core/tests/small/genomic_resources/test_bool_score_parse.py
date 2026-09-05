"""What a ``bool`` score reads out of a cell (gain#1192).

``SCORE_TYPE_PARSERS["bool"]`` used to be Python's ``bool``, which is a
TRUTHINESS test, not a parse: every non-empty cell answered ``True``, so the
literal ``False`` in a table answered ``True``, and so did ``0`` and ``yes``.

The two halves this file holds to each other are why the bug is easy to
reintroduce: one parser serves both the text tables it was fixed for and the
VCF path, which must keep reading a ``Flag``'s presence as true.

The reasoning behind the closed vocabulary, the empty ``na_values`` default
and what that default costs lives in
``docs/adr/0024-a-bool-score-reads-its-cells-text``.
"""
# pylint: disable=C0116,W0212,W0621
import pathlib
import textwrap

import pytest
from gain.genomic_resources.genomic_scores import (
    AlleleScore,
    PositionScore,
    build_score_from_resource,
)
from gain.genomic_resources.histogram import (
    CategoricalHistogram,
    CategoricalHistogramConfig,
)
from gain.genomic_resources.implementations.genomic_scores_impl import scan
from gain.genomic_resources.testing import (
    build_filesystem_test_resource,
    setup_directories,
    setup_vcf,
)
from gain.genomic_resources.testing.builders import (
    PositionScoreBuilder,
    a_position_score,
)


def _bool_builder(data: str) -> PositionScoreBuilder:
    """A one-column ``bool`` position score over the authored rows.

    Returns the BUILDER, not the resource, so a test that needs one more
    knob (``na_values``, a histogram) adds it rather than restating the
    chain -- the builders being immutable, that cannot drift.
    """
    return (
        a_position_score()
        .with_score("flag", "bool")
        .with_data(data)
    )


def _bool_score(tmp_path: pathlib.Path, data: str) -> PositionScore:
    """The common case: build that resource and open it."""
    score = PositionScore(_bool_builder(data).build_resource(tmp_path))
    score.open()
    return score


@pytest.mark.parametrize("text,expected", [
    ("True", True), ("true", True), ("TRUE", True), ("1", True),
    ("False", False), ("false", False), ("FALSE", False), ("0", False),
])
def test_the_accepted_bool_spellings(
    tmp_path: pathlib.Path, text: str, expected: bool,
) -> None:
    """The closed set, read end to end rather than off the parser.

    The four false spellings are the defect: each of them answered ``True``.
    ``0`` and ``1`` are in the set because a machine-written table spells a
    flag that way at least as often as it spells it ``True``; every other
    numeric text is not a bool and is refused.
    """
    score = _bool_score(tmp_path, f"""
        chrom  pos_begin  pos_end  flag
        chr1   10         19       {text}
        """)

    assert score.fetch_position_scores("chr1", 12, ["flag"]) == [expected]


@pytest.mark.parametrize("cell", ["yes", "EMPTY"])
def test_an_unreadable_bool_cell_is_a_logged_non_value(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture, cell: str,
) -> None:
    """Text outside the set is reported and skipped, not guessed at.

    No new refusal mechanism: this is the same broad guard in
    ``parse_value`` that turns a malformed number into a logged non-value.
    ``yes`` is the interesting spelling because it is exactly what the old
    truthiness parser answered ``True`` for.

    ``EMPTY`` renders as ``.``, and it takes this path too -- a ``bool``
    score declares no default NA sentinels, so the missing-value tokens
    reach the parser like any other text.  Same ``None`` value either way;
    what the empty default costs is the report.  See ADR 0024, and
    test_a_configured_na_value_silences_a_missing_bool_cell for the way out.
    """
    score = _bool_score(tmp_path, f"""
        chrom  pos_begin  pos_end  flag
        chr1   10         19       {cell}
        """)

    with caplog.at_level("ERROR"):
        assert score.fetch_position_scores("chr1", 12, ["flag"]) == [None]

    assert "unable to parse" in caplog.text
    assert "flag" in caplog.text


def test_a_configured_na_value_silences_a_missing_bool_cell(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """The way out for a resource whose bool column is sparse.

    ``na_values`` is tested BEFORE the parser is called, so a declared
    sentinel is a non-value rather than a refusal, and the per-cell
    traceback goes.

    Pins PRE-EXISTING behaviour, not gain#1192 -- the NA check has always
    come first, and this passes on either side of the fix.  It is here
    because the empty default is only defensible if this escape hatch
    works, so the two belong in one file.
    """
    resource = (
        _bool_builder("""
            chrom  pos_begin  pos_end  flag
            chr1   10         19       EMPTY
            """)
        .with_na_values(".")
        .build_resource(tmp_path)
    )
    score = PositionScore(resource)
    score.open()

    with caplog.at_level("ERROR"):
        assert score.fetch_position_scores("chr1", 12, ["flag"]) == [None]

    assert "unable to parse" not in caplog.text


def test_a_bool_score_declares_no_default_na_values(
    tmp_path: pathlib.Path,
) -> None:
    """Guard on the decision, not only on the behaviour it produces.

    Giving ``bool`` the numeric types' sentinels is the obvious tidy-up of
    the refusal above, and it is a deployment decision rather than a code
    one: ``na_values`` is serialized into a resource's statistics hash, so
    the edit alone invalidates the statistics of every bool score published
    anywhere.  ADR 0024 carries the measurement; this asserts the state it
    decided on, so that changing it is deliberate.
    """
    resource = _bool_builder("""
        chrom  pos_begin  pos_end  flag
        chr1   10         19       True
        """).build_resource(tmp_path)

    score_def = PositionScore(resource).score_definitions["flag"]

    assert score_def.na_values == set()


def _dbsnp_shaped_flag_score(tmp_path: pathlib.Path) -> AlleleScore:
    """A VCF ``Flag`` that the resource's ``scores:`` block types ``bool``.

    dbSNP's shape, and the one that makes this parser shared rather than
    text-only: the config-override branch of ``parse_vcf_scoredefs`` takes
    ``value_parser`` from the CONFIG-derived definition, not from the
    header-derived one.  So a VCF score declared ``type: bool`` runs the
    very parser the text tables use, over whatever pysam decoded.

    Hand-rolled rather than built with ``a_vcf_info_score()``, which emits
    no ``scores:`` block at all and so cannot express the override this
    exists to exercise.
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
    score = build_score_from_resource(build_filesystem_test_resource(tmp_path))
    assert isinstance(score, AlleleScore)
    return score


def test_a_vcf_flag_declared_bool_in_config_still_reads_presence_as_true(
    tmp_path: pathlib.Path,
) -> None:
    """The constraint the text fix must not break.

    A ``Flag`` says what it means by BEING THERE, and pysam decodes that to
    a real ``True`` rather than to any text.  So the parser has to hand back
    a value that is already a ``bool``, which a table of spellings alone
    does not: ``True`` is not the string ``"1"``.

    This is the whole of the VCF exposure.  There is no separate flag parser
    to leave alone -- protecting this path means handling an already-parsed
    value inside the one parser, which is what every other value type's
    parser does for free.
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
    passthrough carries each of them.

    Predates gain#1192 and is unchanged by it (``bool(False)`` was also
    ``False``).  Pinned anyway: the presence test alone cannot tell a
    passthrough from a parser that answers ``True`` for anything bool-ish.
    """
    score = _dbsnp_shaped_flag_score(tmp_path).open()

    assert score.fetch_allele_scores("chr1", 6, "A", "T", ["RV"]) == \
        {"RV": False}


def test_a_bool_columns_categorical_histogram_counts_both_values(
    tmp_path: pathlib.Path,
) -> None:
    """The defect reached the built STATISTICS, not only the reads.

    A bool score's histogram is categorical, so it bins the parsed value --
    which meant every row of a text bool column landed in the ``True`` bin
    and the histogram published with the resource said the flag was always
    set.  A resource's statistics are what its info page shows, so this was
    the more visible half of the bug and the one a reader had no way to
    distrust.

    Weighted by covered bases (a position score's records reduce by extent),
    so the counts follow the record widths rather than the row count.
    """
    resource = (
        _bool_builder("""
            chrom  pos_begin  pos_end  flag
            chr1   1          10       True
            chr1   11         30       False
            """)
        .with_histogram({"type": "categorical"})
        .build_resource(tmp_path)
    )

    hists = scan.do_histogram(
        resource, {"flag": CategoricalHistogramConfig()}, "chr1", 1, 30)

    hist = hists["flag"]
    assert isinstance(hist, CategoricalHistogram)
    assert hist.display_values == {True: 10, False: 20}
