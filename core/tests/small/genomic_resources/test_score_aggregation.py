# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""The region-aggregation machinery, apart from any score class.

``GenomicScore.aggregate_region`` is the orchestrator these three functions
serve, and ``test_aggregate_region`` pins what it answers.  What is pinned
HERE is what each piece promises the orchestrator on its own: which requests
resolve to which pairs, that every built aggregator is a fresh accumulator,
and -- the reason the fold takes ``weigh`` as an argument at all -- that the
fold applies the per-kind weight rule it is HANDED rather than deriving one
of its own.

Nothing here knows what a KIND is, which is why nothing here pins clipping:
whether a record is cut down to the query window before it reaches the fold
is settled by the kind's ``_aggregation_segments``, and pinned against the
score classes in ``test_aggregate_region`` and ``test_clip_span``.
"""
from __future__ import annotations

import pathlib
from collections.abc import Callable

import gain
import pytest
from gain.genomic_resources.aggregators import PositionScoreAggregationQuery
from gain.genomic_resources.genomic_scores import PositionScore
from gain.genomic_resources.genomic_scores.aggregation import (
    build_region_aggregator,
    distinct_score_ids,
    fold_region_segments,
    resolve_aggregator_requests,
)
from gain.genomic_resources.score_def import GenomicScoreDef, ScoreValue
from gain.genomic_resources.testing.builders import a_grr, a_position_score

_TWO_SCORES = """
    chrom  pos_begin  pos_end  s    t
    1      10         13       0.2  1
    1      14         14       0.8  2
