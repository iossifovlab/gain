"""``FragmentScore.get_scores_in_bins`` -- the fragment kind's binned read.

The fold it answers through is pinned on literal records in
``test_binned_fold.py``; this file pins what the READ adds: which fragments
reach which bin (start-only), one column per query, the score's default for
an unnamed aggregator, the refusals, and the absent contig.

The toy resource has two scores, the shape of a single-cell fragment file:
``cell`` -- a barcode every fragment carries, a ``str`` -- and ``count``, an
``int``.  It holds contig ``1`` only; ``2`` is the contig it lacks.  Read in
bins of 10, its fragments fall as follows:

==============  ======  =====  ======================================
fragment        cell    count  start bin
==============  ======  =====  ======================================
``(3, 12)``     AAA     2      0 -- runs into bin 1, counted in 0 only
``(10, 25)``    BBB     5      0 -- starts on bin 0's last base
``(11, 14)``    CCC     1      1 -- starts on bin 1's first base
``(15, 40)``    AAA     3      1
``(33, 34)``    DDD     4      3
==============  ======  =====  ======================================

Bin 2 (``[21, 30]``) has no fragment starting in it, though two run through
it.
"""
# pylint: disable=W0621,C0116
import math
import pathlib

import numpy as np
import pytest
from gain.genomic_resources.aggregators import ScoreAggregationQuery
from gain.genomic_resources.genomic_scores import FragmentScore
from gain.genomic_resources.genomic_scores.aggregation import fold_into_bins
from gain.genomic_resources.testing.builders import (
    FragmentScoreBuilder,
    a_fragment_score,
)

FRAGMENTS = """
    chrom  pos_begin  pos_end  cell  count
    1      3          12       AAA   2
    1      10         25       BBB   5
    1      11         14       CCC   1
    1      15         40       AAA   3
    1      33         34       DDD   4
"""


def _toy_fragments() -> FragmentScoreBuilder:
    return (
        a_fragment_score()
        .with_score("cell", "str")
        .with_score("count", "int")
        .with_data(FRAGMENTS)
    )


@pytest.fixture
def fragments(tmp_path: pathlib.Path) -> FragmentScore:
    return FragmentScore(_toy_fragments().build_resource(tmp_path))


def test_a_count_of_the_barcode_is_the_fragments_starting_in_each_bin(
    fragments: FragmentScore,
) -> None:
    """Every fragment carries a barcode, so counting it counts fragments."""
    with fragments.open() as score:
        block = score.get_scores_in_bins(
            "1", 1, 40, 10, [ScoreAggregationQuery("cell", "count")])

    np.testing.assert_array_equal(block, [[2.0], [2.0], [0.0], [1.0]])


def test_several_queries_answer_one_column_each_in_query_order(
    fragments: FragmentScore,
) -> None:
    with fragments.open() as score:
        block = score.get_scores_in_bins("1", 1, 40, 10, [
            ScoreAggregationQuery("count", "sum"),
            ScoreAggregationQuery("cell", "count"),
            ScoreAggregationQuery("count", "max"),
        ])

    np.testing.assert_array_equal(block, [
        [7.0, 2.0, 5.0],
        [4.0, 2.0, 3.0],
        [0.0, 0.0, math.nan],
        [4.0, 1.0, 4.0],
    ])


