"""One sentence for "this resource does not define that score" (gain#1112).

A caller naming a score the resource does not define makes the same mistake
whichever surface it reaches: an aggregation request, a read, a histogram
accessor shared with gene scores, a filter expression.  These pin that every
one of them says so in the same words.

Asserted through the public surfaces, not through the helper that builds
the sentence: what a caller sees is the contract, and a surface that
re-inlined its own copy of the words would still pass a test of the helper.
"""
from __future__ import annotations

import pathlib
from collections.abc import Callable

import gain
import pytest
from gain.gene_scores.gene_scores import (
    GeneScore,
    build_gene_score_from_resource,
)
from gain.genomic_resources.aggregators import PositionScoreAggregationQuery
from gain.genomic_resources.genomic_scores import PositionScore
from gain.genomic_resources.score_filter import ScoreFilterError
from gain.genomic_resources.testing.builders import (
    a_gene_score,
    a_grr,
    a_position_score,
)

from tests.small.genomic_resources.conftest import phrase_sites


@pytest.fixture
def position(tmp_path: pathlib.Path) -> PositionScore:
    repo = a_grr().with_resource("pos", (
        a_position_score()
        .with_score("s", "float")
        .with_score("t", "float")
        .with_data("""
            chrom  pos_begin  pos_end  s    t
            1      10         10       0.5  1.5
        """)
    )).build_repo(tmp_path)
    return PositionScore(repo.get_resource("pos")).open()


@pytest.fixture
def gene(tmp_path: pathlib.Path) -> GeneScore:
    repo = a_grr().with_resource("gene", (
        a_gene_score()
        .with_score("s")
        .with_score("t")
        .with_data("""
            gene  s  t
            G1    1  2
        """)
    )).build_repo(tmp_path / "genes")
    return build_gene_score_from_resource(repo.get_resource("gene"))


_POSITION_SURFACES: dict[str, Callable[[PositionScore], object]] = {
    "aggregate_region":
        lambda score: score.aggregate_region("1", 10, 10, ["nope"]),
    "resolve_aggregation_queries":
        lambda score: score.resolve_aggregation_queries(
            [PositionScoreAggregationQuery("nope")]),
    "fetch_region_segments_scores":
        lambda score: list(score.fetch_region_segments_scores(
            "1", 10, 10, scores=["nope"])),
    "get_scores_at_position":
        lambda score: score.get_scores_at_position(
            "1", 10, scores=["nope"]),
    "get_score_range":
        lambda score: score.get_score_range("nope"),
    "get_histogram_filename":
        lambda score: score.get_histogram_filename("nope"),
}


@pytest.mark.parametrize("surface", sorted(_POSITION_SURFACES))
def test_every_genomic_score_surface_refuses_in_one_sentence(
    position: PositionScore, surface: str,
) -> None:
    with pytest.raises(ValueError) as excinfo:
        _POSITION_SURFACES[surface](position)

    assert str(excinfo.value) == (
        "score 'nope' is not defined by resource 'pos'; it has ['s', 't']")


def test_several_undefined_scores_are_named_together_once_each(
    position: PositionScore,
) -> None:
    with pytest.raises(ValueError) as excinfo:
        list(position.fetch_region_segments_scores(
            "1", 10, 10, scores=["zz", "s", "aa", "zz"]))

    assert str(excinfo.value) == (
        "scores ['aa', 'zz'] are not defined by resource 'pos'; "
        "it has ['s', 't']")


@pytest.mark.parametrize("method", [
    "get_score_range", "get_histogram_filename", "get_score_histogram",
    "get_x_scale", "get_y_scale",
])
def test_a_gene_score_refuses_in_the_same_sentence(
    gene: GeneScore, method: str,
) -> None:
    with pytest.raises(ValueError) as excinfo:
        getattr(gene, method)("nope")

    assert str(excinfo.value) == (
        "score 'nope' is not defined by resource 'gene'; it has ['s', 't']")


def test_the_undefined_score_sentence_is_written_in_exactly_one_place(
) -> None:
    """The tests above cannot see two copies that agree; this can.

    Scanned across all of ``gain``, not one package: the surfaces live in
    several, and a fence narrower than that would miss a copy in the one it
    did not scan.
    """
    sites = phrase_sites(
        pathlib.Path(gain.__file__).parent, "not defined by resource",
        min_sources=100)

    assert sites == ["genomic_resources/resource_errors.py:1"]


def test_a_filter_names_the_expression_then_states_the_same_rule(
    position: PositionScore,
) -> None:
    # Still its own type: a caller catching a bad filter by type must keep
    # catching this one.
    with pytest.raises(ScoreFilterError) as excinfo:
        position.compile_filter("nope > 1")

    assert str(excinfo.value) == (
        "filter names 'nope': score 'nope' is not defined by resource "
        "'pos'; it has ['s', 't']")
