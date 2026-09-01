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

import numpy as np

from gain.genomic_resources.genomic_position_table.record import (
    Record,
)
from gain.genomic_resources.repository import (
    GenomicResource,
)
from gain.genomic_resources.resource_errors import (
    overlapping_records_error,
)
from gain.genomic_resources.score_def import (
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
    build_region_aggregator,
    distinct_score_ids,
    resolve_aggregator_name,
    score_def_for,
)
from .base import GenomicScore
from .records import (
    RecordArrays,
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
        ...     values = score.fetch_position_scores("chr1", 12345)
        ...     # Fetch scores across a region
        ...     region = score.fetch_region_segments(
        ...         "chr1", 10000, 20000)
        ...     for pos_begin, pos_end, scores in region:
        ...         print(f"{pos_begin}-{pos_end}: {scores}")

    Aggregating those values over the region is the *annotator's* job, not the
    resource's -- see ``gain.annotation.score_annotator``.  What the resource
    contributes is ``fetch_region_weighted_values``, which pairs every record
    with the number of queried bases it covers.

    Attributes:
        resource: The underlying GenomicResource object
        resource_id: Unique identifier for the resource
        config: Configuration dictionary for the score
        table: GenomicPositionTable for data access
        score_definitions: Dictionary mapping score IDs to their definitions

    Key Methods:
        fetch_position_scores: Get score values at a specific position
        fetch_region_segments: Iterate over score segments in a
            genomic region, each at its record's own extent
        fetch_region_weighted_values: Iterate over ``(values, weight)`` pairs
            in a genomic region, for a caller that aggregates it
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
        stated here but in ``validate_records`` / ``validate_record_arrays``,
        the only places that enforce it.  This is a MEASURE.

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
        the unclipped stream.  The ONE statement of the rule for this kind:
        :meth:`aggregate_region` folds this stream and
        :meth:`fetch_region_weighted_values` weighs it, so the annotators'
        read and the aggregating one cannot come to differ about which part
        of a record the query asked for (gain#1087).
        """
        return clip_to_region(
            super()._aggregation_segments(chrom, pos_begin, pos_end, scores),
            pos_begin, pos_end)

    @staticmethod
    def get_schema() -> dict[str, Any]:
        schema = copy.deepcopy(GenomicScore.get_schema())
        scores_schema = schema["scores"]["schema"]["schema"]
        scores_schema["aggregator"] = AGGREGATOR_SCHEMA
        return schema

    def validate_records(
        self, records: Iterator[Record],
    ) -> Generator[Record, None, None]:
        """Refuse two records that overlap -- or merely touch.

        A position score promises one value per position, so a record
        beginning where its predecessor has not yet ended claims a position
        already taken.  ``begin <= prev_end`` and not ``<``: two records
        sharing a single base pair is the same error as two overlapping by a
        hundred.

        The comparison is against RAW spans, so the verdict does not depend
        on how the scan happened to partition the contig -- clipping a record
        to a queried region can only shrink it, and two records a region
        boundary pulled apart still claim one position between them.

        Adjacent pairs only, as :meth:`validate_record_arrays` also compares
        them: each record is measured against the one before it, not against
        the widest end seen so far.  A record whose own end precedes its own
        begin can therefore hide an overlap between its two neighbours.
        gain#668 carries that, with the data survey it needs -- widening
        either validator to a running maximum refuses strictly more than
        ``repo-stats`` accepts today.
        """
        prev_chrom: str | None = None
        prev_end: int | None = None
        for record in records:
            chrom, begin, end = self._record_to_begin_end(record)
            if chrom != prev_chrom:
                prev_end = None
            if prev_end is not None and begin <= prev_end:
                raise overlapping_records_error(
                    self.resource_id, chrom, begin, prev_end)
            prev_chrom, prev_end = chrom, end
            yield record

    def validate_record_arrays(
        self, batches: Iterator[RecordArrays], chrom: str,
    ) -> Generator[RecordArrays, None, None]:
        """Refuse two records that overlap -- or merely touch, vectorized.

        The same rule as :meth:`validate_records`, stated over a batch's
        columns instead of over records: a record beginning where its
        predecessor has not yet ended claims a position already taken.  Both
        read the RAW begin and end, which is the only layer at which the two
        can say the same thing -- clipping a record to the scanned region
        would make the verdict depend on how the contig was partitioned.

        A violation straddling a batch boundary is caught on the carried end:
        batches are a read-granularity artefact, and no rule may depend on
        where one happens to break.

        Adjacent pairs only, exactly as :meth:`validate_records` compares
        them -- the two agree on this limitation as they agree on the rule.
        See that method, and gain#668.
        """
        prev_end: int | None = None
        for batch in batches:
            pos_begin, pos_end, _cells = batch
            if pos_begin.size:
                if prev_end is not None and int(pos_begin[0]) <= prev_end:
                    raise overlapping_records_error(
                        self.resource_id, chrom, int(pos_begin[0]), prev_end)
                touching = pos_begin[1:] <= pos_end[:-1]
                if bool(touching.any()):
                    first = int(np.argmax(touching))
                    raise overlapping_records_error(
                        self.resource_id, chrom,
                        int(pos_begin[first + 1]), int(pos_end[first]))
                prev_end = int(pos_end[-1])
            yield batch

    def fetch_region_weighted_values(
        self,
        chrom: str,
        pos_begin: int | None = None,
        pos_end: int | None = None,
        scores: list[str] | None = None,
    ) -> Generator[
            tuple[list[ScoreValue], int], None, None]:
        """Yield ``(values, weight)`` for every record touching the region.

        The weight of a position-score record is the number of base pairs
        of the queried region it covers -- how many times its value counts
        when the region is aggregated.  It is derived here so that a caller
        aggregating a region never clips a record nor materialises one copy
        of a value per base pair.

        The stream is :meth:`_aggregation_segments`, not
        :meth:`fetch_region_segments`: this read and
        :meth:`~.base.GenomicScore.aggregate_region` must agree about which
        part of a record the query asked for, and they agree by reading one
        statement of it rather than by both being right (gain#1087).  What
        remains here is the other half -- how many times that part counts --
        which is :meth:`record_weight`, likewise the kind's own.
        """
        for left, right, values in self._aggregation_segments(
            chrom, pos_begin, pos_end, scores,
        ):
            yield (values, self.record_weight(left, right))

    def fetch_position_scores(
        self, chrom: str, position: int,
        scores: list[str] | None = None,
    ) -> list[ScoreValue] | None:
        """Fetch score values at specific genomic position.

        The FIRST record covering the position answers, and a second one is
        not an error here: several records at one position is a malformed
        position score, and refusing it is the statistics scan's job rather
        than a reader's (ADR 0008).  It is the same rule the region read
        stopped enforcing, on the same path, so it leaves with it.

        The region generator is DRAINED rather than abandoned after that
        first record.  Abandoning it would leave
        ``TabixGenomicPositionTable.get_records_in_region`` suspended short
        of the ``buffer.prune()`` that ends its buffered walk, and a
        suspended generator is not torn down by the caller moving on -- its
        cleanup waits on a garbage collection that may never come.  The
        annotation path reads position after position through here, so the
        ``LineBuffer`` would grow without bound across a run.
        """
        if chrom not in self.get_all_chromosomes():
            raise ValueError(
                f"{chrom} is not among the available chromosomes.")

        score_defs = self._resolve_score_defs(scores)

        records = list(self.fetch_records(chrom, position, position))
        if not records:
            return None

        return self.get_score_values_from_record(records[0], score_defs)

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

    def _position_runs(
        self, chrom: str, start: int, end: int,
        scores: list[str],
    ) -> Generator[tuple[list[ScoreValue] | None, int], None, None]:
        """Yield ``(values, run_length)`` runs tiling ``[start, end]``.

        The per-position expansion, run-length encoded: every position of
        the region belongs to exactly one run, uncovered positions to a
        ``None`` run.  Where two records cover one position the FIRST
        answers -- a later record contributes only the positions the
        earlier one did not -- so the total run length always equals the
        region width, and accumulated weight can never exceed it.
        """
        cursor = start
        for left, right, values in self.fetch_region_segments(
                chrom, start, end, scores):
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

    def _guard_position_span(self, start: int, end: int) -> None:
        """Refuse a span no genomic region can mean.

        There is deliberately no UPPER bound check: the exact chromosome
        length is not knowable for most position scores, and a position past
        the end of the data is simply uncovered (see #727).
        """
        if start < 1:
            raise ValueError(
                f"genomic score <{self.resource_id}> asked for a region "
                f"with start {start}; positions are 1-based")
        if end < start:
            raise ValueError(
                f"genomic score <{self.resource_id}> asked for a region "
                f"whose end {end} precedes its start {start}")

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

    def _resolve_single_score(self, score: str | None) -> str:
        """Resolve a singular method's ``score`` argument to one score id.

        ``None`` means "all the scores this resource has", which a singular
        method can honour only when there is exactly one.
        """
        if score is not None:
            return score
        all_scores = self.get_all_scores()
        if len(all_scores) != 1:
            raise ValueError(
                f"genomic score <{self.resource_id}> defines "
                f"{sorted(all_scores)}; a singular read can resolve "
                f"score=None only when there is exactly one")
        return all_scores[0]

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

        A one-position region read, materialised: the generator is drained
        rather than abandoned, for the reason
        :meth:`fetch_position_scores` documents.
        """
        rows = list(self.get_scores_in_region(chrom, pos, pos, scores))
        return rows[0]

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

    def _resolve_aggregation_queries(
        self, queries: Sequence[PositionScoreAggregationQuery],
    ) -> list[tuple[str, Aggregator, ScoreValue]]:
        """Resolve each query to its (score_id, aggregator, replacement).

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
                query.score, query.aggregator, score_def,
                resource_id=self.resource_id,
                remedy="name one on the query")
            resolved.append((
                query.score,
                build_region_aggregator(
                    query.score, aggregator,
                    resource_id=self.resource_id),
                query.none_value_replacement,
            ))
        return resolved

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
        """
        self._guard_position_span(start, end)
        targets, score_ids = self._resolve_aggregation_query_targets(
            chrom, queries)
        for values, length in self._position_runs(
                chrom, start, end, score_ids):
            for column, aggregator, none_value_replacement in targets:
                value = values[column] if values is not None else None
                if value is None:
                    value = none_value_replacement
                aggregator.add(value, length)
        return tuple(
            aggregator.get_final() for _, aggregator, _ in targets)

    def _resolve_aggregation_query_targets(
        self, chrom: str, queries: Sequence[PositionScoreAggregationQuery],
    ) -> tuple[list[tuple[int, Aggregator, ScoreValue]], list[str]]:
        """Resolve queries to per-run fold targets, and the fetch columns.

        One fetch serves every query: each DISTINCT score is fetched once,
        and each query folds the column its score landed in -- so one score
        may be requested twice with different aggregators, exactly as
        ``aggregate_region`` allows.  Which scores those are, and in what
        order, is :func:`~.aggregation.distinct_score_ids`, the same
        derivation the fold uses: the list both names what is fetched and
        indexes what comes back, so a second spelling of it that ordered
        the scores differently would have every aggregator quietly reading
        its neighbour's column.
        """
        resolved = self._resolve_aggregation_queries(queries)
        score_ids = [
            score_def.score_id
            for score_def in self._region_read_defs(
                chrom,
                distinct_score_ids(sid for sid, _, _ in resolved))
        ]
        column_of = {sid: i for i, sid in enumerate(score_ids)}
        targets = [
            (column_of[score_id], aggregator, none_value_replacement)
            for score_id, aggregator, none_value_replacement in resolved
        ]
        return targets, score_ids

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
        """
        self._guard_position_span(start, end)
        if bin_size < 1:
            raise ValueError(
                f"genomic score <{self.resource_id}> asked for bins of "
                f"size {bin_size}; a bin holds at least one position")
        targets, score_ids = self._resolve_aggregation_query_targets(
            chrom, queries)
        return self._binned_runs(
            self._position_runs(chrom, start, end, score_ids),
            start, end, bin_size, targets)

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
        self._guard_position_span(start, end)
        score_ids = [
            score_def.score_id
            for score_def in self._region_read_defs(
                chrom, list(scores) if scores is not None else None)
        ]
        return self._expand_position_runs(
            self._position_runs(chrom, start, end, score_ids),
            len(score_ids))
