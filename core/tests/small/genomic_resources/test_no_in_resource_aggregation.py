# pylint: disable=C0116
"""Aggregation belongs to the annotators, not to the score resources (#267).

A score resource fetches and exposes records; the *annotator* applies the
configured aggregator to them.  An earlier design put a second, self-contained
aggregation engine inside the resources -- methods that built their own
aggregators and ran their own fetch-and-aggregate loops, plus the query and
aggregate-holder types supporting them.  That engine was superseded and
removed; this pins it out, so a resurrected ``fetch_scores_agg`` fails here
instead of quietly re-splitting aggregation across two layers.
"""
from gain.genomic_resources import genomic_scores
from gain.genomic_resources.genomic_scores import AlleleScore, PositionScore

# Every name of the superseded in-resource engine, per class.
REMOVED_ALLELE_SCORE_METHODS = {
    "fetch_scores_agg",
    "build_scores_agg",
}
REMOVED_POSITION_SCORE_METHODS = {
    "fetch_scores_agg",
    "_build_scores_agg",
    "get_region_scores",
}

# The query / aggregate-holder types that only that engine used.
REMOVED_MODULE_LEVEL_TYPES = {
    "AlleleScoreQuery",
    "AlleleScoreAggr",
    "PositionScoreQuery",
    "PositionScoreAggr",
    "ScoreQuery",
}


def test_allele_score_has_no_aggregation_methods() -> None:
    assert not (REMOVED_ALLELE_SCORE_METHODS & set(dir(AlleleScore)))


def test_position_score_has_no_aggregation_methods() -> None:
    assert not (REMOVED_POSITION_SCORE_METHODS & set(dir(PositionScore)))


def test_genomic_scores_exports_no_query_or_aggregate_types() -> None:
    assert not (REMOVED_MODULE_LEVEL_TYPES & set(vars(genomic_scores)))
