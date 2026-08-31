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
import importlib
import pkgutil
from types import ModuleType

from gain.genomic_resources import genomic_scores
from gain.genomic_resources.genomic_scores import (
    AlleleScore,
    PositionScore,
)

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


def _score_modules() -> list[ModuleType]:
    """``genomic_scores`` and every module in it.

    Asking the package alone would not do.  ``vars()`` on a package sees its
    ``__init__`` namespace, and since the gain#902 split that namespace is
    nineteen deliberate re-exports -- so a resurrected ``PositionScoreQuery``
    would sit in ``position``, where the class it served lives, and this test
    would never see it.  Walked rather than listed, so a seventh module added
    to the package is covered without anyone remembering to come here.
    """
    return [genomic_scores, *(
        importlib.import_module(f"{genomic_scores.__name__}.{module.name}")
        for module in pkgutil.iter_modules(genomic_scores.__path__)
    )]


def test_genomic_scores_exports_no_query_or_aggregate_types() -> None:
    found = {
        f"{module.__name__}.{name}"
        for module in _score_modules()
        for name in REMOVED_MODULE_LEVEL_TYPES & set(vars(module))
    }
    assert found == set(), (
        f"the superseded in-resource aggregation engine is back: {found}"
    )
