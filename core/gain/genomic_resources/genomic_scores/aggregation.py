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

from collections.abc import Callable, Iterable

from gain.genomic_resources.score_def import GenomicScoreDef, ScoreValue

from ..aggregators import (
    Aggregator,
)


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
        score_def = score_definitions.get(score_id)
        if score_def is None:
            raise ValueError(
                f"score {score_id!r} is not defined by resource "
                f"{resource_id!r}; it has "
                f"{sorted(score_definitions)}")
        resolved = aggregator or score_def.aggregator
        if resolved is None:
            # Every score has a value type, so the only way to get here
            # is a type whose class default is deliberately None --
            # ``bool``, which has no meaningful reduction to pick for
            # the caller.  Name one and it works.
            raise ValueError(
                f"score {score_id!r} of resource {resource_id!r} "
                f"has no default aggregator for value type "
                f"{score_def.value_type!r}; name one explicitly as "
                f"(score_id, aggregator)")
        requests.append((score_id, resolved))
    return requests


def distinct_score_ids(requests: list[tuple[str, str]]) -> list[str]:
    """The DISTINCT scores ``requests`` needs, in the order asked for.

    One fetch serves every request, so the same list must both name what
    is fetched and index the values that come back.  Both callers derive
    it HERE rather than each spelling it out: a second spelling that
    ordered the scores differently would have every aggregator quietly
    reading its neighbour's column.
    """
    return list(dict.fromkeys(score_id for score_id, _ in requests))


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
    segments: Iterable[tuple[int, int, list[ScoreValue]]],
    aggregators: list[Aggregator],
    requests: list[tuple[str, str]],
    *,
    weigh: Callable[[int, int], int],
) -> list[ScoreValue]:
    """Fold one region read into one value per request.

    ``segments`` is a stream of ``(left, right, values)`` as
    :meth:`~.base.GenomicScore.fetch_region_segments` yields it for
    :func:`distinct_score_ids` of these ``requests`` -- which is how a
    request finds its column: two requests for one score share the fetch
    and keep separate accumulators.

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
        for i, score_id in enumerate(distinct_score_ids(requests))
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