def test_several_queries_are_answered_from_one_read_of_the_region(
    fragments: FragmentScore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    with fragments.open() as score:
        reads: list[tuple[object, ...]] = []
        real = score.fetch_region_segments_scores

        def counting(*args: object, **kwargs: object) -> object:
            reads.append(args)
            return real(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(score, "fetch_region_segments_scores", counting)
        score.get_scores_in_bins("1", 1, 40, 10, [
            ScoreAggregationQuery("count", "sum"),
            ScoreAggregationQuery("cell", "count"),
            ScoreAggregationQuery("count", "mean"),
        ])

    assert len(reads) == 1


def test_a_fragment_spanning_bins_is_counted_once_in_its_start_bin(
    fragments: FragmentScore,
) -> None:
    """``(3, 12)`` and ``(10, 25)`` reach bin 1, ``(15, 40)`` bins 2-3.

    None of them is counted anywhere but where it begins.
    """
    with fragments.open() as score:
        block = score.get_scores_in_bins(
            "1", 11, 40, 10, [ScoreAggregationQuery("cell", "count")])

    np.testing.assert_array_equal(block, [[2.0], [0.0], [1.0]])


def test_adjacent_calls_split_on_a_bin_edge_tile_the_whole(
    fragments: FragmentScore,
) -> None:
    queries = [
        ScoreAggregationQuery("cell", "count"),
        ScoreAggregationQuery("count", "max"),
    ]
    with fragments.open() as score:
        left = score.get_scores_in_bins("1", 2, 10, 10, queries)
        right = score.get_scores_in_bins("1", 11, 37, 10, queries)
        whole = score.get_scores_in_bins("1", 2, 37, 10, queries)

    np.testing.assert_array_equal(np.vstack([left, right]), whole)


def test_an_unnamed_aggregator_is_the_scores_configured_default(
    tmp_path: pathlib.Path,
) -> None:
    """Configured as ``sum``, which the class default (``max``) is not."""
    resource = (
        _toy_fragments()
        .with_aggregator("sum", score_id="count")
        .build_resource(tmp_path))
    with FragmentScore(resource).open() as score:
        block = score.get_scores_in_bins(
            "1", 1, 40, 10, [ScoreAggregationQuery("count")])

    np.testing.assert_array_equal(block, [[7.0], [4.0], [0.0], [4.0]])


def test_an_unnamed_aggregator_on_an_unconfigured_score_is_the_kinds(
    fragments: FragmentScore,
) -> None:
    """An ``int`` fragment score reduces by ``max`` unless configured."""
    with fragments.open() as score:
        block = score.get_scores_in_bins(
            "1", 1, 40, 10, [ScoreAggregationQuery("count")])

    np.testing.assert_array_equal(block, [[5.0], [3.0], [math.nan], [4.0]])


@pytest.mark.parametrize("aggregator", ["sum", "mean", "max", "product"])
def test_a_numeric_only_aggregator_on_the_barcode_is_refused(
    fragments: FragmentScore, aggregator: str,
) -> None:
    with fragments.open() as score, pytest.raises(
            ValueError, match="requires a numeric value type"):
        score.get_scores_in_bins(
            "1", 1, 40, 10, [ScoreAggregationQuery("cell", aggregator)])


def test_a_default_that_cannot_answer_a_float_is_refused(
    fragments: FragmentScore,
) -> None:
    """A ``str`` score's default is ``join``, which the fold does not take."""
    with fragments.open() as score, pytest.raises(
            ValueError, match=r"join\(,\).*float"):
        score.get_scores_in_bins(
            "1", 1, 40, 10, [ScoreAggregationQuery("cell")])


def test_a_contig_the_resource_lacks_answers_the_all_empty_block(
    fragments: FragmentScore,
) -> None:
    with fragments.open() as score:
        block = score.get_scores_in_bins("2", 5, 34, 10, [
            ScoreAggregationQuery("cell", "count"),
            ScoreAggregationQuery("count", "sum"),
            ScoreAggregationQuery("count", "mean"),
        ])

    np.testing.assert_array_equal(block, [[0.0, 0.0, math.nan]] * 4)


def test_a_contig_the_resource_lacks_still_refuses_a_bad_request(
    fragments: FragmentScore,
) -> None:
    with fragments.open() as score, pytest.raises(
            ValueError, match="requires a numeric value type"):
        score.get_scores_in_bins(
            "2", 1, 40, 10, [ScoreAggregationQuery("cell", "sum")])


@pytest.mark.parametrize("start,end", [(0, 40), (20, 19)])
def test_a_region_no_genomic_span_can_mean_is_refused(
    fragments: FragmentScore, start: int, end: int,
) -> None:
    with fragments.open() as score, pytest.raises(
            ValueError, match=r"1-based|precedes its start"):
        score.get_scores_in_bins(
            "1", start, end, 10, [ScoreAggregationQuery("cell", "count")])


@pytest.mark.parametrize("bin_size", [0, -10])
def test_a_bin_size_below_one_is_refused_naming_the_resource(
    fragments: FragmentScore, bin_size: int,
) -> None:
    with fragments.open() as score, pytest.raises(
            ValueError, match=rf"<{fragments.resource_id}>.*size {bin_size}"):
        score.get_scores_in_bins(
            "1", 1, 40, bin_size, [ScoreAggregationQuery("cell", "count")])


def test_the_read_is_the_fold_over_the_fragments_starting_in_the_region(
    fragments: FragmentScore,
) -> None:
    """The agreement: one resource, read binned and folded by hand."""
    queries = [
        ScoreAggregationQuery("cell", "count"),
        ScoreAggregationQuery("count", "sum"),
        ScoreAggregationQuery("count", "median"),
    ]
    with fragments.open() as score:
        starting = list(score.get_fragment_scores_starting_in_region(
            "1", 4, 37, scores=["cell", "count"]))
        read = score.get_scores_in_bins("1", 4, 37, 10, queries)

    folded = fold_into_bins(
        [
            (begin, query, values[column])
            for begin, _, values in starting
            for query, column in enumerate([0, 1, 1])
        ],
        start=4, end=37, bin_size=10,
        aggregators=["count", "sum", "median"])
    np.testing.assert_array_equal(read, folded)
