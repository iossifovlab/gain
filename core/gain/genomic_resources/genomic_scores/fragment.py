""":class:`FragmentScore` -- one value per genomic interval.

The kind whose records span a region rather than a point, and the one that
still answers to a legacy resource-type spelling; recognising that spelling
announces it through
:func:`~gain.genomic_resources.resource_types.warn_deprecated_spelling`.
"""

from __future__ import annotations

import copy
import functools
from collections.abc import Callable, Generator, Iterable, Sequence
from dataclasses import dataclass
from typing import (
    Any,
    ClassVar,
)

import numpy as np

from gain import logging
from gain.genomic_resources.repository import (
    GenomicResource,
)
from gain.genomic_resources.resource_types import (
    FRAGMENT_SCORE_TYPES,
    LEGACY_FRAGMENT_SCORE_TYPE,
    PREFERRED_FRAGMENT_SCORE_TYPE,
    warn_deprecated_spelling,
)
from gain.genomic_resources.score_def import (
    GenomicScoreDef,
    ScoreValue,
)
from gain.genomic_resources.score_filter import (
    ScoreFilter,
)

from ..aggregators import (
    AGGREGATOR_SCHEMA,
    ScoreAggregationQuery,
    validate_aggregator,
)
from .aggregation import (
    build_region_aggregators,
    fold_into_bins,
    fold_region_segments,
    request_score_ids,
    resolve_aggregation_queries,
)
from .base import GenomicScore
from .records import (
    overlap_fractions_admit,
    owns_record,
)

logger = logging.getLogger(__name__)


# Both halves come off ONE walk of the region, which is the reason they are
# answered together rather than by two reads a caller would have to trust to
# agree: nothing a caller can do makes ``count`` disagree with the fragments
# ``values`` was folded from.
#
# ``values`` is parallel to the QUERIES asked, not keyed by score id. One
# score requested twice with two aggregators -- a source exposed as both a
# min and a max -- is two queries and therefore two values, which a mapping
# keyed by score id would silently collapse to one.
#
# An empty region and a filter that rejected every fragment are both ``0``
# -- the distinction gain#820 built for alleles is deliberately not drawn
# here, keeping ADR 0017's reasoning that a region is spanned by fragments
# as a matter of course, so "none cover it" is a count of zero rather than
# an absence.
@dataclass(frozen=True)
class FragmentAggregate:
    """One reduction of the fragments in a region, and the fragment count.

    Attributes
    ----------
    count : int
        The number of fragments that the read saw: the fragments that overlap
        the region, that pass the overlap fractions and that ``score_filter``
        keeps. A region with no fragments gives ``0``. A filter that rejects
        every fragment gives ``0`` too.
    values : tuple[ScoreValue, ...]
        One reduced value for each query, in the order of the queries. The
        same score asked twice with two aggregators gives two values. A value is
        ``None`` when its aggregator has nothing to reduce.
    """

    count: int
    values: tuple[ScoreValue, ...]


#: One fragment as the record plane yields it -- its own unclipped span,
#: then the values for the scores asked for, positionally, in the list
#: :meth:`~.base.GenomicScore.values_from_records` built.  The
#: folding read folds these as they are; the public reads answer them
#: through :func:`_tupled`.
_RawSegment = tuple[int, int, list[ScoreValue]]


def _tupled(
    segments: Iterable[_RawSegment],
) -> Generator[tuple[int, int, tuple[ScoreValue, ...]], None, None]:
    """The public reads' face of a segment stream: ``values`` as a tuple."""
    return ((beg, end, tuple(values)) for beg, end, values in segments)


#: A query list resolved for the fold: the ``(score_id, aggregator)``
#: requests, and the distinct score ids they fetch.  Tuples, because it is
#: memoised and handed out to every read that asks the same list.
_ResolvedQueries = tuple[tuple[tuple[str, str], ...], tuple[str, ...]]


def _query_resolver(
    score_definitions: dict[str, GenomicScoreDef],
    all_scores: list[str],
    resource_id: str,
    *,
    maxsize: int,
) -> Callable[[tuple[ScoreAggregationQuery, ...]], _ResolvedQueries]:
    """A memoised resolver of query tuples for ONE score's definitions.

    :func:`~.aggregation.resolve_aggregation_queries` under a
    ``functools.lru_cache``, built per score instance rather than
    decorating a method, for the reason
    ``TabixGenomicPositionTable.get_file_chromosomes`` records: a
    class-level ``lru_cache`` on a method is keyed by ``self`` and holds
    every instance for the life of the process.  This closes over what
    resolution reads -- the definitions, the score list, the resource id
    -- and nothing else, so it neither references the score nor outlives
    it.
    """
    @functools.lru_cache(maxsize=maxsize)
    def resolve(
        queries: tuple[ScoreAggregationQuery, ...],
    ) -> _ResolvedQueries:
        requests = resolve_aggregation_queries(
            queries,
            score_definitions=score_definitions,
            all_scores=all_scores,
            resource_id=resource_id)
        return tuple(requests), tuple(request_score_ids(requests))
    return resolve


