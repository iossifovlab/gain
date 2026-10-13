""":class:`AlleleScore` -- one value per (position, ref, alt) allele.

The kind keyed by a variant rather than a position, in either of two modes
(``substitutions`` and ``alleles``). Its reads widen the shared batch with
the two key columns a position row does not have -- see
:class:`~.records.AlleleRecordArrays` for why that widening is a slice of
the shared type rather than a separate one.
"""

# pylint: disable=too-many-lines
# The numpy docstrings of the public reads (gain#1911) put the module over
# the cap.  The reads are one class and share one walk, so they stay together.

from __future__ import annotations

import copy
import enum
from collections.abc import Callable, Generator, Iterator, Sequence
from dataclasses import dataclass
from itertools import chain
from typing import (
    Any,
    ClassVar,
    NamedTuple,
)

from gain import logging
from gain.genomic_resources import resource_types
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
from gain.genomic_resources.resource_errors import inverted_span_error
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
    Aggregator,
    ScoreAggregationQuery,
)
from .aggregation import (
    build_region_aggregators,
    fold_region_segments,
    request_score_ids,
    resolve_aggregation_queries,
)
from .base import (
    DEFAULT_VALUE_ARRAYS_BATCH_SIZE,
    GenomicScore,
)
from .records import (
    AlleleRecordArrays,
    _key_column_array,
)

logger = logging.getLogger(__name__)


# ``values`` is parallel to the queries and is never keyed by score id:
# one score asked twice with two aggregators is two queries and therefore
# two values, which a mapping keyed by score id would silently collapse to
# one.  ``allele_keys`` follows D1 and D2 of the allele folding-read design
# (gain#1132).  The keys are built off the same walk the values were folded
# from, so nothing a caller can do makes the two disagree about which
# records were seen.
@dataclass(frozen=True)
class AlleleAggregate:
    """The answer of a folding read: one value per query, and the allele keys.

    Attributes:
        values (tuple[ScoreValue, ...]): One value for each query of the
            read, in the order asked. A query is a score with an
            aggregator, so one score asked with two aggregators gives two
            values.
        allele_keys (tuple[str, ...] | None): The distinct allele keys of
            the rows that were folded, in the order the rows were first
            seen. The value is ``None`` when the read was not asked for
            keys. See :func:`allele_key` for the format.
    """

    values: tuple[ScoreValue, ...]
    allele_keys: tuple[str, ...] | None


class AlleleEntry(NamedTuple):
    """One allele row of a region, as the unreduced region read yields it.

    Attributes:
        pos (int): The position of the row.
        ref (str | None): The reference nucleotides of the row, as the
            table holds them. The value is ``None`` when the table has no
            reference column.
        alt (str | None): The alternative nucleotides of the row, as the
            table holds them. The value is ``None`` when the table has no
            alternative column.
        values (tuple[ScoreValue, ...]): The values of the scores that the
            read was asked for, in the order asked.
    """

    pos: int
    ref: str | None
    alt: str | None
    values: tuple[ScoreValue, ...]


# The one statement of the key's spelling: the folding read builds the key
# per record and the annotator's exact-match path builds it from the
# annotatable, and the two must not drift.  It lives next to the score and
# not in the annotator because the key is the identity of the record, which
# is knowledge of the score layer.  The nucleotides are omitted when either
# is absent, because ``1:10:None:C`` would name an allele that does not
# exist.
def allele_key(
    chrom: str, pos: int, ref: str | None, alt: str | None,
    suffix: Sequence[ScoreValue] = (),
) -> str:
    """Build the allele key, ``chrom:pos[:ref:alt][:v1,v2]``.

    The key is the identity of an allele as annotation output spells it.

    Parameters
    ----------
    chrom : str
        The chromosome name.
    pos : int
        The position of the allele.
    ref : str or None
        The reference nucleotides. The key has no ``:ref:alt`` part when
        this or ``alt`` is ``None``.
    alt : str or None
        The alternative nucleotides.
    suffix : Sequence[ScoreValue], optional
        The values of the scores to append, in the order asked. Each value
        is rendered as :func:`~gain.utils.stringify.stringify` renders it
        for output, and the values are joined with ``,``. The suffix is
        part of the identity: two rows of one allele that differ in a
        suffixed score give two keys. The default is an empty sequence,
        which appends nothing.

    Returns
    -------
    str
        The key, for example ``1:10:A:G`` or ``1:10:A:G:0.5,2``.
    """
    key = f"{chrom}:{pos}"
    if ref is not None and alt is not None:
        key += f":{ref}:{alt}"
    if suffix:
        key += ":" + ",".join(stringify(value) for value in suffix)
    return key


def _allele_pos(record: Record) -> int:
    """The position an allele record stands at, refusing an inverted span.

    ``POS_BEGIN``, with ``POS_END`` read only to refuse a record whose end
    precedes its begin -- the one rule every allele read applies to a
    record it reads (see :meth:`AlleleScore._score_segments`).
    """
    pos: int = record[POS_BEGIN]
    if record[POS_END] < pos:
        raise inverted_span_error(
            record[CHROM], pos, record[POS_END], record[REF], record[ALT])
    return pos


