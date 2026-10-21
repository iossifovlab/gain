"""Pin the offline claims of the "Position scores" page.

The tests run the code that the page describes and assert the results
that the page states. They read no ``.rst`` file. The page is
``docs/source/development/resources/position_scores.rst``. The examples of
the page use ``hg38/scores/phastCons100way``; the tests use mini-GRR
position scores instead. ``mini_positionscore_tsv_1_twoscores`` holds two
scores, a number ``pos_tsv_1`` and a string ``pos_tsv_1_st``, and has no
row at ``chr1:4``. The tests pin these claims:

- Both builders give a closed ``PositionScore``, and the generic builder
  gives one too.
- The plural reads take a list of score ids and return tuples in that
  order, and all the scores when the list is left out. The singular reads
  return single values.
- ``get_scores_in_region`` yields one tuple for each position.
- A position with no value gives ``None``.
- The region aggregation returns one value for each query, in query order,
  and uses the default aggregator of the score when the query names none.
- The binned reads give one summary for each bin.
"""
import types

import pytest
from gain import grr
from gain.genomic_resources.aggregators import PositionScoreAggregationQuery
from gain.genomic_resources.genomic_scores import PositionScore
from gain.genomic_resources.repository import GenomicResourceRepo

TWO_SCORES = "mini_positionscore_tsv_1_twoscores"
TABIX_SCORE = "mini_positionscore_bedgraph_tabix_0"
TABIX_SCORE_ID = "pos_bedgraph_0"


def test_builders_give_a_closed_position_score(
    repo: GenomicResourceRepo,
) -> None:
    """The specific and the generic builders give a ``PositionScore``."""
    from_id = grr.build_position_score_from_resource_id(TABIX_SCORE, repo)
    from_resource = grr.build_position_score_from_resource(
        repo.get_resource(TABIX_SCORE))
    generic = grr.build_score_from_resource_id(TABIX_SCORE, repo)

    for score in (from_id, from_resource, generic):
        assert isinstance(score, PositionScore)
        assert not score.is_open()


def test_with_block_reads_a_position(repo: GenomicResourceRepo) -> None:
    """The page's ``with`` example reads the scores at one position."""
    with grr.build_position_score_from_resource_id(
            TABIX_SCORE, repo).open() as score:
        assert score.get_scores_at_position("chr1", 3) == (0.2,)

    assert not score.is_open()


def test_plural_read_returns_all_scores_by_default(
    repo: GenomicResourceRepo,
) -> None:
    """Without a list, the tuple holds every score of the resource."""
    with grr.build_position_score_from_resource_id(
            TWO_SCORES, repo).open() as score:
        assert score.get_scores_at_position("chr1", 3) == (0.2, "good")


def test_plural_read_follows_the_order_of_the_list(
    repo: GenomicResourceRepo,
) -> None:
    """The tuple follows the order of the requested score ids."""
    with grr.build_position_score_from_resource_id(
            TWO_SCORES, repo).open() as score:
        assert score.get_scores_at_position(
            "chr1", 3, ["pos_tsv_1_st", "pos_tsv_1"]) == ("good", 0.2)


def test_singular_read_returns_one_value(repo: GenomicResourceRepo) -> None:
    """``get_score_at_position`` returns a value, not a tuple."""
    with grr.build_position_score_from_resource_id(
            TWO_SCORES, repo).open() as score:
        assert score.get_score_at_position("chr1", 3, "pos_tsv_1") == 0.2


def test_region_read_yields_one_tuple_per_position(
    repo: GenomicResourceRepo,
) -> None:
    """``get_scores_in_region`` yields a tuple for each position."""
    with grr.build_position_score_from_resource_id(
            TWO_SCORES, repo).open() as score:
        result = score.get_scores_in_region("chr1", 3, 5)

        assert isinstance(result, types.GeneratorType)
        assert list(result) == [(0.2, "good"), (None, None), (0.4, "good")]
        assert list(score.get_score_in_region(
            "chr1", 3, 5, "pos_tsv_1")) == [0.2, None, 0.4]


def test_position_without_a_value_gives_none(
    repo: GenomicResourceRepo,
) -> None:
    """``chr1:4`` has no row, so each score is ``None``, not zero."""
    with grr.build_position_score_from_resource_id(
            TWO_SCORES, repo).open() as score:
        assert score.get_scores_at_position("chr1", 4) == (None, None)
        assert score.get_score_at_position("chr1", 4, "pos_tsv_1") is None
        assert score.get_score_at_position("chr1", 1, "pos_tsv_1") == 0.0


def test_region_aggregation_follows_the_query_order(
    repo: GenomicResourceRepo,
) -> None:
    """One score with two aggregators gives two values, in query order."""
    with grr.build_position_score_from_resource_id(
            TABIX_SCORE, repo).open() as score:
        values = score.get_scores_in_region_agg("chr1", 1, 4, [
            PositionScoreAggregationQuery(TABIX_SCORE_ID, "max"),
            PositionScoreAggregationQuery(TABIX_SCORE_ID, "min"),
        ])

    assert values == pytest.approx((0.3, 0.0))


def test_region_aggregation_uses_the_default_aggregator(
    repo: GenomicResourceRepo,
) -> None:
    """A float score with no aggregator falls back to ``mean``."""
    assert PositionScore.DEFAULT_AGGREGATORS["float"] == "mean"
    with grr.build_position_score_from_resource_id(
            TABIX_SCORE, repo).open() as score:
        value = score.get_score_in_region_agg("chr1", 1, 4, TABIX_SCORE_ID)

    assert value == pytest.approx(0.15)


def test_binned_reads_give_one_summary_per_bin(
    repo: GenomicResourceRepo,
) -> None:
    """Each bin is a ``(begin, end, value)`` summary of its positions."""
    with grr.build_position_score_from_resource_id(
            TABIX_SCORE, repo).open() as score:
        single = list(score.get_score_in_bins(
            "chr1", 1, 4, 2, TABIX_SCORE_ID, "max"))
        plural = list(score.get_scores_in_bins("chr1", 1, 4, 2, [
            PositionScoreAggregationQuery(TABIX_SCORE_ID, "max")]))

    assert [(b, e) for b, e, _ in single] == [(1, 2), (3, 4)]
    assert [v for _, _, v in single] == pytest.approx([0.1, 0.3])
    assert [(b, e) for b, e, _ in plural] == [(1, 2), (3, 4)]
    assert [v[0] for _, _, v in plural] == pytest.approx([0.1, 0.3])
