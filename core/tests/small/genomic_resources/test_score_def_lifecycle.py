"""The scoredef lifecycle, as functions rather than ``GenomicScore`` methods.

Parsing a ``scores:`` block, validating it against a table's header and
filling in what a definition cannot decide for itself used to be private
methods on ``GenomicScore`` (gain#1044).  Only one of them was ever
polymorphic, and only through the class attribute ``DEFAULT_AGGREGATORS`` --
so all three are functions here, parametrized by what they used to read off
``self``, and the tests below can state their contracts without building a
score, a resource or a table.
"""
# pylint: disable=C0116,W0212,W0621

from gain.genomic_resources.genomic_scores import GenomicScore, PositionScore
from gain.genomic_resources.repository import GR_CONF_FILE_NAME, GenomicResource
from gain.genomic_resources.score_def import (
    build_genomic_score_schema,
    finish_scoredefs,
    parse_scoredef_config,
    validate_scoredefs,
)
from gain.genomic_resources.testing import build_inmemory_test_resource


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
