""":class:`AlleleScore` -- one value per (position, ref, alt) allele.

The kind keyed by a variant rather than a position, in either of two modes
(``substitutions`` and ``alleles``). Its reads widen the shared batch with
the two key columns a position row does not have -- see
:class:`~.records.AlleleRecordArrays` for why that widening is a slice of
the shared type rather than a separate one.
"""

from __future__ import annotations

import copy
import enum
import warnings
from collections.abc import Callable, Generator, Iterator, Sequence
from dataclasses import dataclass
from itertools import chain
from typing import (
    Any,
    ClassVar,
)

import numpy as np

from gain.genomic_resources.genomic_position_table.record import (
    ALT,
    CHROM,
    POS_BEGIN,
    POS_END,
    REF,
    Record,
)
from gain.genomic_resources.repository import (
    GenomicResource,
)
from gain.genomic_resources.resource_errors import (
    backwards_records_error,
)
from gain.genomic_resources.resource_types import (
    PREFERRED_ALLELE_SCORE_TYPE,
    reject_retired_resource,
)
from gain.genomic_resources.score_def import (
    GenomicScoreDef,
    ScoreValue,
)
from gain.genomic_resources.score_filter import (
    ScoreFilter,
    select_records,
)
from gain.utils.stringify import stringify

from ..aggregators import (
    AGGREGATOR_SCHEMA,
    ScoreAggregationQuery,
)
from .aggregation import (
    build_region_aggregators,
    fold_region_segments,
    request_score_ids,
    resolve_aggregation_queries,
    score_def_for,
)
from .base import (
    _SEGMENT_SCORES_DEPRECATION,
    DEFAULT_VALUE_ARRAYS_BATCH_SIZE,
    GenomicScore,
)
from .records import (
    AlleleRecordArrays,
    RecordArrays,
    _key_column_array,
)


@dataclass(frozen=True)
class AlleleAggregate:
    """What one folding read reduced a region to, off a single walk.

    ``values`` is parallel to the QUERIES asked, never keyed by score id:
    one score asked twice with two aggregators is two queries and
    therefore two values, which a mapping keyed by score id would
    silently collapse to one.

    ``allele_keys`` is ``None`` unless the read was asked for them; when
    asked, the distinct keys in first-seen order (D1, D2 of the allele
    folding-read design, gain#1132).  Built off the same walk the values
    were folded from, so nothing a caller can do makes the two disagree
    about which records were seen.
    """

    values: tuple[ScoreValue, ...]
    allele_keys: tuple[str, ...] | None


def allele_key(
    chrom: str, pos: int, ref: str | None, alt: str | None,
    suffix: Sequence[ScoreValue] = (),
) -> str:
    """The allele key: ``chrom:pos[:ref:alt][:v1,v2]``.

    An allele's identity as annotation output spells it, and the ONE
    statement of that spelling: the folding read builds it per record and
    the annotator's exact-match path builds it from the annotatable, and
    the two must not drift.  It lives on the score rather than in the
    annotator because the key is the record's identity, which is
    score-layer knowledge.

    The nucleotides are omitted when EITHER is absent: a table may declare
    only one of the two key columns, and ``1:10:None:C`` would name an
    allele that does not exist.  ``suffix`` is the values of the scores a
    caller asked to append, in the order asked, each rendered as
    :func:`~gain.utils.stringify.stringify` renders it for output and
    joined with ``,``; it is part of the key's identity, so two records at
    one allele that differ in a suffixed score are two keys.
    """
    key = f"{chrom}:{pos}"
    if ref is not None and alt is not None:
        key += f":{ref}:{alt}"
    if suffix:
        key += ":" + ",".join(stringify(value) for value in suffix)
    return key


class _AlleleKeyCollector:
    """A pass-through over records that collects their allele keys.

    The fragment kind's ``_CountingStream`` for this kind's side channel:
    :func:`~.aggregation.fold_region_segments` consumes the stream itself,
    so what is collected cannot be a local the caller appends to -- it
    lives here and is read once the fold has RETURNED.  That class says a
    second kind wanting a per-walk tally should make the fold report it
    rather than relocate the class; this is a private sibling instead,
    because what it collects is not a property of the segments the fold
    sees but of the RECORDS beneath them -- the nucleotides and the
    suffix values -- so it has to sit on the record stream, above
    :meth:`AlleleScore.region_values_from_records`, where the fold cannot
    reach.

    Keys de-duplicate in first-seen order -- ``dict.fromkeys`` semantics
    over the walk -- because repeated ``(chrom, pos, ref, alt)`` keys are
    normal published data, and the order is the file's own genomic order
    (D2 of the design).  Holds one entry per DISTINCT key and nothing per
    record.
    """

    def __init__(self, key_of: Callable[[Record], str]) -> None:
        self._key_of = key_of
        self._keys: dict[str, None] = {}

    def __call__(
        self, records: Iterator[Record],
    ) -> Generator[Record, None, None]:
        """Yield ``records`` unchanged, noting each one's key as it passes."""
        for record in records:
            self._keys[self._key_of(record)] = None
            yield record

    @property
    def keys(self) -> tuple[str, ...]:
        """The distinct keys seen so far, in first-seen order."""
        return tuple(self._keys)