class AlleleMultiplicityError(ValueError):
    """The rows of an allele score disagree with its ``allele_multiplicity``.

    The message is ``detail`` with a prefix: the resource id and, when the
    refusal concerns one allele, the allele spelled by :func:`allele_key`::

        AlleleMultiplicityError(
            "scores/x", "holds 2 rows but declares allele_multiplicity: one",
            allele=("1", 10, "A", "G"))
        # allele score 'scores/x', allele 1:10:A:G: holds 2 rows ...

    Attributes:
        resource_id (str): The id of the resource that the error is about.
        allele (tuple[str, int, str | None, str | None] | None): The
            allele as ``(chrom, pos, ref, alt)``. The value is ``None``
            when the refusal does not concern one allele.
        detail (str): The message without the prefix.
    """

    def __init__(
        self,
        resource_id: str,
        detail: str,
        *,
        allele: tuple[str, int, str | None, str | None] | None = None,
    ) -> None:
        self.resource_id = resource_id
        self.allele = allele
        self.detail = detail
        where = f"allele score {resource_id!r}"
        if allele is not None:
            where += f", allele {allele_key(*allele)}"
        super().__init__(f"{where}: {detail}")


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
    :meth:`AlleleScore._score_segments`, where the fold cannot
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
        ...     # Fetch the scores of a specific variant's row
        ...     values = score.get_allele_scores_for_allele(
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
    region mode reads through it (``gain.annotation.allele_score_annotator``).

    No read consults the mode: an exact-match read answers an indel's own
    line in either mode.  The mode is for the caller to route on, and the
    allele score annotator does -- on a ``substitutions`` resource it
    folds any allele other than a substitution over its span instead of
    matching it.

    Attributes:
        resource: The underlying GenomicResource object
        resource_id: Unique identifier for the resource
        config: Configuration dictionary for the score
        table: GenomicPositionTable for data access (typically VCF)
        score_definitions: Dictionary mapping score IDs to their definitions
        mode: Operating mode (SUBSTITUTIONS or ALLELES)

    Key Methods:
        get_allele_scores_for_allele: Get the values of one allele's only
        row, on a resource declaring one row per allele
        get_allele_score_for_allele: The same for one score
        get_allele_scores_in_region_agg: Reduce the alleles of a region to
        one value per query -- and their keys -- in one walk, telling
        the same two answers apart
        get_allele_scores_for_allele_agg: Reduce the rows of one allele to
        one value per query, telling an allele no row holds apart from one
        whose rows were all rejected
        get_allele_score_for_allele_agg: The same for one score
        get_allele_scores_for_allele_rows: Get the values of every row of
        one allele, unreduced
        get_allele_score_for_allele_rows: The same for one score
        get_allele_scores_in_region_rows: Stream an AlleleEntry per row of
        a region, unreduced, off the walk the folding read reduces
        get_allele_score_in_region_rows: The same for one score, as
        ``(pos, ref, alt, value)``
        fetch_region_segments_scores: Iterate over allele scores in a
        genomic region
        substitutions_mode: Check if operating in SUBSTITUTIONS mode
        alleles_mode: Check if operating in ALLELES mode

    Configuration:
        The resource configuration should specify:

        - table.filename: Path to the data file (usually VCF)
        - table.reference: Column/field containing reference alleles
        - table.alternative: Column/field containing alternative alleles
        - allele_score_mode: Either "substitutions" or "alleles" (optional)
        - allele_multiplicity: Either "one" (the default) or "many" -- how
          many table rows one allele key may have (optional)
        - scores: List of score definitions with an optional
          aggregator specification
    """

    #: The unit that the histograms of this kind count: ``"alleles"``.
    HISTOGRAM_COUNT_UNIT: ClassVar[str] = "alleles"

    # The alleles at a position reduce by ``max``, not ``mean``: a variant's
    # score is the worst of the alleles it could be, not their average.
    #: The aggregator that a query uses when it names none, by the value
    #: type of the score: ``max`` for numbers, ``list`` for strings and none
    #: for booleans.
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
        """Return the weight of an allele row, which is always 1.

        This is a hook for the folding reads. Do not call it.

        :meta private:

        Parameters
        ----------
        left : int
            The first position of the row. The value is not used.
        right : int
            The last position of the row. The value is not used.

        Returns
        -------
        int
            ``1``.
        """
        # Several records share a position, one per ref/alt pair, and each
        # weighs 1.  The weight is structural: fetch_region_segments_scores
        # yields (pos, pos, values), which collapses the record to a point
        # however wide an optional pos_end column reaches.  A span weight
        # would not only be a different choice, it would disagree with the
        # per-record read.  The weight is a constant, which is elementwise:
        # GenomicScore.record_weights fills it out to the shape of a batch.
        return 1

    class Mode(enum.Enum):
        """The allele score mode, as the resource declares it.

        ``SUBSTITUTIONS`` is the mode ``substitutions`` and ``ALLELES`` is
        the mode ``alleles``. No read consults the mode. The allele score
        annotator uses it to route an allele.
        """

        SUBSTITUTIONS = 1
        ALLELES = 2

        @staticmethod
        def from_name(name: str) -> AlleleScore.Mode:
            """Return the mode that has the given configuration name.

            Parameters
            ----------
            name : str
                ``"substitutions"`` or ``"alleles"``.

            Returns
            -------
            AlleleScore.Mode
                The mode of the name.

            Raises
            ------
            ValueError
                If the name is neither of the two.
            """
            if name == "substitutions":
                return AlleleScore.Mode.SUBSTITUTIONS
            if name == "alleles":
                return AlleleScore.Mode.ALLELES
            raise ValueError(f"unknown allele mode: {name}")

    class Multiplicity(enum.Enum):
        """How many table rows one allele may have.

        The resource declares it with the top-level key
        ``allele_multiplicity``. ``ONE`` is the default. ``MANY`` means
        that several rows may hold one ``(chrom, pos, ref, alt)``, for
        example one row for each transcript.
        """
        # Rows, not values: several records sharing one allele, not a
        # multi-valued field inside one record (gain#1749, A3).

        ONE = "one"
        MANY = "many"

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
        self._multiplicity = AlleleScore.Multiplicity(
            self.config.get("allele_multiplicity", "one"))

    @property
    def multiplicity(self) -> AlleleScore.Multiplicity:
        """The number of rows that one allele may have in the table.

        Only the bare reads consult it, such as
        :meth:`get_allele_scores_for_allele`.
        """
        # The allele statistics build reads it too: on a ``one`` resource it
        # reports the first allele key that several rows hold.  The _rows
        # and _agg reads do not consult it.
        return self._multiplicity

    def substitutions_mode(self) -> bool:
        """Return whether the mode of the resource is ``substitutions``."""
        return self.mode == AlleleScore.Mode.SUBSTITUTIONS

    def alleles_mode(self) -> bool:
        """Return whether the mode of the resource is ``alleles``."""
        return self.mode == AlleleScore.Mode.ALLELES

    @staticmethod
    def get_schema() -> dict[str, Any]:
        """Return the schema of the resource configuration.

        The schema is the :class:`GenomicScore` schema plus the keys
        ``allele_score_mode``, ``allele_multiplicity`` and
        ``merge_vcf_scores``, and the ``reference``, ``alternative`` and
        ``variant`` column definitions of the table.

        Returns
        -------
        dict[str, Any]
            A new schema in the Cerberus format. The caller may change it.
        """
        schema = copy.deepcopy(GenomicScore.get_schema())

        schema["allele_score_mode"] = {
            "type": "string",
            "allowed": ["substitutions", "alleles"],
        }
        # A ``regex`` rather than ``allowed``: cerberus words an
        # ``allowed`` refusal as "unallowed value X" and never says what
        # would have been allowed, whereas the regex is quoted in its
        # refusal, so the logged error names every valid value.  ``\Z``,
        # not ``$``, which would also admit a trailing newline.  Not a
        # ``check_with`` callable either -- the validator cache relies on
        # no resource schema carrying one.
        schema["allele_multiplicity"] = {
            "type": "string",
            "regex": "^(" + "|".join(
                multiplicity.value
                for multiplicity in AlleleScore.Multiplicity) + r")\Z",
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

    def _score_segments(
        self,
        records: Iterator[Record],
        score_defs: list[GenomicScoreDef],
    ) -> Generator[
            tuple[int, int, list[ScoreValue]], None, None]:
        """Stream one point per allele record, for a resolved request.

        This kind's answer to the per-kind hook
        :meth:`GenomicScore._score_segments
        <.base.GenomicScore._score_segments>` -- read each allele record as
        the point it sits at.  Several records legitimately share a position
        -- one per ref/alt pair -- so each is yielded separately, and the
        span is the point ``(pos, pos)``: an allele's value stands for its
        ref/alt pair, not for the bases an optional ``pos_end`` column may
        cover.  A caller that needs the nucleotides themselves reads
        ``record[REF]`` / ``record[ALT]`` off
        :meth:`~.base.GenomicScore.fetch_records`.

        The point stands wherever it falls relative to the queried window:
        like every segment read, this holds no window opinion, and what a
        point outside the window means is the caller's question (ADR 0008).
        No region reaches here at all -- what this kind does to a record
        does not depend on what was asked for.

        The point is POS_BEGIN, but POS_END is read too, to refuse a record
        whose end precedes its begin: a different rule from anything the scan
        validates, one no reader can proceed past, and one no other allele
        read states -- the scan's rules are the record-validation registry's,
        and the single-allele read matches on ref/alt without looking at the
        span.  Nothing else is checked: the rule an allele score's records
        hold to lives in
        :func:`~gain.genomic_resources.statistics.record_validation.validate_records`,
        which the statistics scan composes over the stream it reads and no
        reader composes at all (ADR 0008, ADR 0027).

        The REQUEST is not resolved here.  This kind used to override
        :meth:`GenomicScore.values_from_records()
        <.base.GenomicScore.values_from_records>` whole, and so
        carried a second copy of the resolution that method performs; since
        gain#1282 the resolution is the base's alone and a kind states only
        its reading.  This method carried the name
        ``_allele_point_values`` until then, when overriding the hook made
        the second name redundant.

        Reads its slots directly and extracts inline, for the reasons the
        base's body gives.
        """
        extract = self._extract_value
        for record in records:
            pos = _allele_pos(record)
            yield pos, pos, [
                extract(record, score_def) for score_def in score_defs]

    def _selected_allele_records(
        self, chrom: str, pos_begin: int | None, pos_end: int | None,
        score_filter: ScoreFilter | None,
    ) -> Iterator[Record] | None:
        """Records of a region the filter keeps; ``None`` when none overlap.

        :meth:`_check_allele_region_request` then
        :meth:`_walk_allele_records`: the two halves every allele region read
        is built on, so they cannot come to disagree about the contig
        refusal, filter ownership, which records the filter keeps or their
        order.  :meth:`get_allele_scores_in_region_agg` takes both halves
        through this method; the unreduced rows reads call them separately, to
        check on the call and walk lazily, and answer ``None`` as an empty
        stream.  A step every region read must share belongs in one of the
        halves, not here.

        ``None`` is a region no record overlaps, judged BEFORE the filter;
        an iterator -- possibly empty -- is a region that held records, of
        which these are the ones the filter accepted.

        The ownership check runs first of all, ahead of the peek: a foreign
        filter is a programming error and must not be refused on a
        populated region and accepted on an empty one.  ``select`` checks
        it again, at the cost of one identity comparison per read.

        A contig the resource does not have is refused from the call, as
        every other allele read refuses it: answering ``None`` would make
        a caller's typo indistinguishable from real absent data.
        """
        self._check_allele_region_request(chrom, score_filter)
        return self._walk_allele_records(
            chrom, pos_begin, pos_end, score_filter)

    def _check_allele_region_request(
        self, chrom: str, score_filter: ScoreFilter | None,
    ) -> None:
        """Refuse an unknown contig or a foreign filter; read nothing.

        The checks of :meth:`_selected_allele_records`, separable so a
        lazy read can run them on the call and defer the walk.
        """
        if not self.has_chromosome(chrom):
            raise ValueError(
                f"{chrom} is not among the available chromosomes for "
                f"allele score resource {self.resource_id}")
        if score_filter is not None:
            score_filter.require_owner(self)

    def _walk_allele_records(
        self, chrom: str, pos_begin: int | None, pos_end: int | None,
        score_filter: ScoreFilter | None,
    ) -> Iterator[Record] | None:
        """The walk of :meth:`_selected_allele_records`, request unchecked.

        Starts the read: the peek takes the first record off the table.
        """
        overlapping = self.fetch_records(chrom, pos_begin, pos_end)
        first = next(overlapping, None)
        if first is None:
            return None
        return select_records(
            self, chain([first], overlapping), score_filter)

    def resolve_aggregation_queries(
        self, queries: Sequence[ScoreAggregationQuery] | None,
    ) -> list[tuple[str, str]]:
        """Resolve each query to a ``(score_id, aggregator name)`` pair.

        This is a hook for the allele score annotator. The annotator calls
        it when the pipeline loads, to refuse a bad attribute early.

        :meta private:

        Parameters
        ----------
        queries : Sequence[ScoreAggregationQuery] or None
            The queries to resolve. ``None`` means every score of the
            resource, each with its default aggregator.

        Returns
        -------
        list[tuple[str, str]]
            One pair for each query, in the order asked.

        Raises
        ------
        ValueError
            If a query names an unknown score, or has no aggregator to
            resolve to.
        """
        # The shared aggregation.resolve_aggregation_queries on this score,
        # made public so AlleleScoreAnnotator can refuse a bad attribute as
        # the pipeline loads (D6 of the allele folding-read design).  That
        # function says why it stops at the aggregator name.
        return resolve_aggregation_queries(
            queries,
            score_definitions=self.score_definitions,
            all_scores=self.get_all_scores(),
            resource_id=self.resource_id)

    def resolve_allele_key_scores(
        self, allele_keys: Sequence[str],
    ) -> list[GenomicScoreDef]:
        """Resolve the score ids that suffix the allele keys.

        This is a hook for the allele score annotator. The annotator calls
        it when the pipeline loads, to refuse a bad ``include_attributes``
        early.

        :meta private:

        Parameters
        ----------
        allele_keys : Sequence[str]
            The score ids to resolve.

        Returns
        -------
        list[GenomicScoreDef]
            The definitions of the scores, in the order asked.

        Raises
        ------
        ValueError
            If a score id is unknown. The message lists the valid ids.
        """
        # The other half of what get_allele_scores_in_region_agg checks on
        # the call, made askable without reading.  The annotator uses it to
        # refuse a bad ``include_attributes`` as the pipeline loads rather
        # than per record.
        return self._resolve_score_defs(list(allele_keys))

    def get_allele_scores_in_region_agg(
        self, chrom: str, start: int, end: int,
        *,
        queries: Sequence[ScoreAggregationQuery] | None = None,
        allele_keys: Sequence[str] | None = None,
        score_filter: ScoreFilter | None = None,
    ) -> AlleleAggregate | None:
        """Reduce the alleles in a region to one value per query.

        Fold the rows that overlap the region and that ``score_filter``
        keeps, in one walk that holds no row. An allele row weighs 1.

        Parameters
        ----------
        chrom : str
            The chromosome name.
        start : int
            The first position of the region, 1-based.
        end : int
            The last position of the region.
        queries : Sequence[ScoreAggregationQuery] or None, optional
            What to compute. Each query is a score with an optional
            aggregator, and the aggregator of the query wins over the
            default aggregator of the score. ``None``, the default, means
            every score of the resource with its default aggregator.
        allele_keys : Sequence[str] or None, optional
            The score ids that suffix each allele key. ``None``, the
            default, builds no keys. An empty sequence builds the bare
            ``chrom:pos:ref:alt`` keys. See :func:`allele_key`.
        score_filter : ScoreFilter or None, optional
            Keep only the rows that this filter accepts. Build the filter
            with :meth:`compile_filter` of this score. ``None``, the
            default, keeps every row.

        Returns
        -------
        AlleleAggregate or None
            ``None`` when no row overlaps the region. The check happens
            before the filter. Otherwise an :class:`AlleleAggregate`. When
            the filter rejects every row, each aggregator answers for an
            empty selection: ``max`` gives ``None`` and ``list`` gives
            ``[]``. The keys are distinct and in the order the rows were
            first seen.

        Raises
        ------
        ValueError
            If a score id of ``queries`` or ``allele_keys`` is unknown, if
            a query has no aggregator to resolve to, or if the contig is
            not in the resource.
        ScoreFilterError
            If ``score_filter`` was compiled for another resource.

        Notes
        -----
        The call checks the request before it reads a row.

        """
        # ``None`` is judged before the filter and ownership is checked
        # first, through _selected_allele_records, which states the
        # contract.  The asymmetry with the fragment kind's folding read
        # is a property of the data, and the Consequences of ADR 0017 say
        # why.  Aggregators are built fresh for each call
        # (build_region_aggregators).
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
        return self._fold_allele_records(
            records, requests, aggregators, collector)

    def _fold_allele_records(
        self,
        records: Iterator[Record],
        requests: list[tuple[str, str]],
        aggregators: list[Aggregator],
        collector: _AlleleKeyCollector | None,
    ) -> AlleleAggregate:
        """Fold the selected records of an ``_agg`` read into its answer.

        The walk-consuming half every allele folding read shares: the
        records are the ones the filter kept, the request is resolved,
        and the aggregators are fresh.
        """
        if collector is not None:
            records = collector(records)
        score_ids = request_score_ids(requests)
        # The per-kind hook directly, not `values_from_records`: that
        # entry re-checks the contig, and on a tabix table the chromosome
        # list is rebuilt per call -- a cost linear in the contig count,
        # which `_check_allele_region_request` has already paid.  gain#1282
        # made this the general shape rather than this read's special case,
        # so the hook is what runs here and the fold cannot drift from what
        # the other allele reads mean by a segment.
        values = fold_region_segments(
            self._score_segments(
                records, self._resolve_score_defs(score_ids)),
            aggregators, requests,
            score_ids=score_ids, weigh=self.record_weight)
        return AlleleAggregate(
            tuple(values),
            None if collector is None else collector.keys)

    def get_allele_scores_for_allele_agg(
        self, chrom: str, pos: int, ref: str, alt: str,
        *,
        queries: Sequence[ScoreAggregationQuery] | None = None,
        allele_keys: Sequence[str] | None = None,
        score_filter: ScoreFilter | None = None,
    ) -> AlleleAggregate | None:
        """Reduce the rows of one allele to one value per query.

        Fold the rows at exactly ``(chrom, pos, ref, alt)``. A row that
        starts earlier and whose span reaches ``pos`` is not folded. The
        read folds the rows that :meth:`get_allele_scores_for_allele_rows`
        answers, with the queries, the aggregators and the
        :class:`AlleleAggregate` of :meth:`get_allele_scores_in_region_agg`.
        The read is allowed on both multiplicities and does not consult
        ``allele_score_mode``. On a ``one`` resource it folds the single
        row.

        Parameters
        ----------
        chrom : str
            The chromosome name.
        pos : int
            The position of the allele.
        ref : str
            The reference nucleotides.
        alt : str
            The alternative nucleotides.
        queries : Sequence[ScoreAggregationQuery] or None, optional
            What to compute. ``None``, the default, means every score of
            the resource with its default aggregator.
        allele_keys : Sequence[str] or None, optional
            As in :meth:`get_allele_scores_in_region_agg`. The answer has
            the one key ``chrom:pos:ref:alt``, or several keys when a
            suffixed score differs across the rows.
        score_filter : ScoreFilter or None, optional
            Keep only the rows that this filter accepts. ``None``, the
            default, keeps every row.

        Returns
        -------
        AlleleAggregate or None
            ``None`` when no row holds the allele. The check happens before
            the filter. When the filter rejects every row, the answer is an
            :class:`AlleleAggregate` in which each aggregator answers for
            no rows: ``max`` and ``count`` give ``None``, and ``list``
            gives ``[]``.

        Raises
        ------
        ValueError
            If a score id is unknown, if a query has no aggregator to
            resolve to, if an aggregator is bad, or if the contig is not in
            the resource.
        ScoreFilterError
            If ``score_filter`` was compiled for another resource.

        Notes
        -----
        The call checks the request before it reads a row.

        """
        requests = self.resolve_aggregation_queries(queries)
        aggregators = build_region_aggregators(
            requests, resource_id=self.resource_id)
        collector = (
            None if allele_keys is None
            else self._allele_key_collector(allele_keys))
        self._check_allele_region_request(chrom, score_filter)
        matches = self._allele_records(chrom, pos, ref, alt, None)
        first = next(matches, None)
        if first is None:
            return None
        return self._fold_allele_records(
            select_records(self, chain([first], matches), score_filter),
            requests, aggregators, collector)

    def get_allele_score_for_allele_agg(
        self, chrom: str, pos: int, ref: str, alt: str,
        *,
        score: str | None = None,
        aggregator: str | None = None,
        score_filter: ScoreFilter | None = None,
    ) -> AlleleAggregate | None:
        """Reduce the rows of one allele for one score.

        The singular form of :meth:`get_allele_scores_for_allele_agg`. It
        does not unwrap the answer: the :class:`AlleleAggregate` has a
        ``values`` tuple of one element.

        Parameters
        ----------
        chrom : str
            The chromosome name.
        pos : int
            The position of the allele.
        ref : str
            The reference nucleotides.
        alt : str
            The alternative nucleotides.
        score : str or None, optional
            The score id. ``None``, the default, is accepted only when the
            resource defines exactly one score.
        aggregator : str or None, optional
            The aggregator name. ``None``, the default, is the default
            aggregator of the score.
        score_filter : ScoreFilter or None, optional
            Keep only the rows that this filter accepts.

        Returns
        -------
        AlleleAggregate or None
            As :meth:`get_allele_scores_for_allele_agg`.

        Raises
        ------
        ValueError
            As :meth:`get_allele_scores_for_allele_agg`. Also when
            ``score`` is ``None`` and the resource defines several scores.
        ScoreFilterError
            If ``score_filter`` was compiled for another resource.

        """
        # Not unwrapped, as FragmentScore.get_fragment_score_overlapping_
        # region_agg does not unwrap its answer.
        return self.get_allele_scores_for_allele_agg(
            chrom, pos, ref, alt,
            queries=[ScoreAggregationQuery(
                self._resolve_single_score(score), aggregator)],
            score_filter=score_filter)

    def get_allele_scores_for_allele_rows(
        self, chrom: str, pos: int, ref: str, alt: str,
        *,
        scores: Sequence[str] | None = None,
        score_filter: ScoreFilter | None = None,
    ) -> list[tuple[ScoreValue, ...]]:
        """Return the values of every row of one allele, in file order.

        Several rows may carry one allele, and all of them are answered.
        The read does not reduce the rows and does not consult
        ``allele_score_mode``. The match is exact on the position and on
        the nucleotides: a row that starts earlier and whose span reaches
        ``pos`` is not this allele.

        Parameters
        ----------
        chrom : str
            The chromosome name.
        pos : int
            The position of the allele.
        ref : str
            The reference nucleotides.
        alt : str
            The alternative nucleotides.
        scores : Sequence[str] or None, optional
            The score ids to read. ``None``, the default, means every
            score of the resource.
        score_filter : ScoreFilter or None, optional
            Keep only the rows that this filter accepts. The filter is
            evaluated on every row that overlaps the position, not only on
            the rows of the allele. ``None``, the default, keeps every row.

        Returns
        -------
        list[tuple[ScoreValue, ...]]
            One tuple for each row that the filter keeps, parallel to
            ``scores``. The list is empty both when no row carries the
            allele and when the filter rejects every row. The read does
            not tell the two cases apart.

        Raises
        ------
        ValueError
            If a score id is unknown, or if the contig is not in the
            resource.
        ScoreFilterError
            If ``score_filter`` was compiled for another resource.

        Notes
        -----
        The call checks the request before it reads a row.

        """
        # Materialised: a point holds a handful of rows.  The read is the
        # region walk of get_allele_scores_in_region_rows at the one
        # position.  The requested values are read only off rows matching
        # the allele, but score_filter, being part of that shared walk, is
        # still evaluated on every row overlapping the position.
        score_defs = self._resolve_score_defs(scores)
        self._check_allele_region_request(chrom, score_filter)
        extract = self._extract_value
        return [
            tuple(extract(record, score_def) for score_def in score_defs)
            for record in self._allele_records(
                chrom, pos, ref, alt, score_filter)
        ]

    def _allele_records(
        self, chrom: str, pos: int, ref: str, alt: str,
        score_filter: ScoreFilter | None,
    ) -> Iterator[Record]:
        """The rows at exactly ``(chrom, pos, ref, alt)``, in file order.

        The point walk every per-allele read of the plane reduces:
        :meth:`_walk_allele_records` at the one position, kept to the rows
        whose own position and nucleotides are the allele's.  A row
        starting earlier whose span reaches ``pos`` is not this allele.
        The request is unchecked; the caller has checked it.
        """
        records = self._walk_allele_records(chrom, pos, pos, score_filter)
        if records is None:
            return iter(())
        return (
            record for record in records
            if (record[POS_BEGIN], record[REF], record[ALT]) == (pos, ref, alt)
        )

    def get_allele_scores_for_allele(
        self, chrom: str, pos: int, ref: str, alt: str,
        *,
        scores: Sequence[str] | None = None,
        score_filter: ScoreFilter | None = None,
    ) -> tuple[ScoreValue, ...] | None:
        """Return the values of the only row of one allele.

        The match is exact on the position and on the nucleotides: a row
        that starts earlier and whose span reaches ``pos`` is not this
        allele. The read does not consult ``allele_score_mode``.

        The read answers one row, as the :attr:`multiplicity` of the
        resource promises. A resource that declares
        ``allele_multiplicity: many`` is refused, because one allele may
        hold several rows. On a ``one`` resource, several rows that hold
        the allele are a data error. The rows are counted before
        ``score_filter``, so a filter that hides one of them does not hide
        the error. Until
        :func:`~gain.genomic_resources.resource_types.allele_multiplicity_enforced`
        answers true, the read warns once for each process and answers the
        first row. The filter applies to that row alone.

        Parameters
        ----------
        chrom : str
            The chromosome name.
        pos : int
            The position of the allele.
        ref : str
            The reference nucleotides.
        alt : str
            The alternative nucleotides.
        scores : Sequence[str] or None, optional
            The score ids to read. ``None``, the default, means every
            score of the resource, in definition order.
        score_filter : ScoreFilter or None, optional
            Keep the row only if this filter accepts it. ``None``, the
            default, accepts every row.

        Returns
        -------
        tuple[ScoreValue, ...] or None
            The values of the row, parallel to ``scores``. ``None`` when no
            row holds the allele, or when ``score_filter`` rejects the
            row.

        Raises
        ------
        AlleleMultiplicityError
            If the resource declares ``allele_multiplicity: many``. Also
            if several rows hold the allele on a ``one`` resource, once
            the enforcement release is reached. Read every row with
            :meth:`get_allele_scores_for_allele_rows`, or reduce the rows
            with :meth:`get_allele_scores_for_allele_agg`.
        ValueError
            If a score id is unknown, or if the contig is not in the
            resource.
        ScoreFilterError
            If ``score_filter`` was compiled for another resource.

        Notes
        -----
        The call checks the request before it reads a row, and before the
        ``many`` refusal: an unknown score id, an unknown contig and a
        foreign filter are refused on any resource.

        """
        score_defs = self._resolve_score_defs(scores)
        self._check_allele_region_request(chrom, score_filter)
        if self.multiplicity is AlleleScore.Multiplicity.MANY:
            raise AlleleMultiplicityError(
                self.resource_id,
                "declares allele_multiplicity: many, so one allele may "
                "hold several rows and a read answering one row cannot "
                "answer it; read every row with "
                "get_allele_scores_for_allele_rows, or reduce them with "
                "an _agg read")
        matches = self._allele_records(chrom, pos, ref, alt, None)
        record = next(matches, None)
        if record is None:
            return None
        if next(matches, None) is not None:
            if resource_types.allele_multiplicity_enforced():
                raise AlleleMultiplicityError(
                    self.resource_id,
                    "several rows hold this allele, but the resource does "
                    "not declare several rows per allele; if that is "
                    "intended, add the line `allele_multiplicity: many` "
                    "to its genomic_resource.yaml",
                    allele=(chrom, pos, ref, alt))
            resource_types.warn_undeclared_allele_multiplicity(
                logger, found_in=self.resource.get_full_id())
        record = next(select_records(self, [record], score_filter), None)
        if record is None:
            return None
        extract = self._extract_value
        return tuple(extract(record, score_def) for score_def in score_defs)

    def get_allele_score_for_allele(
        self, chrom: str, pos: int, ref: str, alt: str,
        *,
        score: str | None = None,
        score_filter: ScoreFilter | None = None,
    ) -> ScoreValue | None:
        """Return the value of one score at the only row of one allele.

        The singular form of :meth:`get_allele_scores_for_allele`.

        Parameters
        ----------
        chrom : str
            The chromosome name.
        pos : int
            The position of the allele.
        ref : str
            The reference nucleotides.
        alt : str
            The alternative nucleotides.
        score : str or None, optional
            The score id. ``None``, the default, is accepted only when the
            resource defines exactly one score.
        score_filter : ScoreFilter or None, optional
            Keep the row only if this filter accepts it.

        Returns
        -------
        ScoreValue or None
            The value of the score. ``None`` when no row holds the allele,
            or when ``score_filter`` rejects the row. The value itself may
            also be ``None`` when the row has no value for the score.

        Raises
        ------
        AlleleMultiplicityError
            As :meth:`get_allele_scores_for_allele`.
        ValueError
            As :meth:`get_allele_scores_for_allele`. Also when ``score`` is
            ``None`` and the resource defines several scores.
        ScoreFilterError
            If ``score_filter`` was compiled for another resource.

        """
        values = self.get_allele_scores_for_allele(
            chrom, pos, ref, alt,
            scores=[self._resolve_single_score(score)],
            score_filter=score_filter)
        if values is None:
            return None
        return values[0]

    def get_allele_scores_in_region_rows(
        self, chrom: str, start: int, end: int,
        *,
        scores: Sequence[str] | None = None,
        score_filter: ScoreFilter | None = None,
    ) -> Generator[AlleleEntry, None, None]:
        """Yield an :class:`AlleleEntry` for each row that overlaps a region.

        The entries come in the position order of the table. Rows that
        share an allele are each yielded. The read does not reduce the
        rows and does not consult ``allele_score_mode``.

        Parameters
        ----------
        chrom : str
            The chromosome name.
        start : int
            The first position of the region, 1-based.
        end : int
            The last position of the region.
        scores : Sequence[str] or None, optional
            The score ids to read. ``None``, the default, means every
            score of the resource.
        score_filter : ScoreFilter or None, optional
            Keep only the rows that this filter accepts. ``None``, the
            default, keeps every row.

        Returns
        -------
        Generator[AlleleEntry, None, None]
            One entry for each row that the filter keeps, with ``values``
            parallel to ``scores``. The generator yields nothing both when
            no row overlaps the region and when the filter rejects every
            row. :meth:`get_allele_scores_in_region_agg` tells the two
            cases apart.

        Raises
        ------
        ValueError
            If a score id is unknown, or if the contig is not in the
            resource.
        ScoreFilterError
            If ``score_filter`` was compiled for another resource.

        Notes
        -----
        The call checks the request, and the errors above are raised
        before the generator is returned. The table is read lazily, on the
        first ``next()``. Iterate one generator at a time, as with every
        region read of a table, and materialise what has to outlive the
        next read.

        """
        score_defs = self._resolve_score_defs(scores)
        self._check_allele_region_request(chrom, score_filter)
        return self._entries_of(
            chrom, start, end, score_filter, score_defs)

    def get_allele_score_for_allele_rows(
        self, chrom: str, pos: int, ref: str, alt: str,
        *,
        score: str | None = None,
        score_filter: ScoreFilter | None = None,
    ) -> list[ScoreValue]:
        """Return the value of one score for every row of one allele.

        The singular form of :meth:`get_allele_scores_for_allele_rows`.

        Parameters
        ----------
        chrom : str
            The chromosome name.
        pos : int
            The position of the allele.
        ref : str
            The reference nucleotides.
        alt : str
            The alternative nucleotides.
        score : str or None, optional
            The score id. ``None``, the default, is accepted only when the
            resource defines exactly one score.
        score_filter : ScoreFilter or None, optional
            Keep only the rows that this filter accepts.

        Returns
        -------
        list[ScoreValue]
            One value for each row that the filter keeps, in file order.
            The list is empty when no row carries the allele or when the
            filter rejects every row.

        Raises
        ------
        ValueError
            As :meth:`get_allele_scores_for_allele_rows`. Also when
            ``score`` is ``None`` and the resource defines several scores.
        ScoreFilterError
            If ``score_filter`` was compiled for another resource.

        """
        return [
            values[0]
            for values in self.get_allele_scores_for_allele_rows(
                chrom, pos, ref, alt,
                scores=[self._resolve_single_score(score)],
                score_filter=score_filter)
        ]

    def get_allele_score_in_region_rows(
        self, chrom: str, start: int, end: int,
        *,
        score: str | None = None,
        score_filter: ScoreFilter | None = None,
    ) -> Generator[
        tuple[int, str | None, str | None, ScoreValue], None, None,
    ]:
        """Yield ``(pos, ref, alt, value)`` for each row that overlaps a region.

        The singular form of :meth:`get_allele_scores_in_region_rows`.

        Parameters
        ----------
        chrom : str
            The chromosome name.
        start : int
            The first position of the region, 1-based.
        end : int
            The last position of the region.
        score : str or None, optional
            The score id. ``None``, the default, is accepted only when the
            resource defines exactly one score.
        score_filter : ScoreFilter or None, optional
            Keep only the rows that this filter accepts.

        Returns
        -------
        Generator[tuple[int, str or None, str or None, ScoreValue], None, None]
            One tuple for each row that the filter keeps. The generator
            yields nothing when no row overlaps the region or when the
            filter rejects every row. ``ref`` or ``alt`` is ``None`` when
            the table has no such column.

        Raises
        ------
        ValueError
            As :meth:`get_allele_scores_in_region_rows`. Also when
            ``score`` is ``None`` and the resource defines several scores.
        ScoreFilterError
            If ``score_filter`` was compiled for another resource.

        Notes
        -----
        The call checks the request. The table is read lazily, as in
        :meth:`get_allele_scores_in_region_rows`.

        """
        entries = self.get_allele_scores_in_region_rows(
            chrom, start, end,
            scores=[self._resolve_single_score(score)],
            score_filter=score_filter)
        return (
            (entry.pos, entry.ref, entry.alt, entry.values[0])
            for entry in entries)

    def _entries_of(
        self, chrom: str, start: int, end: int,
        score_filter: ScoreFilter | None,
        score_defs: list[GenomicScoreDef],
    ) -> Generator[AlleleEntry, None, None]:
        """Walk the region and read each record as an :class:`AlleleEntry`.

        :meth:`_walk_allele_records` -- the walk the folding read reduces
        -- with its absence answer turned into an empty stream.  Being a
        generator, it starts the walk on the first ``next()``.
        """
        records = self._walk_allele_records(chrom, start, end, score_filter)
        if records is None:
            return
        extract = self._extract_value
        for record in records:
            yield AlleleEntry(
                _allele_pos(record), record[REF], record[ALT],
                tuple(extract(record, score_def) for score_def in score_defs))

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
        """Return whether :meth:`fetch_region_allele_arrays` serves the scores.

        The answer needs the conditions of
        :meth:`~gain.genomic_resources.genomic_scores.GenomicScore.supports_region_value_arrays`
        (the backend and the value types of the scores) and one more: the
        table must declare at least one of the ``reference`` and
        ``alternative`` columns. One column is enough. A bigWig-backed
        score has no such columns and is not served.

        The method works on a score that is not open. In one case the
        answer is conservative there: a table with ``header_mode: file``
        that names its key columns only in its data file answers ``False``
        until the score is opened, and ``True`` after.

        Parameters
        ----------
        scores : list[str]
            The score ids that the caller wants to read.

        Returns
        -------
        bool
            ``True`` if :meth:`fetch_region_allele_arrays` accepts the
            scores. If the answer is ``False``, read per record with
            :meth:`~gain.genomic_resources.genomic_scores.GenomicScore.fetch_records`.
            The rows are the same.

        """
        # The key columns of a table are resolved when it opens
        # (_set_core_column_keys), from the config and, failing that, from
        # the header.  This method asks the same two questions in the same
        # order, with whichever of them can be answered yet: the
        # declaration always, and the header when the table already has
        # one.  The one gap is the file's, not the method's: a header
        # cannot be known without reading it.  The answer errs the safe
        # way, because a caller told False reads per record and gets the
        # same rows.  Only the key columns that the table declares count,
        # and they are configured independently: a table that declares
        # only ``alternative`` is served, with the missing side yielded as
        # the None that the record carries for it.  That keeps this read
        # and GenomicScore.fetch_records the same answer.
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
        """Fetch a region as column arrays, with the nucleotides.

        The read widens
        :meth:`~gain.genomic_resources.genomic_scores.GenomicScore.fetch_region_value_arrays`
        with the ``reference`` and ``alternative`` columns. It is meant for
        a caller that scans a whole region for allele content, such as the
        allele statistics.

        The nucleotides are raw and the scores are parsed. An NA sentinel
        of a score arrives as the non-value of the score. The two nucleotide
        columns go through no parsing: no upper-casing, no stripping and no
        sentinel handling. A caller that wants them normalised does it
        once, where the meaning is known.

        Parameters
        ----------
        chrom : str
            The chromosome name.
        pos_begin : int or None
            The first position of the region, 1-based. ``None`` means the
            start of the chromosome.
        pos_end : int or None
            The last position of the region. ``None`` means the end of the
            chromosome.
        scores : list[str]
            The score ids to read.
        batch_size : int, optional
            The most rows in a batch. The reference and alternative columns
            count toward ``VALUE_ARRAYS_CELL_BUDGET`` like two scores, so a
            batch holds at most ``budget // (len(scores) + 2)`` rows. The
            default is 100000.

        Returns
        -------
        Generator[AlleleRecordArrays, None, None]
            The batches, in position order. A batch has the position
            arrays and the value arrays of
            :meth:`~gain.genomic_resources.genomic_scores.GenomicScore.fetch_region_value_arrays`,
            followed by the ``reference`` and ``alternative`` arrays. A
            key column that the table does not declare is an array of
            ``None``.

        Raises
        ------
        TypeError
            If the score does not serve the read for ``scores``. Ask
            :meth:`supports_region_allele_arrays` first.
        ValueError
            If the score is not open, or if the contig is not in the
            resource.

        Notes
        -----
        The call checks the request. The errors above are raised when the
        method is called and not on the first ``next()``.

        """
        # The guards run when the method is called, not on the first
        # next(), which is why the streaming half lives in
        # _allele_array_batches and not in a ``yield`` here.  The
        # nucleotides are raw because build_tabular_parser reads them
        # equally verbatim, and a consumer that reads a region through
        # this method and through GenomicScore.fetch_records must get the
        # same strings and not two dialects of them.
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
