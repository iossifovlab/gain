"""The scoredef lifecycle, as functions rather than ``GenomicScore`` methods.

Parsing a ``scores:`` block, validating it against a table's header and
filling in what a definition cannot decide for itself used to be private
methods on ``GenomicScore`` (gain#1044).  Only one of them was ever
polymorphic, and only through the class attribute ``DEFAULT_AGGREGATORS`` --
so all three are functions here, parametrized by what they used to read off
``self``, and a test can state a function's contract without building a
score, a resource or a table.  The refusals a score class applies at its
construction convergence point are pinned through the class, since that is
the seam a reader of the config meets them at.
"""
# pylint: disable=C0116,W0212,W0621

import pathlib

import pytest
from gain.genomic_resources.genomic_scores import (
    AlleleScore,
    GenomicScore,
    PositionScore,
)
from gain.genomic_resources.repository import GR_CONF_FILE_NAME, GenomicResource
from gain.genomic_resources.resource_errors import MalformedResourceError
from gain.genomic_resources.score_def import (
    build_genomic_score_schema,
    finish_scoredefs,
    parse_scoredef_config,
    validate_scoredefs,
)
from gain.genomic_resources.testing import build_inmemory_test_resource
from gain.genomic_resources.testing.builders import (
    a_grr,
    a_position_score,
    a_vcf_info_score,
)


def test_an_unstated_aggregator_is_filled_from_the_callers_mapping() -> None:
    config = {"scores": [{"id": "s", "type": "float", "column_index": 3}]}

    score_defs = finish_scoredefs(
        parse_scoredef_config(config), {"float": "mean"})

    assert score_defs["s"].aggregator == "mean"


def _a_score_named(spelling: str) -> PositionScore:
    res: GenomicResource = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: f"""
            type: position_score
            table:
                filename: data.mem
                zero_based: false
            scores:
                - id: score
                  type: float
                  {spelling}: score1
        """,
        "data.mem": """
            chrom  pos_begin  score1
            1      10         0.1
        """,
    })
    return PositionScore(res)


def test_the_legacy_name_spelling_is_rewritten_in_the_config_it_is_given(
) -> None:
    score = _a_score_named("name")
    score.table.open()
    assert "column_name" not in score.config["scores"][0]

    validate_scoredefs(score.config, score.table, score.resource)

    assert score.config["scores"][0]["column_name"] == "score1"


def test_the_class_get_schema_delegates_to_the_extracted_builder() -> None:
    """``GenomicScore.get_schema`` stays public, and delegates here.

    The kinds override it -- each deep-copies this one and splices in its
    ``aggregator`` -- so the class method is the hook and the function is the
    declaration.  This pins only the delegation; what the schema declares is
    pinned by the config-validation suites that consume it.
    """
    assert build_genomic_score_schema() == GenomicScore.get_schema()


def test_each_call_builds_the_schema_afresh() -> None:
    """Two callers never share the dict they may splice into.

    ``PositionScore.get_schema()`` deep-copies before splicing, and
    test_using_a_cached_schema_does_not_change_it relies on a freshly-built
    schema comparing equal to a cached one -- both of which need this to
    return a new dict rather than one module-level object.
    """
    first = build_genomic_score_schema()
    second = build_genomic_score_schema()

    assert first is not second
    assert first["scores"] is not second["scores"]


def test_an_aggregator_the_parser_rejects_is_refused_at_construction(
    tmp_path: pathlib.Path,
) -> None:
    """A ``scores:`` ``aggregator:`` that cannot be built names the CONFIG.

    ``join(a)(b)`` passes the config schema and fails the parser; this
    pins that it is refused where the score is built, before ``open()``,
    in the shared configuration wording -- not at the first read.
    """
    repo = a_grr().with_resource("two", (
        a_position_score()
        .with_score("s", "str")
        .with_aggregator("join(a)(b)")
        .with_data("""
            chrom  pos_begin  s
            chr1   10         x
        """)
    )).build_repo(tmp_path)

    with pytest.raises(MalformedResourceError) as excinfo:
        PositionScore(repo.get_resource("two"))

    # The whole sentence, because its two halves fence two things: the
    # address is the shared configuration prefix, and the detail quotes
    # the spelling AND the parser's own complaint about it.
    assert str(excinfo.value) == (
        "Invalid configuration: two: score 's' states aggregator "
        "'join(a)(b)', which cannot be built: Invalid aggregator "
        "definition: 'join(a)(b)'")


def test_a_vcf_derived_definition_is_held_to_the_same_aggregator_rule(
    tmp_path: pathlib.Path,
) -> None:
    """The VCF route converges on the one refusal, not a route of its own.

    ``_build_scoredefs`` dispatches on the table's type, and a VCF score's
    definitions leave that dispatch through the header merge, not through
    the plain ``scores:`` branch.  A check placed on the tabular branch
    alone would let this route construct, open and fail at the first read
    in the request family's words; this pins that it is refused at the
    point every route passes through.
    """
    res = (
        a_vcf_info_score()
        .with_score("s")
        .with_aggregator("join(a)(b)")
        .with_data("""
##fileformat=VCFv4.1
##INFO=<ID=s,Number=1,Type=String,Description="a label">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   10  .  A   T   .    .      s=x
""")
        .build_resource(tmp_path)
    )

    with pytest.raises(MalformedResourceError) as excinfo:
        AlleleScore(res)

    assert str(excinfo.value).startswith(
        f"Invalid configuration: {res.resource_id}: score 's' "
        f"states aggregator 'join(a)(b)', which cannot be built: ")


def test_a_parametrized_aggregator_the_parser_accepts_still_reduces(
    tmp_path: pathlib.Path,
) -> None:
    # The rule is "what the parser builds", not the schema's regex -- so
    # the spelling the schema and the parser both accept is untouched, all
    # the way to the value it reduces to.
    res = (
        a_position_score()
        .with_score("s", "str")
        .with_aggregator("join(,)")
        .with_data("""
            chrom  pos_begin  s
            chr1   10         x
            chr1   11         y
        """)
        .build_resource(tmp_path)
    )

    with PositionScore(res).open() as score:
        assert score.aggregate_region("chr1", 10, 11, ["s"]) == ["x,y"]
