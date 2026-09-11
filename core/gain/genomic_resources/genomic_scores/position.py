""":class:`PositionScore` -- one value per genomic position.

The kind whose records carry no reference or alternative allele, so a
position is the whole key. Adds the position-run reads and the binned and
aggregated region queries built on them.
"""

from __future__ import annotations

import copy
from collections.abc import Generator, Iterator, Sequence
from typing import (
    Any,
    ClassVar,
)

from gain.genomic_resources.repository import (
    GenomicResource,
)
from gain.genomic_resources.score_def import (
    GenomicScoreDef,
    ScoreValue,
)
from gain.utils.regions import (
    calc_bin_begin,
    calc_bin_end,
    calc_bin_index,
)

from ..aggregators import (
    AGGREGATOR_SCHEMA,
    Aggregator,
    PositionScoreAggregationQuery,
)
from .aggregation import (
    QUERY_AGGREGATOR_REMEDY,
    build_region_aggregator,
    distinct_score_ids,
    resolve_aggregator_name,
    score_def_for,
)
from .base import GenomicScore
from .records import (
    clip_span,
    clip_to_region,
)


class PositionScore(GenomicScore):
    """Position-based genomic score resource.

    A PositionScore provides scores associated with genomic positions,
    where each score value applies to a specific genomic coordinate or range.
    Unlike AlleleScore, PositionScore does not consider reference or
    alternative alleles - scores are purely position-based.

    Typical use cases include:
    - Conservation scores (e.g., phastCons, phyloP)
    - Mappability scores
    - GC content
    - Recombination rates
    - Any metric that depends only on genomic position

    The score data can be stored in various formats including tabix-indexed
    files, BigWig files, or in-memory tables.

    Example:
        >>> from gain.genomic_resources.repository_factory import (
        ...     build_genomic_resource_repository
        ... )
        >>> repo = build_genomic_resource_repository()
        >>> resource = repo.get_resource("phastCons100way")
        >>> score = build_score_from_resource(resource)
        >>> with score.open() as score:
        ...     # Fetch scores at a specific position
        ...     values = score.get_scores_at_position("chr1", 12345)
        ...     # Fetch scores across a region
        ...     region = score.fetch_region_segments_scores(
        ...         "chr1", 10000, 20000)
        ...     for pos_begin, pos_end, scores in region:
        ...         print(f"{pos_begin}-{pos_end}: {scores}")

    Aggregating those values over the region is the *resource's* job since
    gain#1131: ``get_scores_in_region_agg`` reduces a region to one value
    per query, and ``gain.annotation.position_score_annotator`` asks for
    that rather than folding records of its own.  What the kind
    contributes to the reduction is ``record_weight`` -- how many queried
    bases a record covers, and so how many times its value counts.

    Attributes:
        resource: The underlying GenomicResource object
        resource_id: Unique identifier for the resource
        config: Configuration dictionary for the score
        table: GenomicPositionTable for data access
        score_definitions: Dictionary mapping score IDs to their definitions

    Key Methods:
        get_scores_at_position: Get score values at a specific position
        fetch_region_segments_scores: Iterate over score segments in a
        genomic region, each at its record's own extent
        get_scores_in_region_agg: Reduce a genomic region to one value per
        aggregation query, weighing each record by the bases it covers
    """

    # A region of positions reduces by ``mean``: each position's value counts
    # once per base pair it covers (see :meth:`record_weight`).
    DEFAULT_AGGREGATORS: ClassVar[dict[str, str | None]] = {
        "float": "mean",
        "int": "mean",
        "str": "list",
        "bool": None,
    }

    def __init__(self, resource: GenomicResource):
        if resource.get_type() != "position_score":
            raise ValueError(
                "The resource provided to PositionScore should be of "
                f"'position_score' type, not a '{resource.get_type()}'")
        super().__init__(resource)

    @classmethod
    def record_weight(cls, left: int, right: int) -> int:
        """A record counts once per base pair it covers.

        The only kind whose answer is not 1.  That there is exactly one
        value per position -- what a position score PROMISES -- is not
        stated here but in the rules this kind is registered under in
        :mod:`gain.genomic_resources.statistics.record_validation`, the only
        places that enforce it.  This is a MEASURE.

        Elementwise, as the base requires: handed the position columns of a
        whole batch, the same expression answers that batch's weights.
        """
        return right - left + 1

    def _aggregation_segments(
        self,
        chrom: str,
        pos_begin: int | None = None,
        pos_end: int | None = None,
        scores: list[str] | None = None,
    ) -> Generator[tuple[int, int, list[ScoreValue]], None, None]:
        """The base's stream, clipped to the queried window first.

        Clipping before weighing is a position-score fact: a record's weight
        is how many bases OF THE QUERY it covers, so the part reaching
        outside must come off before :meth:`record_weight` measures it.
        Left unclipped, a record straddling the edge would count for its
        whole width, and one entirely past the window for a negative number
        of times.

        Written as the ADR 0008 idiom -- compose the region transducer over
        the unclipped stream.  Every SEGMENT-shaped read of this kind states
        the rule here and only here, which is now
        :meth:`aggregate_region` alone: the weighted read that was its
        other reader left with gain#1131, when the annotator moved to the
        logical plane.  The plane's :meth:`_position_runs` clips for itself
        still, because it is not reducing records but tiling positions --
        gain#1027 carries that.
        """
        return clip_to_region(
            super()._aggregation_segments(chrom, pos_begin, pos_end, scores),
            pos_begin, pos_end)

    @staticmethod
    def get_schema() -> dict[str, Any]:
        """The :class:`GenomicScore` schema plus a per-score ``aggregator``."""
        schema = copy.deepcopy(GenomicScore.get_schema())
        scores_schema = schema["scores"]["schema"]["schema"]
        scores_schema["aggregator"] = AGGREGATOR_SCHEMA
        return schema

    # -- The logical read plane (#727) -------------------------------------
    #
    # On this plane a position score is a function from a genomic position
    # to a record of named score values, defined on the per-position
    # expansion: a gap in coverage is not an absence, it is a run of
    # positions whose value is ``None``, and it counts.  It is computed by
    # walking segments -- only the two region reads materialise a value per
    # position; the aggregating and binning reads fold one
    # ``(values, run_length)`` pair per segment, so cost stays proportional
    # to record count.

    def _plane_read_defs(
        self, chrom: str, start: int, end: int,
        scores: Sequence[str] | None,
    ) -> list[GenomicScoreDef]:
        """Refuse what this region read cannot serve, and resolve its scores.

        The preamble every read that takes a MANDATORY region shares: the
        span guard, then the request refusals, then the definitions in the
        order the caller asked for them.  Here rather than repeated per
        read so that the two cannot drift in what they refuse -- the deeper
        fold, of :meth:`_guard_region_span` into :meth:`_region_read_defs`,
        is declined in that method's docstring because it would also refuse
        ``fetch_*`` requests on all three kinds, which this does not touch.

        The DEFINITIONS are what travels on, not the ids resolved out of
        them: a read that has resolved its request carries the answer down
        to :meth:`~.base.GenomicScore._fetch_segments_for_defs` rather than
        handing back ids for the segment read to resolve a second time
        (gain#1282).  A caller wanting the ids reads them off the defs.

        The aggregating and binning reads do NOT share it: their scores
        come from ``distinct_score_ids`` over the queries, not from
        ``scores``, and a contig this score never mentions is uncovered
        there rather than refused (gain#1211).
        """
        self._guard_region_span(start, end)
        return self._region_read_defs(chrom, scores)

    def _fetch_segments_for_defs(
        self, chrom: str, start: int, end: int,
        score_defs: list[GenomicScoreDef],
    ) -> Generator[
            tuple[int, int, list[ScoreValue]], None, None]:
        """:meth:`~.base.GenomicScore.fetch_region_segments_scores`,
        pre-resolved.

        The same read, from the same records, through the same per-kind
        transform (:meth:`~.base.GenomicScore._score_segments`) -- entered
        one step lower, with the definitions this plane already holds
        instead of ids for that method to resolve a second time.

        A read that reaches here has run :meth:`_plane_read_defs`, or the
        aggregating pair's own resolver, and would otherwise pay the whole
        refusal preamble twice: the open check, the contig screen and the id
        resolution.  The second answer can only agree with the first, so it
        was waste, and gain#1282 removed it.  The contig half was the
        expensive one when that landed -- a walk of the ordered contig list,
        O(contigs) on a tabix table (gain#1173); it is a set lookup since
        gain#1304, and the id resolution is now the larger of the two.  The
        repeat is skipped either way, because a second answer that can only
        agree is waste at any price.

        No refusal is weakened, because this is not the only one that runs:
        every caller resolves eagerly first and reaches here only after.
        What is skipped is a REPEAT, never the first answer.

        On :class:`PositionScore` rather than on the base, though it uses
        only base methods: a door that skips the eager refusals may be
        opened only by a read that has already made them, and this plane's
        reads are the ones that have.  Same placement argument as
        :meth:`~.base.GenomicScore._read_defs_for_any_contig`, which is on
        the base and has to say in prose who may call it; this does not
        need to.  The fragment plane wants nothing here -- it never
        resolves before reading, so it has no definitions to carry and
        pays the preamble exactly once through the public entry.

        Like the method it shadows, a plain function returning a generator:
        what the caller resolved was refused at ITS call, and nothing moved
        to the first ``next()``.

        No ``score_filter``, unlike the method it shadows: none of this
        plane's reads select records.  One that grew a filter would take
        the parameter here and forward it to
        :meth:`~.base.GenomicScore.fetch_records`, where it belongs
        (gain#1272).
        """
        return self._score_segments(
            self.fetch_records(chrom, start, end), score_defs)

    def _position_runs(
        self, chrom: str, start: int, end: int,
        score_defs: list[GenomicScoreDef],
    ) -> Generator[tuple[list[ScoreValue] | None, int], None, None]:
        """Yield ``(values, run_length)`` runs tiling ``[start, end]``.

        The per-position expansion, run-length encoded: every position of
        the region belongs to exactly one run, uncovered positions to a
        ``None`` run.  Where two records cover one position the FIRST
        answers -- a later record contributes only the positions the
        earlier one did not -- so the total run length always equals the
        region width, and accumulated weight can never exceed it.

        This region read fetched from the table; :meth:`_runs_from_segments`
        is the same encoding over a segment stream a caller supplies.

        Takes DEFINITIONS, not ids: every caller has already resolved the
        request -- through :meth:`_plane_read_defs`, or through the
        aggregating reads' own resolver -- and
        :meth:`_fetch_segments_for_defs` is the segment read that does not
        resolve it a second time (gain#1282).
        """
        return self._runs_from_segments(
            self._fetch_segments_for_defs(chrom, start, end, score_defs),
            start, end)

    @staticmethod
    def _runs_from_segments(
        segments: Iterator[tuple[int, int, list[ScoreValue]]],
        start: int, end: int,
    ) -> Generator[tuple[list[ScoreValue] | None, int], None, None]:
        """Run-length encode ``segments`` over ``[start, end]``.

        :meth:`_position_runs` as a function OF a segment stream, so what
        the region is read FROM is the caller's to choose.  An empty stream
        is a whole region of uncovered positions -- one ``(None, width)``
        run out of the tail below -- which is what lets
        :meth:`_aggregating_runs` answer a contig the score never mentions
        without a second spelling of that shape (gain#1211).
        """
        cursor = start
        for left, right, values in segments:
            span = clip_span(left, right, start, end)
            if span is None:
                continue
            left, right = span
            if right < cursor:
                continue
            # cursor >= start, so this also subsumes the clip's left clamp.
            left = max(left, cursor)
            if left > cursor:
                yield None, left - cursor
            yield values, right - left + 1
            cursor = right + 1
        if cursor <= end:
            yield None, end - cursor + 1

    @staticmethod
    def _expand_position_runs(
        runs: Iterator[tuple[list[ScoreValue] | None, int]],
        n_scores: int,
    ) -> Generator[tuple[ScoreValue | None, ...], None, None]:
        """Expand run-length encoded runs to one tuple per position."""
        for values, length in runs:
            row: tuple[ScoreValue | None, ...] = (
                tuple(values) if values is not None else (None,) * n_scores)
            for _ in range(length):
                yield row

    def get_score_at_position(
        self, chrom: str, pos: int,
        score: str | None = None,
    ) -> ScoreValue | None:
        """Return one score's value at one position, ``None`` if uncovered.

        The singular form of :meth:`get_scores_at_position`; ``score`` of
        ``None`` is honoured only when the resource declares exactly one.
        """
        return self.get_scores_at_position(
            chrom, pos, [self._resolve_single_score(score)])[0]

    def get_scores_at_position(
        self, chrom: str, pos: int,
        scores: Sequence[str] | None = None,
    ) -> tuple[ScoreValue | None, ...]:
        """Return the score values at one position, ``None`` where uncovered.

        Read straight off :meth:`_position_runs` rather than through the
        region read's per-position expansion: a one-position region is
        exactly ONE run -- no run is ever yielded empty, and the run
        lengths sum to the region width -- so there is nothing to expand
        and nothing to index a position out of.

        The run is DRAINED rather than abandoned; that is simpler to
        write, not load-bearing, since gain#1120 moved the tabix buffer
        prune into a ``finally`` (``fragment.py`` cross-references this
        paragraph, and ``test_a_walk_of_point_reads_leaves_the_tabix_
        buffer_pruned`` pins the drained half).

        Going straight to the runs rather than delegating to
        :meth:`get_scores_in_region` is worth ~4% on this read, which the
        position annotator pays once per substitution.
        """
        score_defs = self._plane_read_defs(chrom, pos, pos, scores)
        # The single-element unpack both DRAINS the generator and asserts
        # the one-run invariant the docstring claims; a second run would
        # raise here rather than be silently dropped.
        (values, _), = self._position_runs(chrom, pos, pos, score_defs)
        return (
            tuple(values) if values is not None
            else (None,) * len(score_defs))

    def get_score_in_region(
        self, chrom: str, start: int, end: int,
        score: str | None = None,
    ) -> Generator[ScoreValue | None, None, None]:
        """Yield one value per position of ``[start, end]`` for one score.

        The singular form of :meth:`get_scores_in_region`; ``score`` of
        ``None`` is honoured only when the resource declares exactly one.
        """
        rows = self.get_scores_in_region(
            chrom, start, end, [self._resolve_single_score(score)])
        return (row[0] for row in rows)

    def resolve_aggregation_queries(
        self, queries: Sequence[PositionScoreAggregationQuery],
    ) -> list[tuple[str, str, ScoreValue]]:
        """Resolve each query to its (score_id, aggregator NAME, replacement).

        The third element is the query's ``none_value_replacement``.

        A query asks the same two questions a request list does -- which
        score, and what reduces it -- so they are asked where they are
        answered for every surface, in :mod:`.aggregation`
        (:func:`~.aggregation.score_def_for`,
        :func:`~.aggregation.resolve_aggregator_name`).  Only the remedy of
        the missing-default refusal is this surface's own, because a caller
        here names an aggregator on the query rather than in a pair.

        What a query asks BESIDES is the third: a ``none_value_replacement``
        must be of a type the score can mean, following
        ``validate_aggregator``'s precedent.  It is judged BETWEEN the other
        two -- after the score is known, since its value type is what
        judges the replacement, and before an aggregator is looked for, so
        that a query wrong in both ways is answered about the value it named
        rather than the one it left out.  That order is a decision and not
        an accident of composition; it is pinned by
        ``test_a_query_invalid_several_ways_reports_the_first_ground``.

        Public, and stopping at the NAME, because asking whether a query is
        answerable is a question a caller may have without wanting to read
        (gain#1131).  ``PositionScoreAnnotator`` asks it once when the
        pipeline loads, so an attribute naming no aggregator for a ``bool``
        score is refused there rather than on the first region that reaches
        it.  Building the accumulators is the READ's business --
        :meth:`_resolve_aggregation_queries` adds them, per call, which is
        what keeps a read thread-safe and an annotator stateless.

        It lives on this kind rather than on
        :class:`~.base.GenomicScore` because of its middle step:
        ``none_value_replacement`` is a field only a
        ``PositionScoreAggregationQuery`` carries, a position score being
        the only kind with an uncovered position to speak for.  Should a
        fragment or an allele score come to want a resolver of its own
        (gain#1124, gain#1132), the ``score_def_for`` /
        ``resolve_aggregator_name`` pair is the part that generalises --
        both already live in :mod:`.aggregation`, kind-neutral, for that
        reason -- and the replacement validation is the part that does
        not.
        """
        resolved = []
        for query in queries:
            score_def = score_def_for(
                query.score,
                score_definitions=self.score_definitions,
                resource_id=self.resource_id)
            self._validate_none_value_replacement(
                query.score, score_def.value_type,
                query.none_value_replacement)
            aggregator = resolve_aggregator_name(
                query.aggregator, score_def,
                resource_id=self.resource_id,
                remedy=QUERY_AGGREGATOR_REMEDY)
            resolved.append((
                query.score, aggregator, query.none_value_replacement))
        return resolved

    def _resolve_aggregation_queries(
        self, queries: Sequence[PositionScoreAggregationQuery],
    ) -> list[tuple[str, Aggregator, ScoreValue]]:
        """The read's form of :meth:`resolve_aggregation_queries`.

        The same triples with the aggregator NAME replaced by a freshly
        built accumulator -- one per call, never shared between reads.
        """
        return [
            (
                score_id,
                build_region_aggregator(
                    score_id, aggregator,
                    resource_id=self.resource_id),
                none_value_replacement,
            )
            for score_id, aggregator, none_value_replacement
            in self.resolve_aggregation_queries(queries)
        ]

    # Which python types a none_value_replacement may have per score value
    # type.  A ``bool`` is deliberately not a valid int or float
    # replacement, exactly as a bool-typed score is not a numeric one.
    _NONE_VALUE_REPLACEMENT_TYPES: ClassVar[dict[str, tuple[type, ...]]] = {
        "float": (int, float),
        "int": (int,),
        "str": (str,),
        "bool": (bool,),
    }

    def _validate_none_value_replacement(
        self, score_id: str, value_type: str | None,
        none_value_replacement: ScoreValue,
    ) -> None:
        """Refuse a none_value_replacement of a type the score cannot mean."""
        if none_value_replacement is None or value_type is None:
            return
        allowed = self._NONE_VALUE_REPLACEMENT_TYPES.get(value_type, ())
        if isinstance(none_value_replacement, bool) and bool not in allowed:
            pass
        elif isinstance(none_value_replacement, allowed):
            return
        raise ValueError(
            f"none_value_replacement {none_value_replacement!r} for score "
            f"{score_id!r} of resource {self.resource_id!r} does not "
            f"match its value type {value_type!r}")

    def get_score_in_region_agg(
        self, chrom: str, start: int, end: int,
        score: str | None = None,
        aggregator: str | None = None,
        none_value_replacement: ScoreValue | None = None,
    ) -> ScoreValue:
        # pylint: disable=too-many-positional-arguments
        """Reduce ``[start, end]`` to one value for one score.

        The singular form of :meth:`get_scores_in_region_agg`; ``score`` of
        ``None`` is honoured only when the resource declares exactly one.
        """
        return self.get_scores_in_region_agg(
            chrom, start, end, [
                PositionScoreAggregationQuery(
                    self._resolve_single_score(score),
                    aggregator, none_value_replacement),
            ])[0]

    def get_scores_in_region_agg(
        self, chrom: str, start: int, end: int,
        queries: Sequence[PositionScoreAggregationQuery],
    ) -> tuple[ScoreValue, ...]:
        """Reduce ``[start, end]`` to one value per query, over positions.

        Defined on the per-position expansion -- an uncovered position is a
        ``None``, and with a ``none_value_replacement`` set it counts --
        but computed by walking segments, so cost stays proportional to
        record count.  Where two records cover one position the first
        answers, so accumulated weight never exceeds the region width.

        A contig this score never mentions is uncovered rather than refused
        (gain#1211): it answers as a window on a contig the score has but
        does not cover.  That is this read and the binned one only; a read
        that materialises positions still refuses an unknown contig.
        """
        self._guard_region_span(start, end)
        targets, score_defs = self._resolve_aggregation_query_targets(queries)
        for values, length in self._aggregating_runs(
                chrom, start, end, score_defs):
            for column, aggregator, none_value_replacement in targets:
                value = values[column] if values is not None else None
                if value is None:
                    value = none_value_replacement
                aggregator.add(value, length)
        return tuple(
            aggregator.get_final() for _, aggregator, _ in targets)

    def _resolve_aggregation_query_targets(
        self, queries: Sequence[PositionScoreAggregationQuery],
    ) -> tuple[
            list[tuple[int, Aggregator, ScoreValue]],
            list[GenomicScoreDef]]:
        """Resolve queries to per-run fold targets, and the fetch columns.

        One fetch serves every query: each DISTINCT score is fetched once,
        and each query folds the column its score landed in -- so one score
        may be requested twice with different aggregators, exactly as
        ``aggregate_region`` allows.  Which scores those are, and in what
        order, is :func:`~.aggregation.distinct_score_ids`, the derivation
        the aggregating reads hand to
        :func:`~.aggregation.fold_region_segments` as its column list: the
        list both names what is fetched and indexes what comes back, so a
        second spelling of it that ordered the scores differently would
        have every aggregator quietly reading its neighbour's column.

        The resolved DEFINITIONS are what comes back, and they are what the
        read hands down: this is the aggregating pair's resolution of its
        request, so re-deriving ids from them for a lower layer to resolve
        again is the waste gain#1282 removed.  The column index is taken
        off the same list, in the same order, so the two still cannot
        disagree about which column a query folds.

        No contig reaches here: what the aggregating reads resolve is which
        scores to fetch and how to fold them, which is a property of the
        queries alone.  Whether the contig exists is
        :meth:`_aggregating_runs`' question, and it answers it by choosing a
        run source rather than by refusing (gain#1211).
        """
        resolved = self._resolve_aggregation_queries(queries)
        score_defs = self._read_defs_for_any_contig(
            distinct_score_ids(sid for sid, _, _ in resolved))
        column_of = {
            score_def.score_id: i for i, score_def in enumerate(score_defs)}
        targets = [
            (column_of[score_id], aggregator, none_value_replacement)
            for score_id, aggregator, none_value_replacement in resolved
        ]
        return targets, score_defs

    def get_score_in_bins(
        self, chrom: str, start: int, end: int, bin_size: int,
        score: str | None = None,
        aggregator: str | None = None,
        none_value_replacement: ScoreValue | None = None,
    ) -> Generator[tuple[int, int, ScoreValue], None, None]:
        # pylint: disable=too-many-positional-arguments
        """Yield ``(bin_start, bin_end, value)`` per bin of ``[start, end]``.

        The singular form of :meth:`get_scores_in_bins`; ``score`` of
        ``None`` is honoured only when the resource declares exactly one.
        """
        bins = self.get_scores_in_bins(
            chrom, start, end, bin_size, [
                PositionScoreAggregationQuery(
                    self._resolve_single_score(score),
                    aggregator, none_value_replacement),
            ])
        return (
            (bin_start, bin_end, values[0])
            for bin_start, bin_end, values in bins)

    def get_scores_in_bins(
        self, chrom: str, start: int, end: int, bin_size: int,
        queries: Sequence[PositionScoreAggregationQuery],
    ) -> Generator[tuple[int, int, tuple[ScoreValue, ...]], None, None]:
        """Yield one aggregated tuple per grid bin of ``[start, end]``.

        Bins follow the GLOBAL grid anchored at position 1
        (``calc_bin_index`` / ``calc_bin_begin`` / ``calc_bin_end``), so
        adjacent queries tile and results are comparable across calls.
        Edge bins are clipped to the query, so the yielded bounds name
        exactly what was aggregated.  Every bin in range is emitted,
        including bins no record touches; a segment straddling a bin
        boundary contributes its weight to each bin it touches, split at
        the boundary.

        A contig this score never mentions is uncovered rather than refused
        (gain#1211): every bin of it is emitted, with the bounds a covered
        contig would yield.  That is this read and
        :meth:`get_scores_in_region_agg` only; a read that materialises
        positions still refuses an unknown contig.
        """
        self._guard_region_span(start, end)
        if bin_size < 1:
            raise ValueError(
                f"genomic score <{self.resource_id}> asked for bins of "
                f"size {bin_size}; a bin holds at least one position")
        targets, score_defs = self._resolve_aggregation_query_targets(queries)
        return self._binned_runs(
            self._aggregating_runs(chrom, start, end, score_defs),
            start, end, bin_size, targets)

    def _aggregating_runs(
        self, chrom: str, start: int, end: int,
        score_defs: list[GenomicScoreDef],
    ) -> Iterator[tuple[list[ScoreValue] | None, int]]:
        """The run source the two aggregating reads fold.

        A contig the score holds records for is read from the table; one it
        never mentions is read from an EMPTY segment stream (gain#1211).  A
        genome-wide fold over a track that skips a chromosome is the normal
        case, not an error, and it needs no shape of its own: a region no
        segment covers is already one ``(None, width)`` run out of
        :meth:`_runs_from_segments`, so the absent contig answers as the
        wholly-uncovered window it is, by the same code, with the width
        computed once. Both branches are that one encoder over a different
        stream -- which is why they cannot drift, and why a degenerate span
        would fail identically in both rather than only in one.

        The BRANCH lives here, above :meth:`_position_runs`, and not inside
        it.  Not because the per-position read would otherwise see it -- it
        would not; that read refuses through :meth:`_region_read_defs`
        before it ever builds these runs.  The reason is the caller after
        next: :meth:`_position_runs` is the plain region read, and a future
        consumer composing it has no way to say "and I did mean to allow an
        absent contig".  Left down there the exemption would be the default
        and silent; up here each read opts in by choosing this source, which
        is the same reason :meth:`_read_defs_for_any_contig` is a second
        door rather than a parameter on the first.

        Choosing this source also means the backend is never asked for the
        absent contig, so the refusals below -- the per-kind record
        transform's, and each table's own -- stay exactly as they are and
        are simply not reached.  The contig is screened once per read here,
        which is the question :meth:`_region_read_defs` used to ask on this
        path; it is the same one screen, moved, not a new one.

        That was true of the ABSENT contig from the start, and of the
        covered one only since gain#1282: this screen chose the source, and
        then the segment read under :meth:`_position_runs` resolved the
        request over again and asked about the contig a second time.  It
        takes resolved definitions now, so the one screen claimed above is
        the one that happens, on either branch.

        Called a *scan* until gain#1304, which is what it was: a walk of the
        ordered contig list, costing more the further down the list the
        contig sat and most of all for one that was absent -- which on this
        branch is the case the read exists to serve.  It is a set lookup
        now, so what is worth saying about the count is only that the read
        asks once.
        """
        if not self.has_chromosome(chrom):
            return self._runs_from_segments(iter(()), start, end)
        return self._position_runs(chrom, start, end, score_defs)

    @staticmethod
    def _binned_runs(
        runs: Iterator[tuple[list[ScoreValue] | None, int]],
        start: int, end: int, bin_size: int,
        targets: list[tuple[int, Aggregator, ScoreValue]],
    ) -> Generator[tuple[int, int, tuple[ScoreValue, ...]], None, None]:
        """Fold position runs into grid bins, splitting at boundaries."""
        bin_idx = calc_bin_index(bin_size, start)
        pos = start
        for values, length in runs:
            remaining = length
            while remaining > 0:
                bin_stop = calc_bin_end(bin_size, bin_idx)
                take = min(remaining, bin_stop - pos + 1)
                for column, aggregator, none_value_replacement in targets:
                    value = values[column] if values is not None else None
                    if value is None:
                        value = none_value_replacement
                    aggregator.add(value, take)
                pos += take
                remaining -= take
                if pos > bin_stop:
                    yield (
                        max(calc_bin_begin(bin_size, bin_idx), start),
                        bin_stop,
                        tuple(agg.get_final() for _, agg, _ in targets))
                    for _, aggregator, _ in targets:
                        aggregator.clear()
                    bin_idx += 1
        if calc_bin_begin(bin_size, bin_idx) <= end:
            yield (
                max(calc_bin_begin(bin_size, bin_idx), start),
                end,
                tuple(agg.get_final() for _, agg, _ in targets))

    def get_scores_in_region(
        self, chrom: str, start: int, end: int,
        scores: Sequence[str] | None = None,
    ) -> Generator[tuple[ScoreValue | None, ...], None, None]:
        """Yield one tuple of score values per position of ``[start, end]``.

        Exactly ``end - start + 1`` tuples, in position order, ``None`` at
        every position no record covers.  ``scores`` of ``None`` asks for
        every score this resource defines, in definition order.
        """
        score_defs = self._plane_read_defs(chrom, start, end, scores)
        return self._expand_position_runs(
            self._position_runs(chrom, start, end, score_defs),
            len(score_defs))
