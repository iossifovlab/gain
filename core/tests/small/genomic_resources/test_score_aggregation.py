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

The exception is the last section, which reaches for a ``PositionScore``
deliberately: what it pins is that the position score's own query surface
and this machinery state one rule between them rather than two, and that
is not observable from either side alone.
"""
from __future__ import annotations

import pathlib
from collections.abc import Callable, Sequence

import gain
import gain.genomic_resources.genomic_scores.position
import pytest
from gain.genomic_resources.aggregators import (
    Aggregator,
    AggregatorDefinition,
    PositionScoreAggregationQuery,
)
from gain.genomic_resources.genomic_scores import PositionScore
from gain.genomic_resources.genomic_scores.aggregation import (
    build_region_aggregator,
    distinct_score_ids,
    fold_region_segments,
    request_score_ids,
    resolve_aggregator_requests,
)
from gain.genomic_resources.score_def import GenomicScoreDef, ScoreValue
from gain.genomic_resources.testing.builders import a_grr, a_position_score

from tests.small.genomic_resources.conftest import a_flag_score

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
    segments: Sequence[tuple[int, int, Sequence[ScoreValue]]],
    requests: list[tuple[str, str]],
    *,
    weigh: Callable[[int, int], int],
    score_ids: list[str] | None = None,
) -> list[ScoreValue]:
    """Drive the fold the way its caller does.

    The aggregators are built here, parallel to ``requests`` and before
    the segments are read, because that is the contract the fold's
    signature states -- retyping them per test would let a caller drift
    from it silently.  ``score_ids`` is what the caller derived ONCE to
    name the fetched columns; left unset it is derived here as a caller
    would, so a test that hands ``segments`` shaped for the requests
    need not spell it out.
    """
    aggregators = [
        build_region_aggregator(score_id, aggregator, resource_id="two")
        for score_id, aggregator in requests
    ]
    if score_ids is None:
        score_ids = request_score_ids(requests)
    return fold_region_segments(
        segments, aggregators, requests, score_ids=score_ids, weigh=weigh)


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


def test_the_fold_indexes_the_columns_it_is_told_the_fetch_carries() -> None:
    # The caller derives the fetched column list ONCE and hands it to the
    # fetch and to the fold (gain#1157); the fold does not re-derive it
    # from the requests.  Pinned with segments whose columns are in the
    # opposite order to the requests: a fold that derived its own list
    # would read each score's neighbour.
    segments = [(10, 10, [1, 0.2]), (11, 11, [2, 0.8])]
    assert _fold(
        segments,
        [("s", "max"), ("t", "max")],
        score_ids=["t", "s"],
        weigh=_by_span,
    ) == [0.8, 2]


def test_a_region_read_derives_its_score_list_once(
    two: PositionScore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # One derivation per read, handed to both the fetch and the fold --
    # not one at each end (gain#1157).  Counted at every module that
    # binds the name, so a fold that went back to deriving for itself
    # would be seen.
    calls: list[list[tuple[str, str]]] = []
    real = request_score_ids

    def counting(requests: list[tuple[str, str]]) -> list[str]:
        calls.append(requests)
        return real(requests)

    for module in (
        gain.genomic_resources.genomic_scores.aggregation,
        gain.genomic_resources.genomic_scores.base,
    ):
        monkeypatch.setattr(module, "request_score_ids", counting)

    with two.open() as score:
        assert score.aggregate_region("1", 10, 14, ["s", "t"]) == [
            pytest.approx((0.2 * 4 + 0.8) / 5), pytest.approx(1.2)]

    assert len(calls) == 1


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


def test_a_tuple_valued_stream_folds_exactly_like_a_list_valued_one() -> None:
    # The fold only ever indexes ``values[column]``, so which container the
    # values arrive in is not part of its contract -- which is why widening
    # the annotation to ``Sequence`` costs nothing.  Pinned because the
    # fragment plane hands it tuples while every caller today hands it
    # lists, and the two must not answer differently.
    requests = [("s", "mean"), ("s", "max")]
    as_lists = _fold(
        [(10, 13, [0.2]), (14, 14, [0.8])], requests, weigh=_by_span)
    as_tuples = _fold(
        [(10, 13, (0.2,)), (14, 14, (0.8,))], requests, weigh=_by_span)

    assert as_tuples == as_lists
    # Not just equal to each other: a fold that answered ``[None, None]``
    # both ways would satisfy the line above and nothing else.
    assert as_tuples == [pytest.approx((0.2 * 4 + 0.8) / 5), 0.8]


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
    flags: PositionScore,
) -> None:
    definitions = flags.score_definitions

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


def test_a_name_built_before_is_not_parsed_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Every aggregating read builds fresh accumulators per call, so the
    # same handful of names are built millions of times per run -- and
    # parsing the name was ~70% of each build (gain#1157).  Once a name
    # has been built, building it again must not go back to the parser:
    # pinned by taking the parser away and asking again.
    build_region_aggregator("s", "max", resource_id="two")

    def refuse(_cls: type, raw: str) -> AggregatorDefinition:
        raise AssertionError(f"parsed {raw!r} again")

    monkeypatch.setattr(
        AggregatorDefinition, "from_string", classmethod(refuse))

    again = build_region_aggregator("s", "max", resource_id="two")
    again.add(0.5)

    assert again.get_final() == 0.5


def test_a_remembered_parametrized_name_still_builds_fresh() -> None:
    # What is remembered about a name is what it resolves TO, never the
    # accumulator: a parametrized form is the case where the two are
    # easiest to confuse, since its parameter travels with the resolution.
    first = build_region_aggregator("s", "join(,)", resource_id="two")
    first.add("a")
    first.add("b")
    second = build_region_aggregator("s", "join(,)", resource_id="two")
    second.add("c")

    assert first.get_final() == "a,b"
    assert second.get_final() == "c"


def test_an_unknown_name_is_refused_every_time_it_is_asked() -> None:
    # A raise is not remembered as an answer: asking twice complains
    # twice, in the same words -- pinned because a memo that stored the
    # failure, or stored a None for it, would turn the second ask into a
    # different error or none at all.
    expected = (
        "score 's' of resource 'two' asks for aggregator 'mediann', "
        "which is not valid: 'mediann'")
    for _ in range(2):
        with pytest.raises(ValueError) as excinfo:
            build_region_aggregator("s", "mediann", resource_id="two")
        assert str(excinfo.value) == expected


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
    return a_flag_score(tmp_path)


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

_STATED_IN = "aggregation.py"


def test_each_aggregation_refusal_is_written_in_exactly_one_place() -> None:
    """The tests above cannot see two copies that agree; this can.

    Two surfaces emitting the same words is what the pins can observe, and
    it is satisfied just as well by two copies -- which is how the missing
    default's remedy came to differ in the first place while the rule half
    still matched.  So the "stated once" half of gain#1087 is pinned where
    it lives: in the source, by counting where each rule is spelled.

    OCCURRENCES and not files: a second copy is just as much a second
    statement for living in the same module as the first, and a fence that
    only counted files would pass while ``resolve_aggregator_requests``
    quietly re-inlined the guard that ``score_def_for`` exists to be.

    Scoped to the ``genomic_scores`` package, which is what these two
    sentences are about, and not to ``gain`` at large: a fence over the
    whole distribution would make every unrelated module's error prose
    answerable to an aggregation test.  The rule itself is worded
    differently elsewhere on purpose, for surfaces that name a SET of
    unknown scores rather than one (``_resolve_score_defs``,
    ``ScoreResource._guard_score_id``, ``score_filter``); converging those
    is gain#1112, so what is pinned here is these two wordings.


    The other limit: this matches raw source text, so a copy whose
    f-string happens to wrap mid-phrase evades it -- which is why each
    anchor is short.

    An intentional rewording goes red here, and should: the new wording
    wants re-anchoring, and the point of the trip is to notice whether
    something else has picked the sentence up again.
    """
    package = pathlib.Path(gain.__file__).parent / \
        "genomic_resources" / "genomic_scores"
    sources = sorted(package.rglob("*.py"))
    # Guard against a scan that silently matches nothing: a fence over an
    # empty list is not a fence, and this package is the whole score layer.
    assert len(sources) > 5, len(sources)

    for rule in _REFUSAL_RULES:
        sites = sorted(
            f"{path.relative_to(package)}:{count}"
            for path in sources
            if (count := path.read_text().count(rule))
        )
        assert sites == [f"{_STATED_IN}:1"], (rule, sites)


# -- Resolving a query without reading, and without building (gain#1131) ---
#
# ``PositionScoreAnnotator`` asks the two questions a query asks when the
# PIPELINE loads, so a misconfigured attribute is refused there rather than
# on the first annotatable that reaches it.  What it must NOT do is build
# the aggregators: the read builds a fresh one per call, which is what keeps
# it thread-safe, and an annotator holding instances of its own is the state
# this seam exists to avoid.  So the resolve half is public and answers
# NAMES; the private one adds the instances for the read.


def test_the_resolver_answers_the_aggregator_name_not_an_instance(
    flags: PositionScore,
) -> None:
    """The whole point of the split: resolution without instantiation.

    A caller that only wants to know whether a query is answerable -- the
    annotator, at load -- gets the name the read would build, and builds
    nothing.  An ``Aggregator`` here instead of a ``str`` would mean the
    annotator was holding accumulators after all.
    """
    resolved = flags.resolve_aggregation_queries(
        [PositionScoreAggregationQuery("flag", "bool")])

    assert resolved == [("flag", "bool", None)]


def test_the_resolver_refuses_a_missing_default_in_the_read_s_own_words(
    flags: PositionScore,
) -> None:
    """Asked earlier is still the SAME refusal, not a second one.

    The annotator surfaces this when the pipeline loads; the read surfaces
    it on the first region.  A caller who moved between them must not get
    two different sentences about one misconfiguration, which is what a
    split that copied the rule instead of sharing it would produce.

    The resolver is asked of an UNOPENED score on purpose: the annotator
    builds its queries in ``__init__``, long before anything opens the
    resource, so a resolver that needed the table would refuse nothing at
    load and the whole seam would be pointless.
    """
    query = PositionScoreAggregationQuery("flag")

    from_resolver = _refusal(
        lambda: flags.resolve_aggregation_queries([query]))
    with flags.open() as score:
        from_read = _refusal(
            lambda: score.get_scores_in_region_agg("1", 10, 10, [query]))

    assert from_resolver == from_read == (
        "score 'flag' of resource 'flags' has no default aggregator for "
        "value type 'bool'; name one on the query")


def test_the_resolver_refuses_an_unknown_score_in_the_read_s_own_words(
    flags: PositionScore,
) -> None:
    """The other ground a query can be refused on, asked at the same door.

    Both grounds have to reach the annotator's load-time call, or a
    misconfiguration would be caught for one reason and not the other.
    """
    query = PositionScoreAggregationQuery("nope")

    from_resolver = _refusal(
        lambda: flags.resolve_aggregation_queries([query]))
    with flags.open() as score:
        from_read = _refusal(
            lambda: score.get_scores_in_region_agg("1", 10, 10, [query]))

    assert from_resolver == from_read == (
        "score 'nope' is not defined by resource 'flags'; it has ['flag']")


def test_the_resolver_builds_no_aggregator(
    flags: PositionScore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolution without instantiation, asserted rather than assumed.

    The whole reason this half is public is that the annotator must ask
    the question without acquiring accumulators, so "it does not build"
    is the property and not an implementation detail.  A returned NAME
    does not say it on its own: a resolver that built an aggregator and
    discarded it would answer identically.

    Sabotaging the builder is what tells the two apart -- if resolution
    reaches it at all, this raises instead of answering.
    """
    def refuse(*args: object, **kwargs: object) -> Aggregator:
        raise AssertionError("resolution must not build an aggregator")

    monkeypatch.setattr(
        gain.genomic_resources.genomic_scores.position,
        "build_region_aggregator", refuse)

    assert flags.resolve_aggregation_queries(
        [PositionScoreAggregationQuery("flag", "bool")],
    ) == [("flag", "bool", None)]


def test_the_resolver_names_the_aggregator_the_read_goes_on_to_use(
    two: PositionScore,
) -> None:
    """The name is only worth having if it is the read's own choice.

    A resolver that answered a plausible name while the read defaulted to
    something else would refuse the right queries and describe the wrong
    reduction.  ``s`` declares no aggregator, so both must land on the
    ``float`` class default -- and the region's answer must be that
    aggregator's, not another's.
    """
    query = PositionScoreAggregationQuery("s")

    assert two.resolve_aggregation_queries([query]) == [("s", "mean", None)]

    with two.open() as score:
        assert score.get_scores_in_region_agg("1", 10, 14, [query]) == (
            pytest.approx(0.32),)
        assert score.get_scores_in_region_agg(
            "1", 10, 14, [PositionScoreAggregationQuery("s", "max")]) == (
            pytest.approx(0.8),)