"""


@pytest.fixture
def two(tmp_path: pathlib.Path) -> PositionScore:
    repo = a_grr().with_resource("two", (
        a_position_score()
        .with_score("s", "float")
        .with_score("t", "int")
        .with_data(_TWO_SCORES)
    )).build_repo(tmp_path)
    return PositionScore(repo.get_resource("two"))


@pytest.fixture
def score_definitions(two: PositionScore) -> dict[str, GenomicScoreDef]:
    # Real definitions, resolved defaults and all: `finish_scoredefs` is what
    # fills a float score's `mean`, and a request resolved against a
    # hand-built def would not be resolved against what production reads.
    return two.score_definitions


# The two per-kind weight rules, spelled out rather than taken off a score
# class on purpose: the fold applies what it is HANDED, and binding these
# to PositionScore would undercut the separation these tests exist to pin.
def _by_span(left: int, right: int) -> int:
    return right - left + 1


def _counts_once(left: int, right: int) -> int:
    return 1


def _fold(
    segments: list[tuple[int, int, list[ScoreValue]]],
    requests: list[tuple[str, str]],
    *,
    weigh: Callable[[int, int], int],
) -> list[ScoreValue]:
    """Drive the fold the way its caller does.

    The aggregators are built here, parallel to ``requests`` and before
    the segments are read, because that is the contract the fold's
    signature states -- retyping them per test would let a caller drift
    from it silently.
    """
    aggregators = [
        build_region_aggregator(score_id, aggregator, resource_id="two")
        for score_id, aggregator in requests
    ]
    return fold_region_segments(
        segments, aggregators, requests, weigh=weigh)


def test_a_bare_score_id_resolves_to_the_definitions_default(
    score_definitions: dict[str, GenomicScoreDef],
) -> None:
    assert resolve_aggregator_requests(
        ["s"],
        score_definitions=score_definitions,
        all_scores=["s", "t"],
        resource_id="two",
    ) == [("s", "mean")]


def test_one_fetch_serves_two_requests_for_the_same_score() -> None:
    # Two requests for one score share the fetch -- the segments carry ONE
    # column -- and keep separate accumulators.
    segments = [(10, 10, [0.2]), (11, 11, [0.8])]
    assert _fold(
        segments,
        [("s", "min"), ("s", "max")],
        weigh=_by_span,
    ) == [0.2, 0.8]


def test_the_fold_aggregates_the_segments_it_is_handed() -> None:
    # The fold does not decide for itself that the query window is
    # relevant: a record at [20, 25] reaching it is a record its caller
    # meant to aggregate, whatever window was asked for.  Which kind
    # clips BEFORE the fold, and why, is pinned where that decision is
    # now made -- on the score classes, by
    # test_a_record_the_query_clips_to_nothing_is_not_aggregated and
    # test_an_allele_point_outside_the_window_still_aggregates_once.
    assert _fold(
        [(20, 25, [0.7])],
        [("s", "count")],
        weigh=_counts_once,
    ) == [1]


def test_the_handed_weight_is_what_each_value_counts_for() -> None:
    # A span-weighted mean over two records of different widths: weighing
    # per RECORD would answer 0.5, per base pair 0.32.  The fold weighs
    # with the callable it was handed, and nothing else.
    assert _fold(
        [(10, 13, [0.2]), (14, 14, [0.8])],
        [("s", "mean")],
        weigh=_by_span,
    ) == [pytest.approx((0.2 * 4 + 0.8) / 5)]


def test_a_pair_names_the_aggregator_and_passes_through(
    score_definitions: dict[str, GenomicScoreDef],
) -> None:
    assert resolve_aggregator_requests(
        [("s", "max"), "t"],
        score_definitions=score_definitions,
        all_scores=["s", "t"],
        resource_id="two",
    ) == [("s", "max"), ("t", "mean")]


def test_no_request_list_asks_for_every_score(
    score_definitions: dict[str, GenomicScoreDef],
) -> None:
    # `None` means "all of them", and it is `all_scores` -- not the
    # definitions dict -- that says which those are and in what order.
    assert resolve_aggregator_requests(
        None,
        score_definitions=score_definitions,
        all_scores=["t", "s"],
        resource_id="two",
    ) == [("t", "mean"), ("s", "mean")]


def test_an_unknown_score_names_the_resource_and_what_it_has(
    score_definitions: dict[str, GenomicScoreDef],
) -> None:
    with pytest.raises(ValueError) as excinfo:
        resolve_aggregator_requests(
            ["nope"],
            score_definitions=score_definitions,
            all_scores=["s", "t"],
            resource_id="two",
        )
    assert str(excinfo.value) == (
        "score 'nope' is not defined by resource 'two'; it has ['s', 't']")


def test_a_score_with_no_default_aggregator_says_how_to_name_one(
    tmp_path: pathlib.Path,
) -> None:
    # `bool` is the one value type whose class default is deliberately
    # None: there is no reduction to pick on the caller's behalf.
    repo = a_grr().with_resource("flags", (
        a_position_score()
        .with_score("flag", "bool")
        .with_data("""
            chrom  pos_begin  pos_end  flag
            1      10         10       True
        """)
    )).build_repo(tmp_path)
    definitions = PositionScore(repo.get_resource("flags")).score_definitions

    with pytest.raises(ValueError) as excinfo:
        resolve_aggregator_requests(
            ["flag"],
            score_definitions=definitions,
            all_scores=["flag"],
            resource_id="flags",
        )
    assert str(excinfo.value) == (
        "score 'flag' of resource 'flags' has no default aggregator for "
        "value type 'bool'; name one explicitly as (score_id, aggregator)")


def test_an_unknown_aggregator_names_the_score_that_asked_for_it() -> None:
    # `Aggregator.build` raises a bare KeyError('mediann') on its own,
    # saying nothing about which score asked.
    with pytest.raises(ValueError) as excinfo:
        build_region_aggregator("s", "mediann", resource_id="two")
    assert str(excinfo.value) == (
        "score 's' of resource 'two' asks for aggregator 'mediann', "
        "which is not valid: 'mediann'")


def test_every_build_is_a_fresh_accumulator() -> None:
    # An aggregator is mutable and not thread-safe; two calls must not
    # hand back one accumulator that has already seen a value.
    first = build_region_aggregator("s", "max", resource_id="two")
    first.add(0.9)
    second = build_region_aggregator("s", "max", resource_id="two")

    assert second.get_final() is None


def test_a_bad_aggregator_is_refused_before_the_region_is_read(
    two: PositionScore,
) -> None:
    """The orchestrator builds every aggregator BEFORE it fetches.

    ``fetch_region_segments`` is not lazy -- its not-open and unknown-contig
    guards run when it is CALLED, not on the first ``next()`` -- so building
    the aggregators after the fetch would let a region complaint mask a
    misspelled aggregator.  An annotation config with a typo would then
    surface only once someone queried a contig the resource covers.
    """
    with two.open() as score, pytest.raises(ValueError) as excinfo:
        score.aggregate_region("nosuchchrom", 10, 14, [("s", "mediann")])

    assert str(excinfo.value) == (
        "score 's' of resource 'two' asks for aggregator 'mediann', "
        "which is not valid: 'mediann'")


def test_the_distinct_scores_keep_the_order_they_were_asked_for() -> None:
    # The fetch and the fold both index by this list, so its ORDER is what
    # pairs a request with its column.  `t` keeps the position its first
    # mention won, and `s` is not asked for twice -- these are the ids of
    # a request list naming t with max, s with mean, t with min and s with
    # count: one score asked for twice with two different aggregators.
    assert distinct_score_ids(["t", "s", "t", "s"]) == ["t", "s"]


# -- The two surfaces that resolve an aggregation request (gain#1087) ------
#
# ``resolve_aggregator_requests`` serves ``aggregate_region``'s list of
# score ids and ``(score_id, aggregator)`` pairs; ``PositionScore``'s
# logical plane serves ``PositionScoreAggregationQuery`` objects, which
# carry a ``none_value_replacement`` besides.  They are different request
# SHAPES asking the same two questions, and what these pin is that the
# answers are one statement rather than two that happen to coincide.


@pytest.fixture
def flags(tmp_path: pathlib.Path) -> PositionScore:
    # `bool` is the one value type whose class default is deliberately
    # None: there is no reduction to pick on the caller's behalf, so both
    # surfaces must refuse a `flag` no aggregator names.
    repo = a_grr().with_resource("flags", (
        a_position_score()
        .with_score("flag", "bool")
        .with_data("""
            chrom  pos_begin  pos_end  flag
            1      10         10       True
        """)
    )).build_repo(tmp_path)
    return PositionScore(repo.get_resource("flags"))


def _refusal(call: Callable[[], object]) -> str:
    with pytest.raises(ValueError) as excinfo:
        call()
    return str(excinfo.value)


def test_both_surfaces_refuse_an_unknown_score_in_the_same_words(
    flags: PositionScore,
) -> None:
    # One rule, one wording: there is nothing about "this resource does not
    # define that score" that depends on which shape asked.
    from_request_list = _refusal(lambda: resolve_aggregator_requests(
        ["nope"],
        score_definitions=flags.score_definitions,
        all_scores=["flag"],
        resource_id="flags",
    ))
    with flags.open() as score:
        from_query = _refusal(lambda: score.get_scores_in_region_agg(
            "1", 10, 10, [PositionScoreAggregationQuery("nope")]))

    assert from_request_list == from_query == (
        "score 'nope' is not defined by resource 'flags'; it has ['flag']")


def test_both_surfaces_state_the_missing_default_rule_identically(
    flags: PositionScore,
) -> None:
    """The RULE is shared; only the remedy names each surface's own API.

    A caller of ``aggregate_region`` writes a ``(score_id, aggregator)``
    pair and a caller of the plane writes it on the query, so telling each
    of them to do the other's thing would be wrong.  Everything up to that
    remedy -- which score, which resource, which value type, and that the
    ground is a missing default -- is one statement, and this is what says
    the two cannot drift apart again.
    """
    from_request_list = _refusal(lambda: resolve_aggregator_requests(
        ["flag"],
        score_definitions=flags.score_definitions,
        all_scores=["flag"],
        resource_id="flags",
    ))
    with flags.open() as score:
        from_query = _refusal(lambda: score.get_scores_in_region_agg(
            "1", 10, 10, [PositionScoreAggregationQuery("flag")]))

    rule, _, request_remedy = from_request_list.partition("; ")
    query_rule, _, query_remedy = from_query.partition("; ")

    assert rule == query_rule == (
        "score 'flag' of resource 'flags' has no default aggregator "
        "for value type 'bool'")
    assert request_remedy == "name one explicitly as (score_id, aggregator)"
    assert query_remedy == "name one on the query"


# The two refusals above, anchored by the part of each that carries the
# RULE rather than the surface's own remedy -- long enough to be
# unmistakable, short enough to survive an f-string's line breaks.
_REFUSAL_RULES = [
    "is not defined by resource",
    "has no default aggregator",
]

_STATED_IN = "genomic_resources/genomic_scores/aggregation.py"


def test_each_aggregation_refusal_is_written_in_exactly_one_place() -> None:
    """The tests above cannot see two copies that agree; this can.

    Two surfaces emitting the same words is what the pins can observe, and
    it is satisfied just as well by two copies -- which is how the missing
    default's remedy came to differ in the first place while the rule half
    still matched.  So the "stated once" half of gain#1087 is pinned where
    it lives: in the source, by counting the files that spell each rule.

    An intentional rewording goes red here, and should: the new wording
    wants re-anchoring, and the point of the trip is to notice whether a
    second file has picked the sentence up again.
    """
    package = pathlib.Path(gain.__file__).parent
    sources = sorted(package.rglob("*.py"))
    # Guard against a scan that silently matches nothing: this package is
    # hundreds of modules, and a fence over an empty list is not a fence.
    assert len(sources) > 100, len(sources)

    for rule in _REFUSAL_RULES:
        sites = sorted(
            str(path.relative_to(package))
            for path in sources
            if rule in path.read_text()
        )
        assert sites == [_STATED_IN], (rule, sites)