class _CountingStream:
    """A pass-through over segments that tallies them as they flow.

    The whole reason the folding read can answer a count at all without a
    second walk.  :func:`~.aggregation.fold_region_segments` consumes the
    stream itself, so the tally cannot be a local the caller increments --
    it lives here and is read once the fold has RETURNED.  Reading it
    before then answers however far the fold happened to have got.

    Kept a class, and kept private, deliberately.  It knows nothing about
    fragments, so it looks like shared machinery -- but this package
    promotes a helper into :mod:`.aggregation` when TWO readers need the
    same derivation (see :func:`~.aggregation.request_score_ids`), and
    this has one.  Should a second kind come to want a per-walk tally, the
    move is to make the fold report what it folded and delete this, rather
    than to relocate it.
    """

    def __init__(self, segments: Iterable[_RawSegment]) -> None:
        self._segments = segments
        self.count = 0

    def __iter__(self) -> Generator[_RawSegment, None, None]:
        for segment in self._segments:
            self.count += 1
            yield segment


# Nothing here is copy-number specific; a CNV collection is one application
# of it. Accepts either resource type in
# :data:`~gain.genomic_resources.resource_types.FRAGMENT_SCORE_TYPES`,
# warning once per resource on the deprecated one.
class FragmentScore(GenomicScore):
    """A genomic score over fragments: intervals that carry attributes.

    A fragment keeps its own begin and end, and fragments may overlap. A
    copy-number variant collection is one use of this kind. The resource type
    is ``fragment_score``. The older type name ``cnv_collection`` is still
    accepted, with a deprecation warning.

    Build a fragment score with
    :func:`~gain.genomic_resources.genomic_scores.build_fragment_score_from_resource_id`.
    The score is built closed. Call ``open()`` before the first read.

    The reads come in pairs. The plural form takes a ``scores`` list and
    returns values parallel to it. The singular form takes one ``score``.

    Parameters
    ----------
    resource : GenomicResource
        The resource of type ``fragment_score`` or ``cnv_collection``.

    Raises
    ------
    ValueError
        If the type of the resource is not a fragment score type.
    """

    #: The unit that the histograms of this kind count: ``"fragments"``.
    HISTOGRAM_COUNT_UNIT: ClassVar[str] = "fragments"

    # As AlleleScore, except that strings join rather than list -- a fragment
    # score's string attributes are rendered into one cell.  Owned by the
    # score class, so no score-definition subclass is needed to carry them.
    #
    #: The default aggregator for each value type, for a score whose
    #: configuration states no ``aggregator``: ``max`` for ``float`` and
    #: ``int``, ``join(,)`` for ``str`` and none for ``bool``.
    DEFAULT_AGGREGATORS: ClassVar[dict[str, str | None]] = {
        "float": "max",
        "int": "max",
        "str": "join(,)",
        "bool": None,
    }

    #: How many distinct query lists a score remembers resolving.  An
    #: annotator asks one list forever; a caller that asks many (the web
    #: api, per request) must not grow the score without limit.
    _RESOLVED_QUERIES_BOUND: ClassVar[int] = 64

    def __init__(self, resource: GenomicResource):
        resource_type = resource.get_type()
        if resource_type not in FRAGMENT_SCORE_TYPES:
            accepted = " or ".join(
                f"'{score_type}'" for score_type in FRAGMENT_SCORE_TYPES)
            raise ValueError(
                "The resource provided to FragmentScore should be of "
                f"{accepted} type, not a '{resource_type}'")
        if resource_type == LEGACY_FRAGMENT_SCORE_TYPE:
            # Warned here, not from the `in FRAGMENT_SCORE_TYPES` membership
            # tests: those also run inside the repository layer's SQL
            # predicate, which would fire the warning on every query rather
            # than on every open.
            #
            # Announced through `warn_deprecated_spelling` rather than
            # logged outright because construction is NOT once per resource:
            # the statistics scan rebuilds the score inside every min/max
            # and histogram task, so a repo-repair over an hg38-scale
            # resource passes here once per region.  Named by full id: a
            # repository may hold several versions of one resource id, each
            # its own directory with its own config to migrate, and the
            # announce-once-per-message rule would otherwise print one line
            # for all of them and name none of them precisely.
            warn_deprecated_spelling(
                logger, "resource type",
                LEGACY_FRAGMENT_SCORE_TYPE, PREFERRED_FRAGMENT_SCORE_TYPE,
                found_in=f"Resource '{resource.get_full_id()}'")
        super().__init__(resource)
        self._resolve_query_tuple = _query_resolver(
            self.score_definitions, self.get_all_scores(), self.resource_id,
            maxsize=self._RESOLVED_QUERIES_BOUND)

    @functools.cached_property
    def _default_queries(self) -> tuple[ScoreAggregationQuery, ...]:
        """Every score the resource defines, each with its own default."""
        return tuple(
            ScoreAggregationQuery(score_id)
            for score_id in self.get_all_scores()
        )

    # The kind's whole reason for weighing by record rather than by span: a
    # fragment is a measured thing, not a run of per-base values, so its length
    # says nothing about how many times its value counts.
    #
    # A constant, which is elementwise: the base's
    # :meth:`~.base.GenomicScore.record_weights` fills it out to a batch's
    # shape.
    @classmethod
    def record_weight(
        cls,
        left: int,  # ruff: ignore[unused-class-method-argument]
        right: int,  # ruff: ignore[unused-class-method-argument]
    ) -> int:
        """Return the weight of one fragment in an aggregate: always ``1``.

        :meta private:
        """
        return 1

    # The GenomicScore schema plus a per-score ``aggregator``.
    @staticmethod
    def get_schema() -> dict[str, Any]:
        """Return the configuration schema of a fragment score resource.

        Returns
        -------
        dict[str, Any]
            The schema of a genomic score, with an ``aggregator`` entry added to
            each score definition.
        """
        schema = copy.deepcopy(GenomicScore.get_schema())
        scores_schema = schema["scores"]["schema"]["schema"]
        scores_schema["aggregator"] = AGGREGATOR_SCHEMA
        return schema

    # **Private to the fragment plane.**
    # :meth:`~.base.GenomicScore.fetch_region_segments_scores` through
    # :func:`_tupled`, and not a read to reach for directly; it keeps its name
    # because it had one, not because the name is an invitation. It diverges
    # from the internals beside it (``_score_segments``, ``_region_read_defs``)
    # in spelling only.
    #
    # What it adds to the base read is the tuple, a locus that is required
    # rather than defaulted, and a ``list`` of score ids. What the fragment
    # plane once had a private TWIN of that method for was ``score_filter``,
    # which the base method takes now (gain#1272).
    #
    # One entry per overlapping fragment, in table order, each reporting the
    # fragment's OWN extent -- unclipped, even where it runs past the region
    # asked for. What a partial overlap means depends on what the caller is
    # computing, so ADR 0008 leaves it to them; a caller that wants the window
    # intersected composes :func:`~.records.clip_span`.
    #
    # ``values`` is positional, parallel to ``scores`` as requested (to
    # :meth:`~.base.GenomicScore.get_all_scores` when that is ``None``), rather
    # than a mapping: the caller already knows what it asked for and in what
    # order. A value may be ``None`` where the record carries no value for that
    # score -- unlike the per-position reads, that is the only ``None`` here,
    # because a fragment score has no notion of an uncovered position.
    #
    # ``score_filter`` reads the RECORD, so it may name any score the resource
    # defines, including one outside ``scores``, and a rejected fragment costs
    # no extraction.
    #
    # The REQUEST is checked when this is called; the READING is lazy. A closed
    # score, a contig this resource does not have and an unknown score id are
    # refused before the first ``next()`` rather than on it, for the reason
    # :meth:`~.base.GenomicScore._region_read_defs` gives. A malformed RECORD is
    # a different matter and is refused when the record is reached: a fragment
    # whose end precedes its begin ends the iteration then, mid-stream.
    #
    # **One live read at a time.** A score serves a single region read at once
    # -- the table's line iterator and line buffer are the table's, not the
    # generator's -- so starting a second read invalidates one that is still
    # being consumed, and on a tabix-backed table the two then answer each
    # other's records with no error raised. Materialising is what makes a held
    # answer safe to keep: ``kept = list(score.fetch_fragment_scores(chrom,
    # beg, end))``.
    #
    # Abandoning a read mid-stream is safe and costs only a
    # ``TabixGenomicPositionTable`` buffer prune, which gain#1120 moved
    # into a ``finally`` -- though that runs
    # when the generator is released, so a caller holding a reference to a
    # ``close()``-ed generator still holds the read open.
    def fetch_fragment_scores(
        self, chrom: str,
        start: int, stop: int,
        scores: list[str] | None = None,
        *,
        score_filter: ScoreFilter | None = None,
    ) -> Generator[tuple[int, int, tuple[ScoreValue, ...]], None, None]:
        """Stream the fragments over a region at the record plane.

        Use :meth:`get_fragment_scores_overlapping_region` instead.

        :meta private:
        """
        return _tupled(self.fetch_region_segments_scores(
            chrom, start, stop, scores, score_filter=score_filter))

    # -- The logical read plane (#1123) -------------------------------------
    #
    # On this plane a fragment score is a collection of measured intervals:
    # one entry per FRAGMENT, carrying that fragment's own span and the
    # values it was asked for.  That is the kind's semantic unit, as a
    # position is the position kind's -- there is no per-base expansion
    # here, because a fragment's length says nothing about how many times
    # its value counts (see :meth:`record_weight`).
    #
    # ``get_*`` is this plane; ``fetch_*`` is the record plane beneath it.
    # The singular of each pair is a thin wrapper over its plural through
    # :meth:`~.base.GenomicScore._resolve_single_score`, and everything
    # after the locus is keyword-only -- see
    # :meth:`get_fragment_scores_overlapping_region` for why that is not
    # cosmetic.

    def _guard_overlap_fraction(
        self, name: str, fraction: float | None,
    ) -> None:
        """Refuse an overlap threshold no fraction can ever reach.

        An *overlap / length* ratio lies in ``[0, 1]``, so a threshold
        outside it names a filter that is either vacuous or empty whatever
        the data -- a caller error, and one worth reporting where it is
        made.  Checked when the read is CALLED rather than on the first
        ``next()``, which is where every other request guard on this plane
        fires: a refusal deferred into a generator body reaches only a
        caller that iterates, and hands a caller that does not iterate a
        plausible nothing.
        """
        if fraction is not None and not 0.0 <= fraction <= 1.0:
            raise ValueError(
                f"genomic score <{self.resource_id}> was asked for "
                f"{name}={fraction}; an overlap fraction is between 0 and 1")

    # The plane's workhorse. Entries are shaped as :meth:`fetch_fragment_scores`
    # shapes them -- one per overlapping fragment, in table order, at the
    # fragment's OWN unclipped extent, with ``values`` positional and parallel
    # to ``scores`` -- and ``score_filter`` behaves as it documents there. What
    # this adds is the two thresholds.
    #
    # The two overlap fractions are :func:`~.records.overlap_fractions_admit`,
    # applied with this region as ``[start, end]`` and each fragment as the
    # record; that function defines them. Both unset filters nothing -- which is
    # what this read did before the thresholds existed -- and hands the stream
    # through without consulting the predicate.
    #
    # They SELECT, they do not RESHAPE: a fragment that passes is still reported
    # at its own unclipped span. That is this plane's rule and it has no
    # decision record of its own -- ADR 0008 is about who validates, not about
    # what a read may do to a span, so it is not the authority for it.
    #
    # Everything after the locus is keyword-only, and that is not cosmetic:
    # :meth:`fetch_fragment_scores` takes its score list positionally, so a
    # caller migrating from ``fetch_fragment_scores(chrom, start, stop,
    # scores)`` would otherwise bind that list to whatever this signature
    # happens to put fourth -- no error, just a plausible-looking filtered
    # result.
    #
    # The REQUEST is checked when this is called; the READING is lazy.
    #
    # The table's line iterator and line buffer belong to the table, not to the
    # generator, so starting a second read invalidates one that is still being
    # consumed. A held generator may be *closed* across another query, never
    # *resumed* across one.
    def get_fragment_scores_overlapping_region(
        self, chrom: str, start: int, end: int,
        *,
        scores: list[str] | None = None,
        score_filter: ScoreFilter | None = None,
        min_region_overlap_fraction: float | None = None,
        min_fragment_overlap_fraction: float | None = None,
    ) -> Generator[tuple[int, int, tuple[ScoreValue, ...]], None, None]:
        """Yield the fragments that overlap a region.

        Each fragment is reported at its own extent. The extent is not clipped
        to the region, also where the fragment runs past it. The fragments come
        in table order.

        The reads check the request when you call them, and read the data later.
        A closed score, an unknown contig, an unknown score id, an invalid
        region and an overlap fraction outside the range from 0 to 1 raise
        before the first ``next()``.

        A score serves one region read at a time. Starting a second read
        invalidates a read that you still consume, and a tabix-backed table then
        answers with the records of the other read and no error. Close a held
        generator, or call ``list(...)`` on it, before the next read.

        Parameters
        ----------
        chrom : str
            The contig name.
        start : int
            The first position of the region (1-based, inclusive).
        end : int
            The last position of the region (1-based, inclusive).
        scores : list[str] | None, default None
            The score ids to read. ``None`` reads every score of the resource.
        score_filter : ScoreFilter | None, default None
            A filter from :meth:`~GenomicScore.compile_filter`. The read drops
            the fragments that the filter rejects. The filter may name a score
            that is not in ``scores``. ``None`` keeps every fragment.
        min_region_overlap_fraction : float | None, default None
            The fragment must cover at least this fraction of the region.
            ``None`` sets no threshold.
        min_fragment_overlap_fraction : float | None, default None
            At least this fraction of the fragment must fall in the region.
            ``None`` sets no threshold.

        Returns
        -------
        Generator[tuple[int, int, tuple[ScoreValue, ...]], None, None]
            One ``(begin, end, values)`` tuple for each fragment. ``values`` is
            parallel to ``scores``. A value is ``None`` when the fragment has no
            value for that score. The generator is empty when no fragment
            overlaps the region.

        Raises
        ------
        ValueError
            If the score is closed, the contig or a score id is unknown,
            ``start`` is below 1, ``end`` is before ``start``, or an overlap
            fraction is outside the range from 0 to 1.
        """
        return _tupled(self._segments_overlapping_region(
            chrom, start, end,
            scores=scores,
            score_filter=score_filter,
            min_region_overlap_fraction=min_region_overlap_fraction,
            min_fragment_overlap_fraction=min_fragment_overlap_fraction))

    def _segments_overlapping_region(
        self, chrom: str, start: int, end: int,
        *,
        scores: Sequence[str] | None = None,
        score_filter: ScoreFilter | None = None,
        min_region_overlap_fraction: float | None = None,
        min_fragment_overlap_fraction: float | None = None,
    ) -> Generator[_RawSegment, None, None]:
        """The overlapping-region SELECTION, over the record plane's stream.

        Everything :meth:`get_fragment_scores_overlapping_region` decides
        -- the eager guards, ``score_filter``, the two overlap fractions --
        is decided here, once, for that read and for the folding read
        alike; so what the public read yields is exactly what the fold
        sees and :class:`FragmentAggregate` counts.
        """
        self._guard_region_span(start, end)
        self._guard_overlap_fraction(
            "min_region_overlap_fraction", min_region_overlap_fraction)
        self._guard_overlap_fraction(
            "min_fragment_overlap_fraction", min_fragment_overlap_fraction)
        rows = self.fetch_region_segments_scores(
            chrom, start, end, scores, score_filter=score_filter)
        if (min_region_overlap_fraction is None
                and min_fragment_overlap_fraction is None):
            # No threshold: hand the stream through rather than ask the
            # predicate per fragment (gain#1157).  Every guard has run.
            return rows
        return (
            (beg, end_, values)
            for beg, end_, values in rows
            if overlap_fractions_admit(
                beg, end_, start, end,
                min_region_fraction=min_region_overlap_fraction,
                min_record_fraction=min_fragment_overlap_fraction)
        )

    def get_fragment_score_overlapping_region(
        self, chrom: str, start: int, end: int,
        *,
        score: str | None = None,
        score_filter: ScoreFilter | None = None,
        min_region_overlap_fraction: float | None = None,
        min_fragment_overlap_fraction: float | None = None,
    ) -> Generator[tuple[int, int, ScoreValue], None, None]:
        """Yield the fragments that overlap a region, with the value of one
        score.

        This is the singular form of
        :meth:`get_fragment_scores_overlapping_region`. The same limits apply.

        Parameters
        ----------
        chrom : str
            The contig name.
        start : int
            The first position of the region (1-based, inclusive).
        end : int
            The last position of the region (1-based, inclusive).
        score : str | None, default None
            The score id. ``None`` is valid only when the resource defines
            exactly one score.
        score_filter : ScoreFilter | None, default None
            A filter from :meth:`~GenomicScore.compile_filter`. ``None`` keeps
            every fragment.
        min_region_overlap_fraction : float | None, default None
            The fragment must cover at least this fraction of the region.
            ``None`` sets no threshold.
        min_fragment_overlap_fraction : float | None, default None
            At least this fraction of the fragment must fall in the region.
            ``None`` sets no threshold.

        Returns
        -------
        Generator[tuple[int, int, ScoreValue], None, None]
            One ``(begin, end, value)`` tuple for each fragment. The value is
            ``None`` when the fragment has no value for the score. The generator
            is empty when no fragment overlaps the region.

        Raises
        ------
        ValueError
            If ``score`` is ``None`` and the resource defines more than one
            score, or for any reason that
            :meth:`get_fragment_scores_overlapping_region` lists.
        """
        rows = self.get_fragment_scores_overlapping_region(
            chrom, start, end,
            scores=[self._resolve_single_score(score)],
            score_filter=score_filter,
            min_region_overlap_fraction=min_region_overlap_fraction,
            min_fragment_overlap_fraction=min_fragment_overlap_fraction)
        return ((beg, end_, values[0]) for beg, end_, values in rows)

    # A one-position region read of
    # :meth:`get_fragment_scores_overlapping_region`, which documents what an
    # entry is; spans are unclipped here too.
    #
    # Materialised, for the caller's convenience: a point query returns a
    # handful of fragments, callers want all of them, and a materialised answer
    # can be measured with ``len()``, iterated twice and kept across a later
    # read. It is NOT the drain hazard
    # :meth:`~.position.PositionScore.get_scores_at_position` documents: that
    # was gain#1120's to fix, and abandoning a region generator has been safe
    # since.
    #
    # The overlap fractions are deliberately absent. Over a one-base region
    # ``overlap / region_length`` is always 1, so the region fraction could only
    # ever be vacuous, and the fragment fraction of a single base is a ratio no
    # caller has been found to want.
    #
    # ``pos`` is refused below 1, through the same
    # :meth:`~.base.GenomicScore._guard_region_span` the region reads use: a
    # backend that reads ``0`` as "unbounded" would otherwise answer a caller
    # error with the whole contig.
    def get_fragment_scores_at_position(
        self, chrom: str, pos: int,
        *,
        scores: list[str] | None = None,
        score_filter: ScoreFilter | None = None,
    ) -> Sequence[tuple[int, int, tuple[ScoreValue, ...]]]:
        """Return the fragments that cover a position.

        Each fragment is reported at its full extent, not clipped to the
        position.

        Parameters
        ----------
        chrom : str
            The contig name.
        pos : int
            The position (1-based).
        scores : list[str] | None, default None
            The score ids to read. ``None`` reads every score of the resource.
        score_filter : ScoreFilter | None, default None
            A filter from :meth:`~GenomicScore.compile_filter`. ``None`` keeps
            every fragment.

        Returns
        -------
        Sequence[tuple[int, int, tuple[ScoreValue, ...]]]
            One ``(begin, end, values)`` tuple for each fragment. The list is
            empty when no fragment covers the position. You can keep the list
            across a later read.

        Raises
        ------
        ValueError
            If ``pos`` is below 1, or for any other reason that
            :meth:`get_fragment_scores_overlapping_region` lists.
        """
        return list(self.get_fragment_scores_overlapping_region(
            chrom, pos, pos, scores=scores, score_filter=score_filter))

    def get_fragment_score_at_position(
        self, chrom: str, pos: int,
        *,
        score: str | None = None,
        score_filter: ScoreFilter | None = None,
    ) -> Sequence[tuple[int, int, ScoreValue]]:
        """Return the fragments that cover a position, with the value of one
        score.

        This is the singular form of :meth:`get_fragment_scores_at_position`.

        Parameters
        ----------
        chrom : str
            The contig name.
        pos : int
            The position (1-based).
        score : str | None, default None
            The score id. ``None`` is valid only when the resource defines
            exactly one score.
        score_filter : ScoreFilter | None, default None
            A filter from :meth:`~GenomicScore.compile_filter`. ``None`` keeps
            every fragment.

        Returns
        -------
        Sequence[tuple[int, int, ScoreValue]]
            One ``(begin, end, value)`` tuple for each fragment. The list is
            empty when no fragment covers the position.

        Raises
        ------
        ValueError
            If ``score`` is ``None`` and the resource defines more than one
            score, or for any reason that
            :meth:`get_fragment_scores_at_position` lists.
        """
        return [
            (beg, end, values[0])
            for beg, end, values in self.get_fragment_scores_at_position(
                chrom, pos,
                scores=[self._resolve_single_score(score)],
                score_filter=score_filter)
        ]

    # The partition rule is :func:`~.records.owns_record`.
    #
    # The allele statistics scan makes the same ownership claim inline, as
    # ``_owns``, but spells it ``clip_span(pos, pos, start, end)``: an allele
    # row sits AT one position, so for it the record partition and the position
    # one coincide. For a fragment they emphatically do not, which is why this
    # read names the record partition rather than reusing that spelling. That
    # scan is left as it is.
    #
    # :meth:`get_scores_in_bins` reads through it: each fragment lands in the
    # one bin holding its begin, which is this partition at bin granularity.
    #
    # The overlap fractions are deliberately absent: this read partitions, and a
    # fraction filter would let a fragment fall out of every window, which is
    # the property being partitioned FOR.
    def get_fragment_scores_starting_in_region(
        self, chrom: str, start: int, end: int,
        *,
        scores: list[str] | None = None,
        score_filter: ScoreFilter | None = None,
    ) -> Generator[tuple[int, int, tuple[ScoreValue, ...]], None, None]:
        """Yield the fragments that begin in a region.

        The read yields the fragments whose begin lies in ``[start, end]``. A
        set of adjacent windows answers each fragment from exactly one window,
        with no duplicate and no gap. Use this read for chunked and parallel
        work. :meth:`get_fragment_scores_overlapping_region` answers a fragment
        from every window that it reaches into.

        Each fragment is reported at its own extent. The limit of one live read
        per score applies, as for
        :meth:`get_fragment_scores_overlapping_region`. The read has no overlap
        fractions, because a fraction filter could let a fragment fall out of
        every window.

        Parameters
        ----------
        chrom : str
            The contig name.
        start : int
            The first position of the region (1-based, inclusive).
        end : int
            The last position of the region (1-based, inclusive).
        scores : list[str] | None, default None
            The score ids to read. ``None`` reads every score of the resource.
        score_filter : ScoreFilter | None, default None
            A filter from :meth:`~GenomicScore.compile_filter`. ``None`` keeps
            every fragment.

        Returns
        -------
        Generator[tuple[int, int, tuple[ScoreValue, ...]], None, None]
            One ``(begin, end, values)`` tuple for each fragment. ``values`` is
            parallel to ``scores``. The generator is empty when no fragment
            begins in the region.

        Raises
        ------
        ValueError
            If ``start`` is below 1, ``end`` is before ``start``, the score is
            closed, or the contig or a score id is unknown.
        """
        self._guard_region_span(start, end)
        rows = self.fetch_fragment_scores(
            chrom, start, end, scores, score_filter=score_filter)
        return (
            (beg, end_, values)
            for beg, end_, values in rows
            if owns_record(beg, start, end)
        )

    def get_fragment_score_starting_in_region(
        self, chrom: str, start: int, end: int,
        *,
        score: str | None = None,
        score_filter: ScoreFilter | None = None,
    ) -> Generator[tuple[int, int, ScoreValue], None, None]:
        """Yield the fragments that begin in a region, with the value of one
        score.

        This is the singular form of
        :meth:`get_fragment_scores_starting_in_region`. The same partition and
        the same limits apply.

        Parameters
        ----------
        chrom : str
            The contig name.
        start : int
            The first position of the region (1-based, inclusive).
        end : int
            The last position of the region (1-based, inclusive).
        score : str | None, default None
            The score id. ``None`` is valid only when the resource defines
            exactly one score.
        score_filter : ScoreFilter | None, default None
            A filter from :meth:`~GenomicScore.compile_filter`. ``None`` keeps
            every fragment.

        Returns
        -------
        Generator[tuple[int, int, ScoreValue], None, None]
            One ``(begin, end, value)`` tuple for each fragment. The generator
            is empty when no fragment begins in the region.

        Raises
        ------
        ValueError
            If ``score`` is ``None`` and the resource defines more than one
            score, or for any reason that
            :meth:`get_fragment_scores_starting_in_region` lists.
        """
        rows = self.get_fragment_scores_starting_in_region(
            chrom, start, end,
            scores=[self._resolve_single_score(score)],
            score_filter=score_filter)
        return ((beg, end_, values[0]) for beg, end_, values in rows)

    # -- The folding read ---------------------------------------------------
    #
    # ``_agg`` is on the overlapping-region predicate ALONE, the one with a
    # consumer -- as ``PositionScore`` grew ``_agg`` only where something
    # needed it.  A predicate that later wants one adds it then.

    def _resolve_fragment_aggregation_queries(
        self, queries: Sequence[ScoreAggregationQuery] | None,
    ) -> _ResolvedQueries:
        """Resolve queries to fold requests and the columns they fetch.

        :func:`~.aggregation.resolve_aggregation_queries` and
        :func:`~.aggregation.request_score_ids`, REMEMBERED per distinct
        query list by CONTENT: a
        :class:`~..aggregators.ScoreAggregationQuery` is frozen, so the
        tuple of them hashes, and both halves are pure over it and over
        ``score_definitions``, fixed at construction.  Never by the list's
        identity, which a caller may mutate between reads.  The memo is a
        per-instance ``lru_cache`` (see :func:`_query_resolver`) bounded
        by ``_RESOLVED_QUERIES_BOUND``, because the read is public and the
        web api asks per request; it keeps only what resolved, so a
        refused list is refused again on every call.

        Private, unlike its siblings' resolvers: an attribute naming no
        aggregator is not an error on this kind, it answers the fragment
        count instead, so nothing asks at pipeline load.

        The aggregators are no part of it.  The read builds them FRESH per
        call (:func:`~.aggregation.build_region_aggregators`): an
        accumulator is mutable and not thread-safe, so a reused one would
        have two concurrent reads accumulating into each other.  That does
        not make the read thread-safe -- the TABLE's line iterator is
        shared too, as :meth:`get_fragment_scores_overlapping_region` says
        under "one live region read per score at a time" -- and the worst
        concurrent resolution can do is resolve one list twice.

        The allele and position folding reads pay the same per-call
        resolution.  A resolved request crossing the seam instead, which
        would supersede this memo, was measured and declined (gain#1300;
        see ``.out-of-scope/point-read-pre-resolution.md``), so the memo
        stays this kind's.

        ``queries`` of ``None`` means every score the resource defines,
        each with its own default aggregator, remembered under that list
        as if the caller had spelled it out.
        """
        return self._resolve_query_tuple(
            self._default_queries if queries is None else tuple(queries))

    # The plane's folding read: what
    # :meth:`get_fragment_scores_overlapping_region` yields, already reduced, in
    # ONE pass that also counts what it saw. The selection is that read's
    # exactly, because it is the same code: both
    # consume :meth:`_segments_overlapping_region`, and only the public read
    # tuples what comes out. A fragment is weighed by :meth:`record_weight`.
    #
    # The count comes off the walk itself (``_CountingStream``), not off a
    # ``count`` aggregator: ``CountAggregator`` has the wrong count semantics
    # here -- it skips ``None`` values, so it counts non-null VALUES rather
    # than fragments, and answers ``None`` rather than ``0`` for a region no
    # fragment overlaps.
    #
    # THIS READ holds nothing per fragment: the stream is folded as it arrives
    # and never materialised. Whether the CALL is constant in the number of
    # fragments is then the AGGREGATORS' business, and they divide three ways:
    #
    # - constant -- ``max``, ``min``, ``mean``, ``count``, ``bool``;
    # - one entry per DISTINCT value -- ``mode``, ``value_count``, so bounded
    #   by how many values a resource has rather than by how many fragments a
    #   region holds;
    # - one entry per FRAGMENT -- ``list``, ``median``, ``concatenate`` and
    #   ``join``. ``join(,)`` is the DEFAULT for a ``str`` score, which makes
    #   this the ordinary case for a CNV collection rather than an exotic one.
    #
    # Under the last group the fold still allocates per fragment. What this read
    # removes is the SECOND copy the annotator used to build beside it, which is
    # a halving there and a flattening everywhere else.
    def get_fragment_scores_overlapping_region_agg(
        self, chrom: str, start: int, end: int,
        *,
        queries: Sequence[ScoreAggregationQuery] | None = None,
        min_region_overlap_fraction: float | None = None,
        min_fragment_overlap_fraction: float | None = None,
        score_filter: ScoreFilter | None = None,
    ) -> FragmentAggregate:
        """Reduce the fragments that overlap a region to one value for each
        query.

        The read selects the fragments as
        :meth:`get_fragment_scores_overlapping_region` does. The overlap
        fractions and ``score_filter`` have the same meaning. Each fragment
        counts once, however long it is. The read folds the fragments as they
        arrive and does not keep them. A few aggregators keep one entry for each
        fragment while they run: ``list``, ``median``, ``concatenate`` and
        ``join``. The default aggregator of a ``str`` score is ``join(,)``,
        which is one of them.

        The ``count`` of the result counts fragments. A query with the
        ``count`` aggregator counts the non-null values instead, and gives
        ``None`` for an empty region.

        Parameters
        ----------
        chrom : str
            The contig name.
        start : int
            The first position of the region (1-based, inclusive).
        end : int
            The last position of the region (1-based, inclusive).
        queries : Sequence[ScoreAggregationQuery] | None, default None
            The scores to reduce, each with an aggregator. A query with no
            aggregator uses the default of the score. ``None`` reduces every
            score of the resource with its default aggregator.
        min_region_overlap_fraction : float | None, default None
            The fragment must cover at least this fraction of the region.
            ``None`` sets no threshold.
        min_fragment_overlap_fraction : float | None, default None
            At least this fraction of the fragment must fall in the region.
            ``None`` sets no threshold.
        score_filter : ScoreFilter | None, default None
            A filter from :meth:`~GenomicScore.compile_filter`. ``None`` keeps
            every fragment.

        Returns
        -------
        FragmentAggregate
            The number of fragments that the read saw, and one reduced value for
            each query. When no fragment overlaps the region, ``count`` is
            ``0``.

        Raises
        ------
        ValueError
            If a query names an unknown score or an aggregator that the score
            does not accept, or for any reason that
            :meth:`get_fragment_scores_overlapping_region` lists.
        """
        requests, score_ids = self._resolve_fragment_aggregation_queries(
            queries)
        aggregators = build_region_aggregators(
            requests, resource_id=self.resource_id)
        segments = _CountingStream(
            self._segments_overlapping_region(
                chrom, start, end,
                scores=score_ids,
                score_filter=score_filter,
                min_region_overlap_fraction=min_region_overlap_fraction,
                min_fragment_overlap_fraction=min_fragment_overlap_fraction))
        values = fold_region_segments(
            segments, aggregators, requests,
            score_ids=score_ids, weigh=self.record_weight)
        return FragmentAggregate(segments.count, tuple(values))

    # Alone among this plane's singular reads it does NOT unwrap: it answers the
    # same :class:`FragmentAggregate`, whose ``values`` is a one-element tuple.
    # The others have a bare value to answer with; this one's answer is a count
    # and a reduction together, and the count is a property of the QUERY rather
    # than of the score named -- so there is nothing for a bare value to be.
    def get_fragment_score_overlapping_region_agg(
        self, chrom: str, start: int, end: int,
        *,
        score: str | None = None,
        aggregator: str | None = None,
        min_region_overlap_fraction: float | None = None,
        min_fragment_overlap_fraction: float | None = None,
        score_filter: ScoreFilter | None = None,
    ) -> FragmentAggregate:
        """Reduce the fragments that overlap a region to one value for one
        score.

        This is the singular form of
        :meth:`get_fragment_scores_overlapping_region_agg`. It returns the same
        :class:`FragmentAggregate`, and ``values`` has one element.

        Parameters
        ----------
        chrom : str
            The contig name.
        start : int
            The first position of the region (1-based, inclusive).
        end : int
            The last position of the region (1-based, inclusive).
        score : str | None, default None
            The score id. ``None`` is valid only when the resource defines
            exactly one score.
        aggregator : str | None, default None
            The aggregator. ``None`` uses the default aggregator of the score.
        min_region_overlap_fraction : float | None, default None
            The fragment must cover at least this fraction of the region.
            ``None`` sets no threshold.
        min_fragment_overlap_fraction : float | None, default None
            At least this fraction of the fragment must fall in the region.
            ``None`` sets no threshold.
        score_filter : ScoreFilter | None, default None
            A filter from :meth:`~GenomicScore.compile_filter`. ``None`` keeps
            every fragment.

        Returns
        -------
        FragmentAggregate
            The number of fragments that the read saw, and a tuple with the one
            reduced value.

        Raises
        ------
        ValueError
            If ``score`` is ``None`` and the resource defines more than one
            score, or for any reason that
            :meth:`get_fragment_scores_overlapping_region_agg` lists.
        """
        return self.get_fragment_scores_overlapping_region_agg(
            chrom, start, end,
            queries=[ScoreAggregationQuery(
                self._resolve_single_score(score), aggregator)],
            min_region_overlap_fraction=min_region_overlap_fraction,
            min_fragment_overlap_fraction=min_fragment_overlap_fraction,
            score_filter=score_filter)

    # -- The binned read ----------------------------------------------------

    # Answers :func:`~.aggregation.fold_into_bins`'s block. Which aggregators
    # are accepted at all, and what an empty cell holds, are the fold's rules,
    # not this read's.
    #
    # ``aggregator`` of ``None`` on a query is the score's configured default,
    # as everywhere a :class:`~..aggregators.ScoreAggregationQuery` is read; a
    # default the fold does not accept (``join`` for a ``str`` score) is refused
    # like a named one. A numeric-only aggregator on a non-numeric score is
    # refused by :func:`~..aggregators.validate_aggregator`.
    #
    # A contig the resource lacks answers the all-empty block rather than being
    # refused: a genome-wide pass over fragment files that each skip some
    # contigs is the ordinary case.
    def get_scores_in_bins(
        self, chrom: str, start: int, end: int, bin_size: int,
        queries: Sequence[ScoreAggregationQuery],
    ) -> np.ndarray:
        """Reduce the fragments that start in each bin, one column for each
        query.

        The grid is global and starts at position 1. Row ``i`` is the bin with
        index ``calc_bin_index(bin_size, start) + i``. The rows are whole bins.
        The read does not clip them to the region.

        A fragment belongs to the bin that holds its begin, however far it runs.
        The blocks of two adjacent calls that split on a bin edge stack into the
        block of their union. The fragments are those that
        :meth:`get_fragment_scores_starting_in_region` yields for ``[start,
        end]``.

        The fragment count of a bin is the query
        ``ScoreAggregationQuery(<score>, "count")``. It counts the fragments
        that start in the bin and have a value for the score. For a score that
        every fragment carries, it is the number of fragments that start in the
        bin.

        The read answers all queries from one read of the region. It
        materialises the block, so the limit of one live read applies for the
        duration of the call only.

        Parameters
        ----------
        chrom : str
            The contig name. A contig that the resource lacks gives the block of
            empty cells, not an error.
        start : int
            The first position of the region (1-based, inclusive).
        end : int
            The last position of the region (1-based, inclusive).
        bin_size : int
            The width of a bin in positions. It must be at least 1.
        queries : Sequence[ScoreAggregationQuery]
            The scores to reduce, each with an aggregator. A query with no
            aggregator uses the default of the score.

        Returns
        -------
        numpy.ndarray
            A ``float64`` array of shape ``(n_bins, len(queries))``. Column
            ``j`` reduces ``queries[j]``. An empty cell is ``0`` for ``count``
            and ``sum``. It is ``NaN`` for ``mean``, ``max``, ``min``,
            ``median`` and ``product``.

        Raises
        ------
        ValueError
            If ``start`` is below 1, ``end`` is before ``start``, ``bin_size``
            is below 1, a score is unknown, or an aggregator is not accepted for
            the score. The read raises before it reads the first fragment.
        """
        self._guard_region_span(start, end)
        if bin_size < 1:
            raise ValueError(
                f"genomic score <{self.resource_id}> asked for bins of "
                f"size {bin_size}; a bin holds at least one position")
        requests, score_ids = self._resolve_fragment_aggregation_queries(
            queries)
        for score_id, aggregator in requests:
            validate_aggregator(
                aggregator, self.score_definitions[score_id].value_type)
        records: Iterable[tuple[int, int, ScoreValue]] = ()
        if self.has_chromosome(chrom):
            column_of = {
                score_id: column for column, score_id in enumerate(score_ids)
            }
            columns = list(enumerate(
                column_of[score_id] for score_id, _ in requests))
            records = (
                (begin, query, values[column])
                for begin, _, values in
                self.get_fragment_scores_starting_in_region(
                    chrom, start, end, scores=list(score_ids))
                for query, column in columns
            )
        return fold_into_bins(
            records, start=start, end=end, bin_size=bin_size,
            aggregators=[aggregator for _, aggregator in requests])
