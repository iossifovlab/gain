"""Reducing a region to one value per requested score.

The machinery :meth:`~.base.GenomicScore.aggregate_region` orchestrates,
apart from any score class: resolving a caller's request list to
``(score_id, aggregator)`` pairs, building a fresh aggregator for each, and
folding a stream of fetched segments into one value per request.

Nothing here knows what a score KIND is.  The one thing that differs
between kinds -- how many times a record's value counts -- reaches
:func:`fold_region_segments` as ``weigh``, the kind's own
:meth:`~.base.GenomicScore.record_weight`.  The fold applies what it is
handed and does not re-derive or cross-check it: the rule is stated once
per kind, on the kind (see
``test_the_weight_rule_is_stated_once_per_kind``).  Whether the segments
were clipped to the query window before they got here is settled the same
way, by the kind's :meth:`~.base.GenomicScore._aggregation_segments`, so
the fold carries no flag saying which kind it serves.

Every BACKEND feeds one path here -- this generic weighted stream over
fetched records -- and a backend-specific fast path was considered and
rejected to keep it that way; see
``.out-of-scope/bigwig-stats-pushdown.md``.  That is a claim about
backends, not about the package: :class:`~.position.PositionScore` folds
its own segments for the aggregated plane, and converging the two is
gain#1027's remaining work, not this module's promise.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence

from gain.genomic_resources.score_def import GenomicScoreDef, ScoreValue

from ..aggregators import (
    Aggregator,
)

# How each surface tells a caller to name an aggregator the score has no
# default for.  Both live HERE, with the rule they are appended to, so the
# whole sentence a caller sees is written in this module -- a surface
# selects its remedy, it does not word one.  Fragments: lowercase, no
# leading punctuation, spliced after the rule's "; ".
PAIR_AGGREGATOR_REMEDY = "name one explicitly as (score_id, aggregator)"
QUERY_AGGREGATOR_REMEDY = "name one on the query"


def resolve_aggregator_requests(
    scores: list[str | tuple[str, str]] | None,
    *,
    score_definitions: dict[str, GenomicScoreDef],
    all_scores: list[str],
    resource_id: str,
) -> list[tuple[str, str]]:
    """Normalize the request list to ``(score_id, aggregator)`` pairs.

    Two arguments, because they answer two questions: ``all_scores`` says
    which scores "all of them" means and in what order, for a ``scores``
    of ``None``; ``score_definitions`` says what each one IS.  Today a
    score class answers the first with ``list(self.score_definitions)``,
    so the two cannot disagree -- but which scores a resource OFFERS is
    the class's decision to change, and this asks for it rather than
    assuming the answer stays derivable.
    """
    if scores is None:
        scores = list(all_scores)

    requests = []
    for request in scores:
        score_id, aggregator = (
            (request, None) if isinstance(request, str) else request)
        score_def = score_def_for(
            score_id,
            score_definitions=score_definitions,
            resource_id=resource_id)
        resolved = resolve_aggregator_name(
            aggregator, score_def,
            resource_id=resource_id,
            remedy=PAIR_AGGREGATOR_REMEDY)
        requests.append((score_id, resolved))
    return requests


def score_def_for(
    score_id: str,
    *,
    score_definitions: dict[str, GenomicScoreDef],
    resource_id: str,
) -> GenomicScoreDef:
    """The definition an aggregation request names, refusing an unknown one.

    The first of the two questions every aggregation request asks, and the
    one statement of the refusal when the answer is no.  Whether the
    request arrived as a bare score id, as a ``(score_id, aggregator)``
    pair, or as a
    :class:`~gain.genomic_resources.aggregators.PositionScoreAggregationQuery`
    changes nothing about it: the resource either defines that score or it
    does not, and the caller is told which ones it has either way.
    """
    score_def = score_definitions.get(score_id)
    if score_def is None:
        raise ValueError(
            f"score {score_id!r} is not defined by resource "
            f"{resource_id!r}; it has "
            f"{sorted(score_definitions)}")
    return score_def


def resolve_aggregator_name(
    aggregator: str | None,
    score_def: GenomicScoreDef,
    *,
    resource_id: str,
    remedy: str,
) -> str:
    """The aggregator to reduce a score with: the caller's, else its own.

    The second question, and the one statement of the rule that a score
    with neither is refused.  ``remedy`` is the only part that differs
    between surfaces, because it tells the caller what to write and the
    two surfaces take an aggregator in different places -- a
    ``(score_id, aggregator)`` pair for
    :func:`resolve_aggregator_requests`, a field on the query for
    :meth:`~.position.PositionScore.get_scores_in_region_agg`.  Pass one of
    :data:`PAIR_AGGREGATOR_REMEDY` / :data:`QUERY_AGGREGATOR_REMEDY`, which
    is why they live here and not at the call sites.  Everything ahead of
    the remedy is shared, and pinned so by
    ``test_both_surfaces_state_the_missing_default_rule_identically``.

    The score is named by ``score_def`` rather than beside it:
    ``score_definitions`` is keyed by ``score_id`` at every construction
    path, so a separate argument would be one the caller could contradict.
    """
    resolved = aggregator or score_def.aggregator
    if resolved is None:
        # Every score has a value type, so the only way to get here is a
        # type whose class default is deliberately None -- ``bool``, which
        # has no meaningful reduction to pick for the caller.  Name one
        # and it works.
        raise ValueError(
            f"score {score_def.score_id!r} of resource {resource_id!r} "
            f"has no default aggregator for value type "
            f"{score_def.value_type!r}; {remedy}")
    return resolved


def distinct_score_ids(score_ids: Iterable[str]) -> list[str]:
    """The DISTINCT ids among ``score_ids``, in the order asked for.

    One fetch serves every aggregation request, so the same list must both
    name what is fetched and index the values that come back -- which is
    why the ORDER is part of the answer, and why every aggregating read in
    this package derives it here.  A second spelling that ordered the
    scores differently would not fail; it would have every aggregator
    quietly reading its neighbour's column.

    Takes the ids rather than the requests they came off, because a
    request is shaped differently on each surface -- a pair here, a
    :class:`~gain.genomic_resources.aggregators.PositionScoreAggregationQuery`
    resolved to a triple on the position score's plane -- and none of that
    is what the derivation is about.  Each surface projects its own shape
    at its call site; the request list gets a named projection,
    :func:`request_score_ids`, because it is the one shape TWO readers
    project.

    Note this is the aggregating reads' derivation, not a package-wide
    one: ``score_annotator`` dedupes its attribute sources with its own
    ``dict.fromkeys`` over a different input, and converging that is
    gain#1111.
    """
    return list(dict.fromkeys(score_ids))


def request_score_ids(requests: list[tuple[str, str]]) -> list[str]:
    """:func:`distinct_score_ids` of a request list's scores.

    Named because TWO readers project this one list and must project it
    identically: :meth:`~.base.GenomicScore.aggregate_region` names the
    scores to fetch with it, and :func:`fold_region_segments` indexes the
    fetched values with it.  The position score's plane projects its own
    shape inline instead -- one reader, nothing to agree with.
    """
    return distinct_score_ids(score_id for score_id, _ in requests)


def build_region_aggregator(
    score_id: str, aggregator: str, *, resource_id: str,
) -> Aggregator:
    """Build a FRESH aggregator, naming the resource if it cannot.

    Fresh per call, not reused: an aggregator is a mutable accumulator
    and explicitly not thread-safe (see
    :class:`~gain.genomic_resources.aggregators.Aggregator`).  Reuse is
    an annotator optimisation resting on being single-threaded; a score
    may be read from several threads (the web api's thread pool), so
    this cannot assume the same.

    ``Aggregator.build`` raises a bare ``KeyError('mediann')`` for an
    unknown name, saying nothing about which score asked for it.
    """
    try:
        return Aggregator.build(aggregator)
    except (KeyError, ValueError, TypeError) as err:
        raise ValueError(
            f"score {score_id!r} of resource {resource_id!r} asks "
            f"for aggregator {aggregator!r}, which is not valid: "
            f"{err}") from err


def fold_region_segments(
    segments: Iterable[tuple[int, int, Sequence[ScoreValue]]],
    aggregators: list[Aggregator],
    requests: list[tuple[str, str]],
    *,
    weigh: Callable[[int, int], int],
) -> list[ScoreValue]:
    """Fold one region read into one value per request.

    ``segments`` is a stream of ``(left, right, values)`` as
    :meth:`~.base.GenomicScore.fetch_region_segments` yields it for
    :func:`request_score_ids` of these ``requests`` -- which is how a
    request finds its column: two requests for one score share the fetch
    and keep separate accumulators.  Any ``Sequence`` of values will do --
    the fold only ever indexes ``values[column]`` -- so a kind that hands
    over tuples folds exactly as one that hands over lists.

    ``aggregators`` is parallel to ``requests`` and built by the CALLER,
    which is what lets an invalid aggregator name be refused before the
    region is read at all (see :meth:`~.base.GenomicScore.aggregate_region`,
    which explains why that ordering matters).

    ``weigh`` is the caller's per-kind weight rule, applied as handed: it
    turns a segment's span into the number of times that record's value
    counts.  It is not second-guessed here, and neither is the stream --
    whether a record was first cut down to the query window is the
    caller's business, settled before the segments arrive (see
    :meth:`~.base.GenomicScore._aggregation_segments`).  A kind that counts
    a record once counts it wherever the point it collapses to falls,
    window or not.
    """
    column_of = {
        score_id: i
        for i, score_id in enumerate(request_score_ids(requests))
    }
    targets = [
        (aggregator, column_of[score_id])
        for aggregator, (score_id, _) in zip(
            aggregators, requests, strict=True)
    ]

    for left, right, values in segments:
        weight = weigh(left, right)
        for aggregator, column in targets:
            aggregator.add(values[column], weight)

    return [aggregator.get_final() for aggregator in aggregators]