class AlleleScore(GenomicScore):
    """Allele-specific genomic score resource.

    An AlleleScore provides scores that depend on specific alleles at genomic
    positions. Unlike PositionScore, AlleleScore considers both the reference
    and alternative alleles when computing scores. This makes it suitable for
    variant-specific predictions and annotations.

    AlleleScore supports two operational modes:

    1. **SUBSTITUTIONS mode**: Scores are specific to nucleotide substitutions
       (e.g., A>T, C>G). This mode is optimized for single nucleotide variants
       and considers the directionality of the change. Used by resources like
       CADD, which provide substitution-specific scores.

    2. **ALLELES mode**: Scores are associated with specific alleles at
       positions, without considering the reference allele. This mode supports
       insertions, deletions, and more complex variants. The score depends on
       the alternative allele itself rather than the substitution pattern.

    Typical use cases include:
    - Variant pathogenicity scores (e.g., CADD, DANN)
    - Functional impact predictions (e.g., PolyPhen, SIFT scores)
    - Splice site predictions
    - Regulatory variant scores
    - Any metric that depends on specific alleles

    The score data is typically stored in VCF files or tabix-indexed tables
    with reference and alternative allele columns.

    Example:
        >>> from gain.genomic_resources.repository_factory import (
        ...     build_genomic_resource_repository
        ... )
        >>> repo = build_genomic_resource_repository()
        >>> resource = repo.get_resource("cadd_v1_6")
        >>> score = build_score_from_resource(resource)
        >>> with score.open() as score:
        ...     # Fetch scores for a specific variant
        ...     values = score.fetch_allele_scores(
        ...         "chr1", 12345, "A", "T"
        ...     )
        ...     # Iterate over the alleles in a region.  The nucleotides
        ...     # come off the record; the values come off the score.
        ...     for record in score.fetch_records("chr1", 10000, 20000):
        ...         values = score.get_score_values_from_record(
        ...             record, score_defs
        ...         )
        ...         print(f"{record[POS_BEGIN]} "
        ...               f"{record[REF]}>{record[ALT]}: {values}")

    Reducing those values over a region is the resource's own job:
    :meth:`get_allele_scores_in_region_agg` folds a region in one streaming
    walk, one value per :class:`~..aggregators.ScoreAggregationQuery`, with
    the allele keys beside them when asked, and the allele annotator's
    region mode reads through it (``gain.annotation.score_annotator``).

    Attributes:
        resource: The underlying GenomicResource object
        resource_id: Unique identifier for the resource
        config: Configuration dictionary for the score
        table: GenomicPositionTable for data access (typically VCF)
        score_definitions: Dictionary mapping score IDs to their definitions
        mode: Operating mode (SUBSTITUTIONS or ALLELES)

    Key Methods:
        fetch_allele_scores: Get score values for a specific variant
        fetch_allele_records: Get the records of a region, filtered, telling
            a region holding no allele apart from one whose alleles were all
            rejected
        get_allele_scores_in_region_agg: Reduce the alleles of a region to
            one value per query -- and their keys -- in one walk, telling
            the same two answers apart
        fetch_region_segments: Iterate over allele scores in a
            genomic region
        substitutions_mode: Check if operating in SUBSTITUTIONS mode
        alleles_mode: Check if operating in ALLELES mode

    Configuration:
        The resource configuration should specify:
        - table.filename: Path to the data file (usually VCF)
        - table.reference: Column/field containing reference alleles
        - table.alternative: Column/field containing alternative alleles
        - allele_score_mode: Either "substitutions" or "alleles" (optional)
        - scores: List of score definitions with an optional
                 aggregator specification
    """

    # The alleles at a position reduce by ``max``, not ``mean``: a variant's
    # score is the worst of the alleles it could be, not their average.
    DEFAULT_AGGREGATORS: ClassVar[dict[str, str | None]] = {
        "float": "max",
        "int": "max",
        "str": "list",
        "bool": None,
    }

    @classmethod
    def record_weight(
        cls,
        left: int,  # ruff: ignore[unused-class-method-argument]
        right: int,  # ruff: ignore[unused-class-method-argument]
    ) -> int:
        """An allele line counts once.

        Several records share a position -- one per ref/alt pair -- and
        each weighs 1.  Structurally so: :meth:`fetch_region_segments`
        yields ``(pos, pos, values)``, collapsing the record to a point
        however wide an optional ``pos_end`` column reaches, so a span
        weight would not merely be a different choice, it would disagree
        with the per-record read.

        A constant, which is elementwise: the base's
        :meth:`~.base.GenomicScore.record_weights` fills it out to a
        batch's shape.
        """
        return 1

    class Mode(enum.Enum):
        """Allele score mode."""

        SUBSTITUTIONS = 1
        ALLELES = 2

        @staticmethod
        def from_name(name: str) -> AlleleScore.Mode:
            if name == "substitutions":
                return AlleleScore.Mode.SUBSTITUTIONS
            if name == "alleles":
                return AlleleScore.Mode.ALLELES
            raise ValueError(f"unknown allele mode: {name}")

    def __init__(self, resource: GenomicResource):
        # Ahead of the type check below: `np_score` used to be accepted
        # here, so it earns a message naming its replacement rather than
        # the generic "should be of 'allele_score' type" a never-supported
        # type gets (gain#920).
        reject_retired_resource(resource)
        if resource.get_type() != PREFERRED_ALLELE_SCORE_TYPE:
            raise ValueError(
                "The resource provided to AlleleScore should be of "
                f"'{PREFERRED_ALLELE_SCORE_TYPE}' type, "
                f"not a '{resource.get_type()}'")
        super().__init__(resource)
        allele_score_mode = self.config.get("allele_score_mode")
        if allele_score_mode is None:
            # One accepted type, so one default.  This branched on the
            # resource type until 2026.8.5, because `np_score` defaulted to
            # substitutions while `allele_score` defaults to alleles; with
            # `np_score` removed (gain#920) there is nothing left to ask.
            # That difference is why the removal is not a plain rename, and
            # `reject_retired_resource` says so.
            self.mode = AlleleScore.Mode.ALLELES
        else:
            self.mode = AlleleScore.Mode.from_name(allele_score_mode)

    def substitutions_mode(self) -> bool:
        """Return True if the score is in substitutions mode."""
        return self.mode == AlleleScore.Mode.SUBSTITUTIONS

    def alleles_mode(self) -> bool:
        """Return True if the score is in alleles mode."""
        return self.mode == AlleleScore.Mode.ALLELES

    @staticmethod
    def get_schema() -> dict[str, Any]:
        schema = copy.deepcopy(GenomicScore.get_schema())

        schema["allele_score_mode"] = {
            "type": "string",
            "allowed": ["substitutions", "alleles"],
        }
        schema["merge_vcf_scores"] = {
            "type": "boolean",
            "default": False,
        }
        schema["table"]["schema"]["reference"] = {
            "type": "dict", "schema": {
                "index": {"type": "integer"},
                "name": {"type": "string", "excludes": "index"},
                "column_index": {
                    "type": "integer",
                    "excludes": ["index", "name", "column_name"],
                },
                "column_name": {
                    "type": "string",
                    "excludes": ["name", "index", "column_index"],
                },
            },
        }
        schema["table"]["schema"]["alternative"] = {
            "type": "dict", "schema": {
                "index": {"type": "integer"},
                "name": {"type": "string", "excludes": "index"},
                "column_index": {
                    "type": "integer",
                    "excludes": ["index", "name", "column_name"],
                },
                "column_name": {
                    "type": "string",
                    "excludes": ["name", "index", "column_index"],
                },
            },
        }
        schema["table"]["schema"]["variant"] = {
            "type": "dict", "schema": {
                "index": {"type": "integer"},
                "name": {"type": "string", "excludes": "index"},
                "column_index": {
                    "type": "integer",
                    "excludes": ["index", "name", "column_name"],
                },
                "column_name": {
                    "type": "string",
                    "excludes": ["name", "index", "column_index"],
                },
            },
        }
        scores_schema = schema["scores"]["schema"]["schema"]
        scores_schema["aggregator"] = AGGREGATOR_SCHEMA
        return schema

    def validate_records(
        self, records: Iterator[Record],
    ) -> Generator[Record, None, None]:
        """Refuse a record beginning before the one before it.

        Several records legitimately sit at one position -- one per ref/alt
        pair -- so a record at the SAME position as its predecessor is what
        an allele score IS, not an error.  Only a record that moves
        BACKWARDS is one: no ordering of the alleles at a site can produce
        it, so it is a table read out of order.

        The comparison is against RAW spans, and it restarts at every contig:
        where a record sits on one contig says nothing about the next, and
        without the reset every resource whose second contig starts before
        the first one ended would be refused.
        """
        prev_chrom: str | None = None
        prev_pos: int | None = None
        for record in records:
            chrom, pos, _end = self._record_to_begin_end(record)
            if chrom != prev_chrom:
                prev_pos = None
            if prev_pos is not None and pos < prev_pos:
                raise backwards_records_error(
                    self.resource_id, chrom, pos, prev_pos,
                    "an allele score's")
            prev_chrom, prev_pos = chrom, pos
            yield record

    def validate_record_arrays(
        self, batches: Iterator[RecordArrays], chrom: str,
    ) -> Generator[RecordArrays, None, None]:
        """Refuse a record beginning before the one before it, vectorized.

        The same rule as :meth:`validate_records`, over a batch's columns.
        The comparison is strict: several records at ONE position are what an
        allele score is made of, and only a record that moves backwards is a
        table read out of order.

        Only the begins take part, and only the RAW ones -- the ends an
        optional ``pos_end`` column carries are not what an allele record
        means, and clipping would tie the verdict to the region partition.
        A violation straddling a batch boundary is caught on the carried
        begin.
        """
        prev_pos: int | None = None
        for batch in batches:
            pos_begin, _pos_end, _cells = batch
            if pos_begin.size:
                if prev_pos is not None and int(pos_begin[0]) < prev_pos:
                    raise backwards_records_error(
                        self.resource_id, chrom, int(pos_begin[0]), prev_pos,
                        "an allele score's")
                backwards = pos_begin[1:] < pos_begin[:-1]
                if bool(backwards.any()):
                    first = int(np.argmax(backwards))
                    raise backwards_records_error(
                        self.resource_id, chrom, int(pos_begin[first + 1]),
                        int(pos_begin[first]), "an allele score's")
                prev_pos = int(pos_begin[-1])
            yield batch

    def region_values_from_records(
        self,
        records: Iterator[Record],
        chrom: str,
        pos_begin: int | None = None,  # ruff: ignore[unused-method-argument]
        pos_end: int | None = None,  # ruff: ignore[unused-method-argument]
        scores: list[str] | None = None,
    ) -> Generator[
            tuple[int, int, list[ScoreValue]], None, None]:
        """Read each allele record as the point it sits at.

        Several records legitimately share a position -- one per ref/alt pair
        -- so each is yielded separately, and the span is the point
        ``(pos, pos)``: an allele's value stands for its ref/alt pair, not for
        the bases an optional ``pos_end`` column may cover.  A caller that
        needs the nucleotides themselves reads ``record[REF]`` /
        ``record[ALT]`` off :meth:`~.base.GenomicScore.fetch_records`.

        The point stands wherever it falls relative to the queried window:
        like every segment read, this holds no window opinion, and what a
        point outside the window means is the caller's question (ADR 0008).
        ``pos_begin`` and ``pos_end`` are still taken, because they are what
        :meth:`GenomicScore.region_values_from_records()
        <.base.GenomicScore.region_values_from_records>` means by a region
        and this is one kind's answer to it.

        Nothing is checked either: every record is read, whatever its
        position is next to the one before it.  The rule an allele score's
        records hold to lives in :meth:`validate_records`, which the
        statistics scan composes over the stream it reads and no reader
        composes at all (ADR 0008).
        """
        score_defs = self._region_read_defs(chrom, scores)
        return self._allele_point_values(records, score_defs)

    def fetch_region_segment_scores(
        self,
        chrom: str,
        pos_begin: int | None = None,
        pos_end: int | None = None,
        scores: list[str] | None = None,
    ) -> Generator[
            tuple[int, int, list[ScoreValue]], None, None]:
        """Yield ``(pos, pos, values)`` per allele record of the region.

        .. deprecated::
            Use :meth:`~.base.GenomicScore.fetch_region_segments` -- for this
            kind the very same read.  An allele read collapses each record to
            a point and
            holds no window opinion, so unlike the base method there is no
            clip to preserve here; the two names differ only in the
            warning.  Removal is tracked as gain#844.
        """
        warnings.warn(
            _SEGMENT_SCORES_DEPRECATION
            + "For an allele score the two are the same read.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.fetch_region_segments(chrom, pos_begin, pos_end, scores)

    def _allele_point_values(
        self,
        records: Iterator[Record],
        score_defs: list[GenomicScoreDef],
    ) -> Generator[
            tuple[int, int, list[ScoreValue]], None, None]:
        """Stream one point per allele record, for a checked request.

        The point is POS_BEGIN, but POS_END is read too, to refuse a record
        whose end precedes its begin: a different rule from anything the scan
        validates, one no reader can proceed past, and one no other allele
        read states -- ``validate_records`` states the scan's rules, and the
        single-allele read matches on ref/alt without looking at the span.

        Reads its slots directly and extracts inline, for the reasons
        :meth:`GenomicScore._score_segments` gives.
        """
        extract = self._extract_value
        for record in records:
            pos = record[POS_BEGIN]
            if record[POS_END] < pos:
                raise self._inverted_span_error(record)
            yield pos, pos, [
                extract(record, score_def) for score_def in score_defs]

    def _fetch_allele_record(
        self, chrom: str, pos: int, ref: str, alt: str,
        *,
        score_filter: ScoreFilter | None = None,
    ) -> Record | None:
        """Return the record matching this allele exactly, or None.

        Exact on all four of chrom, position, ref and alt: several records
        share a position, one per ref/alt pair, so the nucleotides are what
        pick between them.

        ``score_filter`` -- from :meth:`GenomicScore.compile_filter` -- is
        applied to the matched record, and an allele it rejects reads as
        absent: the caller asked for an allele it is not to have, which is
        the same answer as an allele this resource does not carry.  The
        filter runs on the RECORD, so a rejected allele costs no value
        extraction.

        Internal to the allele read: :meth:`fetch_allele_scores` is the
        per-allele read and hands back values, which is what a caller
        asking about one allele wants.  A caller that wants the records of a
        whole region asks :meth:`fetch_allele_records`, or
        :meth:`GenomicScore.fetch_records` to stream them.
        """
        for record in self.fetch_records(
                chrom, pos, pos, score_filter=score_filter):
            if record[REF] == ref and record[ALT] == alt:
                return record
        return None

    def fetch_allele_scores(
        self, chrom: str, position: int,
        reference: str, alternative: str,
        scores: list[str] | None = None,
        *,
        score_filter: ScoreFilter | None = None,
    ) -> dict[str, ScoreValue] | None:
        """Fetch score values at specified genomic position and nucleotide.

        ``score_filter`` selects whether this allele is reported at all; an
        allele it rejects reads as absent, exactly as an unmatched one does.
        """
        if chrom not in self.get_all_chromosomes():
            raise ValueError(
                f"{chrom} is not among the available chromosomes for "
                f"NP Score resource {self.resource_id}")

        requested_scores = scores or self.get_all_scores()
        score_defs = self._resolve_score_defs(requested_scores)

        selected = self._fetch_allele_record(
            chrom, position, reference, alternative,
            score_filter=score_filter)
        if selected is None:
            return None
        return dict(zip(
            requested_scores,
            self.get_score_values_from_record(selected, score_defs),
            strict=True))

    def fetch_allele_records(
        self, chrom: str, pos_begin: int | None, pos_end: int | None,
        *,
        score_filter: ScoreFilter | None = None,
    ) -> list[Record] | None:
        """Return the allele records overlapping a region, or ``None``.

        ``None`` means no record overlaps the region at all -- absent data.
        A list means records were there, and holds the ones ``score_filter``
        accepted, which may be none of them: ``[]`` is an empty selection.
        The two are different answers and a caller may well report them
        differently, which is the whole reason this read exists rather than
        :meth:`GenomicScore.fetch_records()
        <.base.GenomicScore.fetch_records>` serving the same purpose -- an
        iterator makes both an empty stream.

        :meth:`FragmentScore.fetch_fragment_scores()
        <.fragment.FragmentScore.fetch_fragment_scores>` deliberately has no
        ``None``, and the difference is in the data rather than in taste: a
        region is spanned by fragments as a matter of course, so "no
        fragment covers it" is a count of zero.  Allele records sit at
        points, most of a genome carries none, and a region holding no
        allele is the same absent data that :meth:`fetch_allele_scores`
        already answers ``None`` for.

        ``score_filter`` -- from :meth:`GenomicScore.compile_filter()
        <.base.GenomicScore.compile_filter>` -- is
        applied inside the read, so a rejected record costs no value
        extraction and the ownership check covers this path too.

        Records, not values: a caller wants the nucleotides and the position
        as well as the scores, reads several scores off one record, and may
        read scores this method was never told about.  Handing back dicts
        would settle all three for it, and wrongly.  Values come off a
        record through
        :meth:`~.base.GenomicScore.get_score_value_from_record`.

        A contig the resource does not have is refused, as the other allele
        reads refuse it, and refused from the call: answering ``None`` would
        make a caller's typo indistinguishable from real absent data.  This
        read materialises, so there is no generator body to defer the
        refusal into -- unlike :meth:`GenomicScore.fetch_records()
        <.base.GenomicScore.fetch_records>`, which
        reports it from the first record read.

        Materialising is what the ``list``/``None`` answer costs: a caller
        reading a region far larger than it can hold wants the streaming
        read instead -- or, to reduce the region rather than hold it,
        :meth:`get_allele_scores_in_region_agg`, which shares this read's
        two answers and its peek.  Records the filter rejects are never
        held, though -- only the accepted ones accumulate.
        """
        selected = self._selected_allele_records(
            chrom, pos_begin, pos_end, score_filter)
        if selected is None:
            return None
        return list(selected)

    def _selected_allele_records(
        self, chrom: str, pos_begin: int | None, pos_end: int | None,
        score_filter: ScoreFilter | None,
    ) -> Iterator[Record] | None:
        """Records of a region the filter keeps; ``None`` when none overlap.

        The absence peek :meth:`fetch_allele_records` and
        :meth:`get_allele_scores_in_region_agg` share, stated once so the
        two reads cannot come to disagree about what ``None`` means or
        when a filter is checked.  ``None`` is a region no record overlaps,
        judged BEFORE the filter; an iterator -- possibly empty -- is a
        region that held records, of which these are the ones the filter
        accepted.

        The ownership check runs first of all, ahead of the peek: a foreign
        filter is a programming error and must not be refused on a
        populated region and accepted on an empty one.  ``select`` checks
        it again, at the cost of one identity comparison per read.

        A contig the resource does not have is refused from the call, as
        every other allele read refuses it: answering ``None`` would make
        a caller's typo indistinguishable from real absent data.
        """
        if chrom not in self.get_all_chromosomes():
            raise ValueError(
                f"{chrom} is not among the available chromosomes for "
                f"allele score resource {self.resource_id}")
        if score_filter is not None:
            score_filter.require_owner(self)

        overlapping = self.fetch_records(chrom, pos_begin, pos_end)
        first = next(overlapping, None)
        if first is None:
            return None
        return select_records(
            self, chain([first], overlapping), score_filter)

    def resolve_aggregation_queries(
        self, queries: Sequence[ScoreAggregationQuery] | None,
    ) -> list[tuple[str, str]]:
        """Resolve each query to its ``(score_id, aggregator NAME)`` pair.

        Public, and stopping at the NAME, for the reason
        :meth:`PositionScore.resolve_aggregation_queries()
        <.position.PositionScore.resolve_aggregation_queries>` gives:
        asking whether a query list is answerable is a question a caller
        may have without wanting to read.  ``AlleleScoreAnnotator`` asks
        it once when the pipeline loads, so an attribute naming no
        aggregator for a ``bool`` score is refused there rather than on
        the first region that reaches it (D6 of the allele folding-read
        design).  Building the accumulators is the READ's business, per
        call.

        Kind-neutral queries, so the shared
        :func:`~.aggregation.resolve_aggregation_queries` answers it
        whole; ``None`` means every score with its own default.
        """
        return resolve_aggregation_queries(
            queries,
            score_definitions=self.score_definitions,
            all_scores=self.get_all_scores(),
            resource_id=self.resource_id)

    def resolve_allele_key_scores(
        self, allele_keys: Sequence[str],
    ) -> list[GenomicScoreDef]:
        """The definitions of the scores an allele-key request suffixes.

        The other half of what :meth:`get_allele_scores_in_region_agg`
        checks on the call, made askable without reading: an unknown id
        is refused with the valid names listed, which is how the
        annotator refuses a bad ``include_attributes`` as the pipeline
        loads rather than per record.
        """
        return [
            score_def_for(
                score_id,
                score_definitions=self.score_definitions,
                resource_id=self.resource_id)
            for score_id in allele_keys
        ]

    def get_allele_scores_in_region_agg(
        self, chrom: str, start: int, end: int,
        *,
        queries: Sequence[ScoreAggregationQuery] | None = None,
        allele_keys: Sequence[str] | None = None,
        score_filter: ScoreFilter | None = None,
    ) -> AlleleAggregate | None:
        """Reduce the alleles in a region to one value per query, or ``None``.

        The kind's folding read (gain#1132): what
        :meth:`fetch_allele_records` would hand back, already reduced, in
        ONE walk that holds no record.  ``queries`` of ``None`` means every
        score the resource defines, each with its own default aggregator;
        a query's own aggregator wins over the default.  An allele line
        is weighed by :meth:`record_weight`, which counts it once.

        ``None`` is a region no record overlaps -- absent data -- exactly
        the answer :meth:`fetch_allele_records` gives for it, and judged
        by the same peek, before ``score_filter`` is applied.  An
        :class:`AlleleAggregate` whose fold saw nothing is different: the
        records were there and the filter rejected every one, so each
        aggregator answers for an empty selection (``list`` gives ``[]``,
        ``max`` gives ``None``, ...).  That asymmetry with the fragment
        kind's folding read is a property of the data, and ADR 0017's
        Consequences say why.

        ``score_filter`` composes over the filtered record read, as the
        fragment kind's does -- ownership is checked first of all, so a
        foreign filter is refused on an empty region as loudly as on a
        populated one.

        ``allele_keys`` of ``None`` -- the default -- builds no keys, so a
        caller that wants none pays nothing per record for them.  A
        sequence, possibly empty, asks for the keys and names the score
        ids to suffix each with: ``()`` is the bare ``chrom:pos:ref:alt``.
        :func:`allele_key` states the format; the keys come back distinct
        and in first-seen order, off the same walk the values were folded
        from.

        The REQUEST is checked when this is called: an unknown score id --
        in a query or in ``allele_keys`` -- a query with no aggregator to
        resolve to, an unknown contig and a foreign filter are all refused
        before a record is read.  Aggregators are built FRESH per call,
        never held on the score, for the reason
        :func:`~.aggregation.build_region_aggregators` gives.
        """
        requests = self.resolve_aggregation_queries(queries)
        aggregators = build_region_aggregators(
            requests, resource_id=self.resource_id)
        collector = (
            None if allele_keys is None
            else self._allele_key_collector(allele_keys))
        records = self._selected_allele_records(
            chrom, start, end, score_filter)
        if records is None:
            return None
        if collector is not None:
            records = collector(records)
        score_ids = request_score_ids(requests)
        values = fold_region_segments(
            self.region_values_from_records(
                records, chrom, start, end, score_ids),
            aggregators, requests,
            score_ids=score_ids, weigh=self.record_weight)
        return AlleleAggregate(
            tuple(values),
            None if collector is None else collector.keys)

    def _allele_key_collector(
        self, suffix_scores: Sequence[str],
    ) -> _AlleleKeyCollector:
        """A collector building each record's key with these scores suffixed.

        The suffix ids are resolved here, up front and by name, so an
        unknown one is refused from the call with the valid names listed
        -- earlier than the per-record ``KeyError`` the annotator used to
        raise, and on an empty region too.
        """
        suffix_defs = self.resolve_allele_key_scores(suffix_scores)
        extract = self._extract_value

        def key_of(record: Record) -> str:
            return allele_key(
                record[CHROM], record[POS_BEGIN], record[REF], record[ALT],
                [extract(record, score_def) for score_def in suffix_defs])

        return _AlleleKeyCollector(key_of)

    def supports_region_allele_arrays(self, scores: list[str]) -> bool:
        """Whether :meth:`fetch_region_allele_arrays` will serve these scores.

        :meth:`GenomicScore.supports_region_value_arrays()
        <.base.GenomicScore.supports_region_value_arrays>` -- the backend and
        the score value types -- plus the one condition that is this read's
        alone: the table must declare at least one of the two key columns,
        or there is nothing for it to carry that the shared read does not
        already give.

        The columns are configured **independently**, and one of them is
        enough.  A table declaring only ``alternative`` is served, with the
        missing side yielded as the ``None`` the record carries for it; that
        is what keeps this read and :meth:`GenomicScore.fetch_records()
        <.base.GenomicScore.fetch_records>` the
        same answer rather than two.  A bigWig-backed score is turned away
        here without being named: it has no such columns to declare.

        Answerable on an UNOPENED score, as its counterpart is -- and, in one
        case, **conservative** there rather than exact.  A table's key columns
        are resolved when it opens (``_set_core_column_keys``), from the
        config and, failing that, from the header.  So this asks the same two
        questions in the same order, using whichever of them can be answered
        yet: the declaration always, and the header when the table already
        has one (``header_mode: list`` names it in the config, and an opened
        table has read it).

        That leaves exactly one gap: a ``header_mode: file`` table that names
        its key columns nowhere but inside its own data file answers ``False``
        until it is opened and ``True`` after.  The asymmetry is the file's,
        not this method's -- a header cannot be known without reading it --
        and it errs the safe way, because a caller told ``False`` reads
        per-record and gets the same rows.
        """
        return self._allele_arrays_refusal_reason(scores) is None

    def _allele_arrays_refusal_reason(self, scores: list[str]) -> str | None:
        """Why this read is refused for ``scores``, or ``None`` if it is not.

        The predicate and the message it owes a caller who ignored it, from
        ONE evaluation.  Asking :meth:`supports_region_allele_arrays` and
        then re-deriving which of its two rules had said no would be the
        same question answered twice, and the pair could come to disagree
        about which one it was.
        """
        if not self.supports_region_value_arrays(scores):
            return self._value_arrays_refusal_reason()
        table = self.table
        if table.ref_key is not None or table.alt_key is not None:
            # Resolved -- the table is open, and these are authoritative.
            return None
        if any(
                table.would_resolve_column(column)
                for column in (table.REF, table.ALT)):
            return None
        return (
            "its table declares neither a 'reference' nor an "
            "'alternative' column")

    def fetch_region_allele_arrays(
        self,
        chrom: str,
        pos_begin: int | None,
        pos_end: int | None,
        scores: list[str],
        *,
        batch_size: int = DEFAULT_VALUE_ARRAYS_BATCH_SIZE,
    ) -> Generator[AlleleRecordArrays, None, None]:
        """Fetch a region as column arrays, nucleotides included.

        :meth:`GenomicScore.fetch_region_value_arrays()
        <.base.GenomicScore.fetch_region_value_arrays>` widened by the two
        columns an allele row has and a position row does not, for a caller
        scanning a whole region for allele *content* rather than values --
        the allele statistics, above all.  Each batch is that method's
        ``(pos_begin, pos_end, {score_id: values})`` followed by the
        ``reference`` and ``alternative`` arrays, as
        :class:`~.records.AlleleRecordArrays`.

        **The nucleotides are RAW; the scores beside them are parsed.**  That
        asymmetry is deliberate and is the whole contract.  A score column
        goes through its definition's column parse, so an NA sentinel arrives
        as that score's non-value; these two columns go through nothing at
        all.  Whatever the row held is what the array holds -- no
        upper-casing, no stripping, no sentinel handling -- because
        :func:`~.genomic_position_table.record.build_tabular_parser` reads
        them equally verbatim, and a
        consumer reading a region through this method and a region through
        :meth:`GenomicScore.fetch_records()
        <.base.GenomicScore.fetch_records>` must be handed the same strings
        rather than two dialects of them.  Whoever wants them normalised
        normalises them, once, where the meaning of the normalisation is
        known.

        Refused, rather than emulated, for a score this facade cannot serve
        it for -- ask :meth:`supports_region_allele_arrays` first.  The
        guards run when this method is CALLED, not on the first ``next()``,
        which is why the streaming half lives in ``_allele_array_batches``
        rather than a ``yield`` here.
        """
        reason = self._allele_arrays_refusal_reason(scores)
        if reason is not None:
            raise TypeError(
                f"genomic score <{self.resource_id}> does not serve "
                f"fetch_region_allele_arrays for {sorted(scores)}: {reason}. "
                f"Ask supports_region_allele_arrays(scores) before calling.")
        self._require_open_and_known_chrom(chrom)
        return self._allele_array_batches(
            chrom, pos_begin, pos_end, scores, batch_size)

    def _allele_array_batches(
        self,
        chrom: str,
        pos_begin: int | None,
        pos_end: int | None,
        scores: list[str],
        batch_size: int,
    ) -> Generator[AlleleRecordArrays, None, None]:
        """Stream the batches for an already-validated request.

        The shared column read plus this kind's two extra columns, which it
        asks for by index and reads out of the raw cells -- so the positions
        and the parse stay stated once, in
        :meth:`GenomicScore._parsed_column_batches`.
        """
        ref_key = self.table.ref_key
        alt_key = self.table.alt_key
        key_columns = frozenset(
            key for key in (ref_key, alt_key) if key is not None)

        for begin, end, values, cells in self._parsed_column_batches(
                self._score_column_indexes(scores),
                (chrom, pos_begin, pos_end), batch_size,
                extra_columns=key_columns):
            yield AlleleRecordArrays(
                begin, end, values,
                _key_column_array(cells, ref_key, len(begin)),
                _key_column_array(cells, alt_key, len(begin)),
            )
