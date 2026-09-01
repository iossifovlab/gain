"""The :class:`GenomicScore` base class.

Everything the three score kinds share: config parsing and score-def
construction, the open/close lifecycle over the position table, and the
record and array reads. The kinds themselves live in :mod:`.position`,
:mod:`.allele` and :mod:`.fragment`, and the factories that dispatch
between them in :mod:`~gain.genomic_resources.genomic_scores.builders`.

Decomposing this class -- so that a kind's author reads the handful of hooks
their kind overrides rather than the whole base -- is gain#1027.  Its first
extraction (gain#1044) moved the scoredef lifecycle to
:mod:`~gain.genomic_resources.score_def` and took this module under the
1500-line cap, so the file-scoped ``too-many-lines`` pragma gain#1007 added
here when it restored that cap is gone; its second (gain#1074) moved the
region-aggregation machinery to :mod:`.aggregation`, leaving
:meth:`GenomicScore.aggregate_region` here as the orchestrator that hands
it the per-kind weight rule.  The remaining seams are #1027's other
children.
"""

from __future__ import annotations

import warnings
from abc import abstractmethod
from collections.abc import Generator, Iterator
from types import TracebackType
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Self,
    cast,
)

import numpy as np

from gain import logging
from gain.genomic_resources.bigwig_scores import (
    BIGWIG_VALUE_COLUMN,
    build_bigwig_scoredefs,
    extract_bigwig_value,
    extract_bigwig_value_na,
    validate_bigwig_scoredefs,
)
from gain.genomic_resources.genomic_position_table import (
    BigWigTable,
    VCFGenomicPositionTable,
    build_genomic_position_table,
)
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
from gain.genomic_resources.score_def import (
    BULK_PARSEABLE_VALUE_TYPES,
    GenomicScoreDef,
    ScoreValue,
    ValueExtractor,
    build_genomic_score_schema,
    extract_column_value,
    finish_scoredefs,
    parse_scoredef_config,
    validate_scoredefs,
)
from gain.genomic_resources.score_filter import (
    ScoreFilter,
    compile_score_filter,
    select_records,
)
from gain.genomic_resources.score_resource import ScoreResource
from gain.genomic_resources.vcf_scores import (
    extract_vcf_value,
    parse_vcf_scoredefs,
)

from .aggregation import (
    build_region_aggregator,
    distinct_score_ids,
    fold_region_segments,
    resolve_aggregator_requests,
)
from .records import (
    RecordArrays,
    clip_to_region,
)

if TYPE_CHECKING:
    # Only ever needed to type the VCF INFO proxies in annotations.  pysam
    # is a hard runtime dep and is already imported by the VCF table anyway, but
    # keeping it behind TYPE_CHECKING makes it unambiguous that the annotations
    # cost nothing at runtime.
    pass

logger = logging.getLogger(__name__)


# Default rows-per-batch hint for GenomicScore.fetch_region_value_arrays.  Big
# enough that the per-batch numpy overhead disappears against the per-row work
# it replaces, small enough that one batch's arrays stay comfortably in cache.
DEFAULT_VALUE_ARRAYS_BATCH_SIZE = 100_000


# The shared core of every fetch_region_segment_scores deprecation warning
# (the base method and the AlleleScore override); gain#844 removes the name.
_SEGMENT_SCORES_DEPRECATION = (
    "GenomicScore.fetch_region_segment_scores is deprecated; use "
    "fetch_region_segments. This name is retained until gain#844 "
    "removes it. ")


class GenomicScore(ScoreResource[GenomicScoreDef]):
    """Base class for genomic score resources.

    GenomicScore provides a unified interface for accessing and managing
    genomic annotation scores stored in various formats. It serves as the
    foundation for specialized score types including PositionScore (position-
    based scores) and AlleleScore (variant-specific scores).

    This abstract base class handles:
    - Resource configuration validation and normalization
    - Score definition management and parsing
    - File format abstraction through GenomicPositionTable
    - Histogram and statistics management
    - Default annotation attribute configuration
    - Context manager protocol for resource lifecycle

    Score resources can be stored in multiple formats:
    - Tabix-indexed files (TSV, BED)
    - VCF files (particularly for allele scores)
    - BigWig files (for position scores)
    - In-memory tables (for testing)

    Configuration Structure:
        A genomic score resource requires a YAML configuration file
        (genomic_resource.yaml) specifying:

        - **type**: Resource type (position_score, allele_score)
        - **table**: Table configuration with filename, format, and column
          mappings for chrom, pos_begin, pos_end (and ref/alt for allele scores)
        - **scores**: List of score definitions with id, type, name/index,
          description, and optional aggregators
        - **default_annotation**: Optional list specifying which scores to
          include in default annotations with optional name mappings
        - **histograms**: Optional histogram configurations for statistics

    Score Definition:
        Each score in the resource is defined with:
        - **id**: Unique identifier for the score
        - **type**: Data type (int, float, str, bool)
        - **name/index**: Column name or index in the data file
        - **desc**: Human-readable description
        - **na_values**: Values to treat as missing/NA (optional)
        - **hist_conf**: Histogram configuration for statistics (optional)
        - **aggregator**: Default aggregator (optional). How several
          values for one annotatable are reduced to one; the default
          depends on the resource type and the score's value type.

    Usage Pattern:
        Genomic scores follow a resource lifecycle pattern:

        1. Build/retrieve the resource from a repository
        2. Create a score object from the resource
        3. Open the score to initialize data access
        4. Query scores using fetch methods
        5. Close the score to release resources

        Example using context manager:
            >>> from gain.genomic_resources.genomic_scores import (
            ...     build_score_from_resource_id
            ... )
            >>> score = build_score_from_resource_id("phastCons100way")
            >>> with score.open():
            ...     # Score is open and ready to use
            ...     chromosomes = score.get_all_chromosomes()
            ...     scores = score.get_all_scores()
            ...     # Query data...
            >>> # Score is automatically closed

    Statistics and Histograms:
        GenomicScore supports automatic statistics generation including:
        - Value distribution histograms
        - Min/max ranges for numeric scores
        - Category frequencies for categorical scores
        - Custom histogram configurations per score

    Attributes:
        resource (GenomicResource): The underlying genomic resource object
        resource_id (str): Unique identifier for the resource
        config (dict): Validated and normalized configuration dictionary
        table (GenomicPositionTable): Data access abstraction layer
        score_definitions (dict[str, GenomicScoreDef]): Mapping of score IDs to
            their internal definitions including parsers and metadata
        table_loaded (bool): Flag indicating if the table is currently open

    Key Methods:
        open(): Initialize the score resource for data access
        close(): Release resources and close the data table
        get_all_scores(): Get list of all available score IDs
        get_all_chromosomes(): Get list of all available chromosomes
        get_score_definition(): Get metadata for a specific score
        get_default_annotation_attributes(): Get default annotation config
        get_histogram(): Load histogram for a score (if available)
        get_score_range(): Get value range for a numeric scores

    Per-kind Methods:
        A kind whose records read as something other than the span they
        cover states that ONCE, by overriding:
        - region_values_from_records(): what a region's raw records mean for
          this kind.  ``fetch_region_segments`` is it applied to
          ``fetch_records``, and the statistics scan is it applied to
          ``validate_records(fetch_records(...))`` -- so a kind states its
          reading once and both consumers get it (ADR 0008).
        - validate_records(): the rule this kind's records must hold to,
          which only the statistics scan applies.
        - validate_record_arrays(): the same rule over a batch's columns,
          which only the statistics scan's vectorized path applies.
        - record_weight(): how many times one record's value counts when a
          region is aggregated.  Every reader goes through it -- the
          annotators' ``aggregate_region``, the per-record scan, and the
          bulk scan via ``record_weights`` (gain#1095).
        - _aggregation_segments(): whether those records are cut down to
          the queried window before they are weighed.  Unlike the others
          this HAS a default -- not clipping -- because it is a
          consequence of the weight rule rather than a rule of its own: a
          kind that counts a record once counts it wherever it falls.

        All but the last have no default.  A kind that inherited one would
        be validated, or weighed, by a rule nobody chose for it, which is
        the failure ADR 0008 exists to undo.

    See Also:
        - PositionScore: For position-based genomic scores
        - AlleleScore: For variant-specific genomic scores
        - GenomicResource: Base resource abstraction
        - GenomicPositionTable: Table format abstraction
    """

    # How a value is read off a record.  Installed by :meth:`open`, from the
    # table's ``yields_records`` claim, and declared here with NO default on
    # purpose: a record's payload means two different things -- a raw row or a
    # VCF (variant, allele index) pair -- so no single extractor reads both,
    # and a default would have to be wrong for one of them.  Unset until open()
    # routes, an unopened score raises AttributeError rather than silently
    # reading a VCF record as a row; open() installs it *before* publishing
    # table_loaded, so no caller can observe the gap (see open()).
    _extract_value: ValueExtractor

    # How a score of this resource type is reduced when a caller reads several
    # values for one annotatable, keyed by the score's value type.  Declared
    # per CLASS because the reduction is a property of the resource type -- a
    # position score is aggregated over a region of positions, an allele score
    # over the alleles at one -- and a ``GenomicScoreDef`` cannot know which
    # kind it belongs to.
    #
    # Handed to ``score_def.finish_scoredefs``, which applies it to every
    # score whose config does not state an ``aggregator:`` -- today every
    # deployed score: 0 of 16502 resource configs set one.
    DEFAULT_AGGREGATORS: ClassVar[dict[str, str | None]] = {}

    def __init__(self, resource: GenomicResource):
        self.resource = resource
        self.resource_id = resource.resource_id
        assert self.resource.config is not None
        self.config: dict = self.resource.config
        self.config = self.validate_and_normalize_schema(
            self.config, resource,
        )
        self.config["id"] = resource.resource_id
        self.table_loaded = False
        self.table = build_genomic_position_table(
            self.resource, self.config["table"],
        )
        self.score_definitions = self._build_scoredefs()

    @staticmethod
    def get_schema() -> dict[str, Any]:
        """The config this kind accepts; each kind extends it."""
        return build_genomic_score_schema()

    def _build_scoredefs(self) -> dict[str, GenomicScoreDef]:
        """Route this resource's definitions through their construction path.

        The one piece of the scoredef lifecycle that did NOT move to
        :mod:`~gain.genomic_resources.score_def` in gain#1044: it dispatches
        on the table's type into ``parse_vcf_scoredefs`` and
        ``build_bigwig_scoredefs``, and both of those modules import
        ``score_def``, so hosting this there would close an import cycle.
        Everything it calls is a function now, and the class's only
        contribution is ``DEFAULT_AGGREGATORS``, passed explicitly -- once,
        at the single exit the three routes converge on, which is where
        ``finish_scoredefs`` documents that it has to be applied.
        """
        config_scoredefs = None
        if "scores" in self.config:
            config_scoredefs = parse_scoredef_config(self.config)

        scoredefs: dict[str, GenomicScoreDef]
        if isinstance(self.table, VCFGenomicPositionTable):
            merge = bool(self.config.get("merge_vcf_scores", False))

            scoredefs = parse_vcf_scoredefs(
                cast(dict[str, Any], self.table.header),
                config_scoredefs,
                merge=merge)
        elif config_scoredefs is None:
            raise ValueError("No scores configured and not using a VCF")
        elif isinstance(self.table, BigWigTable):
            scoredefs = build_bigwig_scoredefs(self.config, config_scoredefs)
        else:
            scoredefs = config_scoredefs

        return finish_scoredefs(scoredefs, self.DEFAULT_AGGREGATORS)

    def get_config(self) -> dict[str, Any]:
        return self.config

    def get_default_annotation_attributes(self) -> list[Any]:
        """Collect default annotation attributes."""
        default_annotation = self.get_config().get("default_annotation")
        if default_annotation is None:
            return [
                {"source": attr, "name": attr}
                for attr in self.score_definitions
            ]

        if not isinstance(default_annotation, list):
            raise TypeError(
                "The default_annotation in the "
                f"{self.resource_id} resource is not a list.")
        return default_annotation

    def get_default_annotation_attribute(self, score_id: str) -> str | None:
        """Return default annotation attribute for a score.

        Returns None if the score is not included in the default annotation.
        Returns the name of the attribute if present or the score if not.
        """
        attributes = self.get_default_annotation_attributes()
        result = []
        for attr in attributes:
            if attr["source"] != score_id:
                continue
            dst = score_id
            if "name" in attr:
                dst = attr["name"]
            result.append(dst)
        if result:
            return ",".join(result)
        return None

    def close(self) -> None:
        self.table.close()
        self.table_loaded = False

    def is_open(self) -> bool:
        return self.table_loaded

    def _select_value_extractor(
        self, *, is_vcf: bool, is_bigwig: bool,
    ) -> ValueExtractor:
        """Pick the per-record value read for this table's payload.

        ONE decision, per table, taken at open rather than per line.  What it
        turns on is what a record's PAYLOAD *is*, which is whatever the backend
        that built it says it is:

        * a **VCF** record's payload carries the variant and the pysam INFO
          proxies, and a VCF score is an INFO field addressed by name --
          :func:`extract_vcf_value`;
        * a **bigWig** record's payload IS the interval's value, so the read is
          an identity (:func:`extract_bigwig_value`) -- or, for the rare
          resource that configures NA sentinels, an identity plus one
          membership test (:func:`extract_bigwig_value_na`).  Which of the two
          is settled here, from the score definitions, and never per record:
          the sentinel set is fixed for the life of the open score.  A bigWig
          declares exactly one score (``validate_bigwig_scoredefs`` refuses
          more), so there is a single answer to give; ``any`` states that
          without depending on it;
        * any other record-yielding table's payload is a raw row, read by
          integer column -- :func:`extract_column_value`.

        The table's ``yields_records`` claim is simply believed: that every
        backend's claim matches what it really yields is pinned statically,
        over all four of them, by test_backend_record_contract.py, so the fetch
        path pays nothing for it.

        A table that yields no records is a programming error, not a data
        error: there is no fallback reader, so a backend leaving the flag
        False has nothing that can read it and we refuse rather than guess.
        (Nothing in the tree reaches it: it guards a backend added later
        without its migration.)
        """
        if is_vcf:
            return extract_vcf_value
        if is_bigwig:
            # A bigWig value is a float, so only a NUMERIC sentinel can
            # ever match it -- the four text tokens a float score defaults
            # to ("", "nan", ".", "NA") cannot.  Testing for those rather
            # than for a non-empty set is what lets the definition keep its
            # default (and so its statistics hash) while every unconfigured
            # bigWig still takes the identity read.
            if any(
                not isinstance(sentinel, str)
                for score_def in self.score_definitions.values()
                for sentinel in score_def.na_values
            ):
                return extract_bigwig_value_na
            return extract_bigwig_value
        if self.table.yields_records:
            return extract_column_value
        raise TypeError(
            f"{type(self.table).__name__} does not yield records, so "
            f"there is no score line that can read it. A genomic "
            f"position table backend must set yields_records = True "
            f"and yield six-slot record tuples: see the record "
            f"contract in gain.genomic_resources."
            f"genomic_position_table.record, and "
            f"test_backend_record_contract.py for what that backend "
            f"is held to.")

    def _resolve_score_indices(
        self, *, is_vcf: bool, is_bigwig: bool,
    ) -> None:
        """Resolve each score's configured address to a payload column.

        Runs after ``table.open()``, because the by-NAME case is the one thing
        here that has to consult the table's header.

        These raise rather than assert: an assert reported a misconfigured
        resource with a message-less AssertionError naming neither the resource
        nor the score, and ``python -O`` strips it altogether, leaving the
        by-name branch to call ``header.index(None)`` on a table whose header
        may itself be ``None``.  A resource config is data, and bad data is
        reported, not asserted away.
        """
        if is_vcf:
            # A VCF score has no column to resolve: it is addressed by INFO
            # KEY, which is ``col_name``, and :func:`extract_vcf_value` reads
            # that attribute directly.  All this enforces is that the key is
            # actually there.
            for score_def in self.score_definitions.values():
                if score_def.col_name is None:
                    raise ValueError(
                        f"score {score_def.score_id!r} of VCF resource "
                        f"{self.resource_id!r} has no INFO key; a VCF score "
                        f"is addressed by name")
            return

        if is_bigwig:
            # A bigWig has exactly one column -- the payload, which IS the
            # value -- so there is nothing to resolve: the answer is 0, and it
            # is the same 0 for the canonical config (which addresses no column
            # at all) and for the deprecated ``index: 3`` that
            # ``validate_bigwig_scoredefs`` has already warned about.  Only
            # ``fetch_region_value_arrays`` reads it; the per-record path
            # indexes nothing.
            for score_def in self.score_definitions.values():
                score_def.score_index = BIGWIG_VALUE_COLUMN
            return

        # Index first, because it needs nothing from the table.
        for score_def in self.score_definitions.values():
            if score_def.col_index is not None:
                if score_def.col_name is not None:
                    raise ValueError(
                        f"score {score_def.score_id!r} of resource "
                        f"{self.resource_id!r} configures both a column "
                        f"name ({score_def.col_name!r}) and a column "
                        f"index ({score_def.col_index}); they are "
                        f"mutually exclusive")
                score_def.score_index = score_def.col_index
            elif score_def.col_name is not None:
                if self.table.header is None:
                    raise ValueError(
                        f"score {score_def.score_id!r} of resource "
                        f"{self.resource_id!r} is addressed by column "
                        f"name ({score_def.col_name!r}), but its table "
                        f"has no header to resolve that name against; "
                        f"address it by column_index instead")
                score_def.score_index = self.table.header.index(
                    score_def.col_name)
            else:
                raise ValueError(
                    f"score {score_def.score_id!r} of resource "
                    f"{self.resource_id!r} configures neither "
                    f"column_name nor column_index; one is required")

    def open(self) -> Self:
        """Open genomic score resource and returns it.

        **Validate and route BEFORE opening, and so before publishing.**  Every
        input to both steps is known at construction -- the table's class, its
        ``yields_records`` ClassVar, and the score definitions -- so neither
        needs the open handle, and two things fall out of that order:

        * a refusal costs no handle.  Routing after ``table.open()`` would
          leave a caller that is not using the ``with`` form holding an opened
          pysam handle it cannot reach: ``table_loaded`` would still be
          False, so ``close()`` would not have been reached.  Raising first
          means there is nothing to leak.  The bigWig config validation sits
          here for exactly that reason.
        * ``table_loaded = True`` is what makes this score look open to
          everyone else: from that write on, another caller's open() takes the
          is_open() early return above and reads ``_extract_value`` straight
          away.  Routed last, that caller could catch the score
          published-but-unrouted, and since the routing has no default at
          all, that caller reads an AttributeError.  Scores are
          shared across threads (the process-wide in-memory fragment-score
          cache; gain-web-api's thread pool), so the window is reachable;
          this ordering keeps the ROUTING out of it.  Pinned by
          test_the_score_is_routed_before_it_reports_itself_open.

        It does not make open() as a whole safe to race, and does not claim to:
        ``_resolve_score_indices`` still runs after the score has published
        itself open, so a caller that catches that window reads a score def
        with no ``score_index`` yet.  That window is older than this ordering
        and untouched by it -- open() is not synchronised, and making it so is
        a separate change.
        """
        if self.is_open():
            logger.info(
                "opening already opened genomic score: %s",
                self.resource.resource_id)
            return self
        is_vcf = isinstance(self.table, VCFGenomicPositionTable)
        is_bigwig = isinstance(self.table, BigWigTable)

        if is_bigwig:
            validate_bigwig_scoredefs(
                self.resource_id, self.score_definitions)
        self._extract_value = self._select_value_extractor(
            is_vcf=is_vcf, is_bigwig=is_bigwig)

        self.table.open()
        self.table_loaded = True
        # A bigWig's score config has already been validated, and by a stricter
        # rule: ``validate_bigwig_scoredefs`` permits no column addressing at
        # all (bar the deprecated ``index: 3``), where this method *demands*
        # one whenever the table reports a header.  A bigWig table has no
        # header to speak of -- but one whose config carries a stray
        # ``header:``/``header_mode:`` pair reports one anyway, and those keys
        # are ignored for bigWig (see ``genomic_position_table.utils``), so
        # they must not decide how the scores are checked either.
        if "scores" in self.config and not is_bigwig:
            validate_scoredefs(self.config, self.table, self.resource)
        self._resolve_score_indices(is_vcf=is_vcf, is_bigwig=is_bigwig)

        return self

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            logger.error(
                "exception while working with genomic score: %s, %s, %s",
                exc_type, exc_value, exc_tb)
        self.close()

    @staticmethod
    def _inverted_span_error(record: Record) -> OSError:
        """Build the refusal for a record whose end precedes its begin.

        Returned rather than raised, so the raise stays at the site that read
        the slots -- and so the several sites that perform this check share
        one message.  Off the hot path by construction: a caller compares two
        integers per record and only calls this when the comparison fails.

        Not to be confused with
        :func:`~gain.genomic_resources.resource_errors.backwards_records_error`,
        despite the neighbouring vocabulary: that one refuses a resource whose
        records move backwards *along a contig*, raises
        :class:`MalformedResourceError`, and belongs to ``validate_records``.
        This one is about a single record's own two ends, and stays an
        ``OSError`` -- the type the read path has always raised for it, and
        the type the tests pin.

        The message names the record by its DECODED slots rather than
        interpolating it.  A record's last slot is the backend's payload, so
        ``f"{record}"`` would print a whole ``pysam.VariantRecord`` -- whose
        repr is the entire VCF line -- or a ``TupleProxy``.
        """
        chrom = record[CHROM]
        pos_begin = record[POS_BEGIN]
        pos_end = record[POS_END]
        ref, alt = record[REF], record[ALT]
        ref_alt = f" {ref}->{alt}" if ref is not None or alt is not None \
            else ""
        return OSError(
            f"The resource record {chrom}:{pos_begin}-{pos_end}{ref_alt} "
            f"has a region with end {pos_end} smaller than the "
            f"beginning {pos_begin}.")

    @staticmethod
    def _record_to_begin_end(record: Record) -> tuple[str, int, int]:
        """Read a record's three positional slots, checking their order.

        Returns the chrom as well, so it is the wrong door for a caller that
        wants only the two positions: read the slots and raise
        :meth:`_inverted_span_error` directly, as the per-record loops do.
        """
        chrom = record[CHROM]
        pos_begin = record[POS_BEGIN]
        pos_end = record[POS_END]
        if pos_end < pos_begin:
            raise GenomicScore._inverted_span_error(record)
        return chrom, pos_begin, pos_end

    def _get_header(self) -> tuple[Any, ...] | None:
        assert self.table is not None
        return self.table.header

    def compile_filter(self, expression: str) -> ScoreFilter:
        """Compile a boolean expression into a filter over this score.

        The expression names this resource's own scores and relates them
        with ``>``, ``>=``, ``<``, ``<=``, ``==``, ``!=`` and ``in``,
        combined with ``not``, ``and`` and ``or``; the result is
        passed back to any of the record reads as ``score_filter``.

        Raises :class:`~gain.genomic_resources.score_filter.ScoreFilterError`
        on an expression that does not parse or that names a score this
        resource does not define.  See
        :func:`~gain.genomic_resources.score_filter.compile_score_filter` for
        what compiling settles,
        ``docs/adr/0017-score-filtering-is-a-score-capability.md`` for why
        the capability sits on the score, and
        ``docs/adr/0018-score-filter-grammar-extension.md`` for the
        language's precedence and what a name may contain.
        """
        return compile_score_filter(self, expression)

    def fetch_records(
        self,
        chrom: str,
        pos_begin: int | None,
        pos_end: int | None,
        *,
        score_filter: ScoreFilter | None = None,
    ) -> Generator[Record, None, None]:
        """Yield the records of a region, optionally filtered.

        A caller reads a record's positional fields from its slots
        (``record[CHROM]``, ``record[POS_BEGIN]``, ...) and a score value
        through :meth:`get_score_value_from_record` or
        :meth:`get_score_values_from_record` on this score.

        ``score_filter`` is a predicate from :meth:`compile_filter`, applied
        to each record; only records it accepts are yielded.  ``None`` --
        the default -- yields what the table yields, and is the whole of
        what this method did before filtering became a score capability.

        ``chrom`` is required, here and throughout the region-read family.
        A caller that wants every record of a table asks the table:
        ``score.table.get_all_records()``.

        Nothing here is checked before the first ``next()``, the filter's
        ownership included: every backend's
        ``get_records_in_region`` is itself a generator function, so an
        unknown contig has always been reported from the first record read
        rather than from the call, and there is no eagerness left to
        preserve by structuring this any other way.  That is a property of
        *this* read rather than a rule for the family: a read that
        materialises has no generator body to defer a refusal into, and
        :meth:`AlleleScore.fetch_allele_records()
        <.allele.AlleleScore.fetch_allele_records>` accordingly refuses from
        the call.
        """
        records = self.table.get_records_in_region(chrom, pos_begin, pos_end)
        yield from select_records(self, records, score_filter)

    def get_score_value_from_record(
        self, record: Record, score_id: str,
    ) -> ScoreValue:
        """Read one configured score off a record of this score's table."""
        return self._extract_value(record, self.score_definitions[score_id])

    def _resolve_score_defs(
        self, scores: list[str] | None,
    ) -> list[GenomicScoreDef]:
        """Resolve requested score ids to definitions, refusing unknown ones.

        ``None`` asks for every score this resource defines.  A score id the
        resource does not define is a caller error, and it is refused here --
        before any data is read -- so the refusal does not depend on whether
        the queried region happens to hold a record.  A typo answering
        differently on a populated contig than on an empty one is the failure
        this exists to prevent.
        """
        if scores is None:
            scores = self.get_all_scores()
        unknown = [
            score_id for score_id in scores
            if score_id not in self.score_definitions
        ]
        if unknown:
            raise ValueError(
                f"genomic score <{self.resource_id}> does not define "
                f"{sorted(unknown)}; it has "
                f"{sorted(self.score_definitions)}")
        return [self.score_definitions[score_id] for score_id in scores]

    def get_score_values_from_record(
        self, record: Record, score_defs: list[GenomicScoreDef],
    ) -> list[ScoreValue]:
        """Read several scores off one record, for ALREADY-resolved defs.

        The bulk counterpart of :meth:`get_score_value_from_record`: a caller
        resolves score names to definitions once per fetch and passes them per
        record, so the name->definition lookup stays out of the per-record
        loop.
        """
        extract = self._extract_value
        return [extract(record, score_def) for score_def in score_defs]

    def supports_region_value_arrays(self, scores: list[str]) -> bool:
        """Whether :meth:`fetch_region_value_arrays` will serve these scores.

        Answers the two things a caller can be wrong about: the backend
        serves the bulk column-array read, AND every named score is one this
        facade can parse.  A predicate that answered only the first would say
        True for a call that then refuses -- not a capability query but a trap.

        It is not a promise the call cannot fail for some OTHER reason.  A
        score whose configured column index does not exist in its backend's
        payload still raises (deliberately -- see ``BigWigTable``), and so
        does a closed score or an unknown contig.  This answers "is this score
        the kind this method serves", not "is every argument valid".

        The value-type half is not a consumer's condition leaking in: the
        facade parses, so it serves the value types
        :meth:`GenomicScoreDef.parse_array()
        <gain.genomic_resources.score_def.GenomicScoreDef.parse_array>`
        defines a column parse for
        (:data:`~gain.genomic_resources.score_def.BULK_PARSEABLE_VALUE_TYPES`)
        and no others.  What a *consumer* additionally needs stays with the
        consumer: the
        statistics scan also requires a bounded region and a resource kind it
        is exercised against, and it keeps asking that itself (see
        ``genomic_scores_impl.scan.bulk_scan_eligible``).  What it does
        NOT require is a particular record shape: the accumulator reads the
        kind's own ``record_weight`` and the scan's door reads the
        kind's own ``validate_record_arrays``, so a position, allele and
        fragment score are all served.

        Answerable on an UNOPENED score: the table and the score definitions
        are both built in ``__init__``, so nothing here touches the file.
        """
        if not self.table.supports_value_arrays:
            return False
        for score_id in scores:
            score_def = self.score_definitions.get(score_id)
            if score_def is None \
                    or score_def.value_type not in BULK_PARSEABLE_VALUE_TYPES:
                return False
        return True

    def _require_open_and_known_chrom(self, chrom: str) -> None:
        """Refuse a region read this score cannot answer at all.

        The two conditions every bulk column read shares, stated once for
        the readers that widen it.  Several OLDER reads in this module spell
        the same pair out inline; they are left as they are rather than
        swept into this change, and a few of them word the contig message
        differently on purpose (an allele read names the resource in it).
        """
        if not self.is_open():
            raise ValueError(f"genomic score <{self.resource_id}> is not open")
        if chrom not in self.get_all_chromosomes():
            raise ValueError(
                f"{chrom} is not among the available chromosomes.")

    def _value_arrays_refusal_reason(self) -> str:
        """Why :meth:`supports_region_value_arrays` said no, for a raiser.

        The two halves of that predicate, worded for a caller who ignored it.
        Stated here rather than at each raise site so the reason cannot drift
        from the predicate it explains, nor between the readers that widen it
        (:meth:`AlleleScore.fetch_region_allele_arrays`).
        """
        if not self.table.supports_value_arrays:
            return (
                f"its {type(self.table).__name__} backend leaves "
                f"supports_value_arrays False")
        return (
            "not every requested score has a value type the column "
            f"parse serves {sorted(BULK_PARSEABLE_VALUE_TYPES)}")

    def fetch_region_value_arrays(
        self,
        chrom: str,
        pos_begin: int | None,
        pos_end: int | None,
        scores: list[str],
        *,
        batch_size: int = DEFAULT_VALUE_ARRAYS_BATCH_SIZE,
    ) -> Generator[
            RecordArrays, None, None]:
        """Fetch a region as column arrays, without building a record per row.

        The bulk counterpart of :meth:`fetch_records`, for a caller that scans a
        whole region and wants columns rather than rows -- statistics, above
        all.  Each batch is ``(pos_begin, pos_end, {score_id: values})``: the
        one-based position arrays, plus one array of **parsed** values per
        requested score.

        **Values are parsed, by the same contract the per-record read uses.**
        Each column goes through :meth:`GenomicScoreDef.parse_array()
        <gain.genomic_resources.score_def.GenomicScoreDef.parse_array>`, whose
        agreement with the per-value :meth:`GenomicScoreDef.parse_value()
        <gain.genomic_resources.score_def.GenomicScoreDef.parse_value>` is
        pinned by test_parse_array_agrees_with_parse_value_fuzz.  So NA
        sentinels and unparseable cells arrive as that score's non-value,
        whatever the backend stores underneath: a ``float`` or ``int`` score
        yields ``float64`` with ``nan`` for no value, a ``str`` score an
        ``object`` array with ``None``.  **The array's dtype follows the
        score's declared type, not the backend's** -- a caller reading several
        scores in one batch can be handed both shapes.

        That parse is why a value type the definition cannot parse as a column
        is refused, and why :meth:`supports_region_value_arrays` asks about
        the scores and not only about the backend.

        **It does NOT clip.**  A record overlapping the region's start is
        yielded whole, exactly as :meth:`fetch_records` yields it; trimming to
        ``[pos_begin, pos_end]`` is the caller's, because what a partial
        overlap means depends on what the caller is computing.

        ``batch_size`` is a HINT.  A backend whose read granularity is fixed by
        its own windowing -- ``BigWigTable``, whose batches are sized by its
        adaptive fetch window -- ignores it.

        Each score id gets an array of its own -- the parse builds one per
        id, so two ids sharing a payload column do not alias.

        The guards below run when this method is CALLED, not on the first
        ``next()`` -- which is why the streaming half lives in
        ``_value_array_batches`` rather than a ``yield`` here.
        """
        if not self.supports_region_value_arrays(scores):
            # Refuse here rather than let the call reach the table.  A VCF
            # table INHERITS the tabix implementation, so an unguarded call
            # does not fail cleanly -- it trips that method's
            # ``assert isinstance(self.pysam_file, pysam.TabixFile)`` and
            # yields a message-less AssertionError (nothing at all under
            # ``python -O``).  Probing this capability by catching is therefore
            # not viable; ask supports_region_value_arrays() first.
            reason = self._value_arrays_refusal_reason()
            raise TypeError(
                f"genomic score <{self.resource_id}> does not serve "
                f"fetch_region_value_arrays for {sorted(scores)}: {reason}. "
                f"Ask supports_region_value_arrays(scores) before calling.")
        self._require_open_and_known_chrom(chrom)
        return self._value_array_batches(
            self._score_column_indexes(scores),
            (chrom, pos_begin, pos_end), batch_size)

    def _value_array_batches(
        self,
        columns: dict[str, int],
        region: tuple[str, int | None, int | None],
        batch_size: int,
    ) -> Generator[
            RecordArrays, None, None]:
        """Stream the batches for an already-validated request.

        Split out so :meth:`fetch_region_value_arrays` is a plain function and
        its guards fire when it is CALLED.  Were it a generator itself, every
        one of those checks would be deferred to the first ``next()``, so a
        caller that built the generator and passed it elsewhere would be handed
        the refusal at some arbitrary later point, far from the mistake.
        """
        for begin, end, values, _cells in self._parsed_column_batches(
                columns, region, batch_size):
            yield begin, end, values

    def _parsed_column_batches(
        self,
        columns: dict[str, int],
        region: tuple[str, int | None, int | None],
        batch_size: int,
        extra_columns: frozenset[int] = frozenset(),
    ) -> Generator[
            tuple[np.ndarray, np.ndarray,
                  dict[str, np.ndarray], dict[int, np.ndarray]],
            None, None]:
        """The column read every bulk reader is made of: parse, plus cells.

        One statement of the parse loop, because there is more than one
        reader over it: :meth:`_value_array_batches` and
        :meth:`AlleleScore._allele_array_batches`.  Two copies of it is how
        the two would come to disagree about a batch's positions, its NA
        handling or its dtypes -- the drift ADR 0008 spends its length on.

        ``extra_columns`` are fetched but NOT parsed, and reach the caller
        through the raw ``cells`` alongside the parsed values.  That is the
        whole of what a reader wanting a non-score column adds: it asks for
        the index and reads it out itself, rather than teaching this loop
        what the column means.
        """
        chrom, pos_begin, pos_end = region
        defs = {
            score_id: self.score_definitions[score_id]
            for score_id in columns
        }
        wanted = set(columns.values()) | set(extra_columns)
        for begin, end, cells in self.table.get_region_value_arrays(
                chrom, pos_begin, pos_end, wanted, batch_size):
            yield begin, end, {
                score_id: defs[score_id].parse_array(cells[column])
                for score_id, column in columns.items()
            }, cells

    def _score_column_indexes(self, scores: list[str]) -> dict[str, int]:
        """Score id -> payload column index, resolved once for a whole scan.

        No cast needed: ``score_index`` is an ``int``.  A VCF score is
        addressed by ``col_name`` and has none, which is how the type already
        says the VCF backend does not reach here.
        """
        return {
            score_id: self.score_definitions[score_id].score_index
            for score_id in scores
        }

    def get_all_chromosomes(self) -> list[str]:
        if not self.is_open():
            raise ValueError(f"genomic score <{self.resource_id}> is not open")

        return self.table.get_chromosomes()

    def region_values_from_records(
        self,
        records: Iterator[Record],
        chrom: str,
        pos_begin: int | None = None,  # ruff: ignore[unused-method-argument]
        pos_end: int | None = None,  # ruff: ignore[unused-method-argument]
        scores: list[str] | None = None,
    ) -> Generator[
            tuple[int, int, list[ScoreValue]], None, None]:
        """Extract this kind's ``(begin, end, values)`` from raw records.

        The region read expressed as a function OF a record stream, which is
        what lets the two consumers of a region differ by what they COMPOSE
        rather than by a flag: :meth:`fetch_region_segments` is this
        applied to :meth:`fetch_records`, and the statistics scan is this
        applied to
        ``validate_records(fetch_records(...))``.  Neither can quietly
        acquire the other's behaviour, and no argument travels down to say
        which of the two is reading (ADR 0008).

        ``chrom``, ``pos_begin`` and ``pos_end`` name the region the records
        were asked for.  Nothing is fetched here, and nothing is reshaped to
        the window either -- what a partial overlap means belongs to the
        caller (ADR 0008); a consumer answering a question about the window
        clips with :func:`~.records.clip_span`.  The positions are what the
        guards below are about.

        The guards run when this is CALLED rather than on the first
        ``next()`` -- the pattern :meth:`fetch_records` documents -- which is
        why the streaming half lives in ``_score_segments``.  They
        stay here rather than moving down into ``fetch_records``: that method
        is on the annotation hot path, where a per-call
        ``get_all_chromosomes()`` membership scan is a real cost.

        This base body yields every record at its own extent, which is what
        a position score and a fragment score both mean by it.
        """
        score_defs = self._region_read_defs(chrom, scores)
        return self._score_segments(records, score_defs)

    def _region_read_defs(
        self, chrom: str, scores: list[str] | None,
    ) -> list[GenomicScoreDef]:
        """Refuse a region request this score cannot serve, before any record.

        Shared by every kind's :meth:`region_values_from_records`, so a
        closed score, an unknown contig and an unknown score id are refused
        alike whatever the kind.  The score ids are resolved once for the
        whole region rather than per record, and before the first record
        rather than on it: a typo answering differently on a populated contig
        than on an empty one is the failure that eagerness prevents.
        """
        if not self.is_open():
            raise ValueError(f"genomic score <{self.resource_id}> is not open")

        if chrom not in self.get_all_chromosomes():
            raise ValueError(
                f"{chrom} is not among the available chromosomes.")

        return self._resolve_score_defs(scores)

    def _score_segments(
        self,
        records: Iterator[Record],
        score_defs: list[GenomicScoreDef],
    ) -> Generator[
            tuple[int, int, list[ScoreValue]], None, None]:
        """Stream each record's own span for an already-checked request.

        Every record is yielded at its full extent, including one that only
        partly overlaps -- or entirely misses -- the region it was fetched
        for.  What a partial overlap means depends on what the caller is
        computing, so deciding it belongs to the window-answering consumers,
        each of which clips with :func:`clip_span` (ADR 0008).

        A record whose end precedes its begin is refused: that is a claim
        about the record itself, not about any window.  A record outside
        the queried region is NOT refused -- a backend answering a region
        query with such a record is misconfigured rather than holding bad
        data (a table whose index and ``pos_end`` name different columns,
        gain#553), which ADR 0008 refuses at ``open()`` and deliberately
        not here.

        The hottest loop in the read path, so it reads its record slots
        directly rather than through the helpers that wrap them (gain#823):
        :meth:`_record_to_begin_end` returns a 3-tuple whose chrom this loop
        drops on the next line, and :meth:`get_score_values_from_record` is a
        method call around a comprehension over defs already resolved for the
        whole region.  Both remain, unchanged, for their other callers -- what
        is removed is two objects and a call per record, not the surface.  The
        ordering refusal they carried is kept, in place, as one comparison;
        see ``test_segment_path_refuses_a_backwards_record``.
        """
        extract = self._extract_value
        for record in records:
            rec_begin = record[POS_BEGIN]
            rec_end = record[POS_END]
            if rec_end < rec_begin:
                raise self._inverted_span_error(record)
            yield (rec_begin, rec_end, [
                extract(record, score_def) for score_def in score_defs])

    @abstractmethod
    def validate_records(
        self, records: Iterator[Record],
    ) -> Generator[Record, None, None]:
        """Yield a raw record stream through, refusing a malformed one.

        A **transducer**: it hands back exactly what it was given, in order,
        and raises
        :class:`~gain.genomic_resources.resource_errors.MalformedResourceError`
        at the first record its
        kind cannot mean.  It never re-reads and never materialises the
        region -- the statistics scan pays for one read, and this rides it.

        It reads RAW records rather than the spans a kind yields, because a
        kind's normalization destroys the evidence: an allele score collapses
        a record to the point it sits at, discarding its end entirely.  Raw
        is also the only layer at which this and the vectorized validator
        can state one rule (ADR 0008).

        Every kind states this itself; there is deliberately no default to
        inherit.  A kind that inherited one would be validated by a rule
        nobody chose for it, and a rule stated once for kinds that mean
        different things is what gain#585 is unwinding.
        """
        raise NotImplementedError

    @abstractmethod
    def validate_record_arrays(
        self, batches: Iterator[RecordArrays], chrom: str,
    ) -> Generator[RecordArrays, None, None]:
        """Yield a stream of raw column batches through, refusing a bad one.

        The vectorized counterpart of :meth:`validate_records`, and the same
        transducer shape over the batches the bulk scan is already pulling.
        It states the SAME ordering rule as its per-record twin -- both read
        the raw begins and ends, which is the only layer at which they can --
        so a resource whose records are out of order is refused identically
        whichever path it was eligible for.  Divergence between the two is
        what ADR 0008 records as the reason the shared class attribute was
        removed.

        The ordering rule is all it states.  The per-record path additionally
        refuses a record whose end precedes its begin (the message is
        ``_inverted_span_error``); there is no array counterpart, because
        no backend the bulk path reads can produce one (tabix refuses to index
        such a row, and a bigWig cannot express it).  If that ever stops being
        true, this is where the check belongs.

        ``chrom`` is what the batches were read for.  A bulk scan reads one
        region, which lies within one contig, so the implementations carry
        their ordering state across batches but never across contigs.

        Every kind states this itself; there is deliberately no default.
        """
        raise NotImplementedError

    def fetch_region_segments(
        self,
        chrom: str,
        pos_begin: int | None = None,
        pos_end: int | None = None,
        scores: list[str] | None = None,
    ) -> Generator[
            tuple[int, int, list[ScoreValue]], None, None]:
        """Yield ``(begin, end, values)`` per record touching the region.

        One tuple per underlying RECORD -- a segment, at that record's own
        extent -- not one value per position.  The region's records, read as
        this kind means them.  A record straddling the region's edge is
        reported whole: what a partial overlap means depends on what the
        caller is computing, so a caller answering a question about the
        window composes :func:`~.records.clip_to_region` over this stream, or
        calls :func:`~.records.clip_span` per segment (ADR 0008).

        A plain read: it checks nothing.  The statistics scan reads the same
        records through the same transform with :meth:`validate_records`
        composed in front, and that extra link -- visible at the consumer,
        in ``genomic_scores_impl/scan.py`` -- is the whole of the
        difference
        between the two (ADR 0008).

        One body per kind, in :meth:`region_values_from_records`, rather than
        one per kind per consumer: two that had to agree is how the paths
        drift.
        """
        return self.region_values_from_records(
            self.fetch_records(chrom, pos_begin, pos_end),
            chrom, pos_begin, pos_end, scores)

    def fetch_region_segment_scores(
        self,
        chrom: str,
        pos_begin: int | None = None,
        pos_end: int | None = None,
        scores: list[str] | None = None,
    ) -> Generator[
            tuple[int, int, list[ScoreValue]], None, None]:
        """Yield ``(begin, end, values)`` per record, clipped to the region.

        .. deprecated::
            Use :meth:`fetch_region_segments` instead -- the same read,
            reporting each record's own extent instead of reshaping it to
            the queried window.  Retained because published callers hold
            the clipped spans (``docs/source/python_interface.rst``);
            removal is tracked as gain#844.

        The body is the worked example of composing the region transducer:
        the unclipped segment stream, with :func:`~.records.clip_to_region`
        deciding what a partial overlap means.
        """
        warnings.warn(
            _SEGMENT_SCORES_DEPRECATION
            + "The replacement reports each record's own extent instead "
            "of clipping it to the queried window.",
            DeprecationWarning,
            stacklevel=2,
        )
        return clip_to_region(
            self.fetch_region_segments(chrom, pos_begin, pos_end, scores),
            pos_begin, pos_end)

    def fetch_region_values(
        self,
        chrom: str,
        pos_begin: int | None = None,
        pos_end: int | None = None,
        scores: list[str] | None = None,
    ) -> Generator[
            tuple[int, int, list[ScoreValue]], None, None]:
        """Yield ``(begin, end, values)`` per record touching the region.

        .. deprecated::
            Use :meth:`fetch_region_segments` -- note it reports each
            record's own extent, where this alias yields the record
            clipped to the queried window (compose
            :func:`~.records.clip_to_region` over it where the clipped spans
            matter).  Retained as a thin compatibility alias only because the
            published
            ``docs/source/python_interface.rst`` showed this name to
            external readers; no in-tree or known cross-repo caller
            remains.  Removal is tracked as gain#730.
        """
        warnings.warn(
            "GenomicScore.fetch_region_values is deprecated; use "
            "fetch_region_segments (unclipped; compose clip_to_region "
            "over it for the clipped spans this alias yields). It is "
            "retained only for readers of the published documentation, "
            "until gain#730 removes it.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.fetch_region_segment_scores(
            chrom, pos_begin, pos_end, scores)

    @classmethod
    @abstractmethod
    def record_weight(cls, left: int, right: int) -> int:
        """How many times one record's value counts when aggregating.

        The rule is a property of the resource TYPE, and ``WeightedValues``
        already states it: "a position-score record counts once per base
        pair of the queried region it covers, an allele line counts once, a
        fragment counts once however long it is".  One record, one count,
        is the answer for everything except a position score.

        **The kind's single statement of that rule**, and every reader goes
        through it: :meth:`aggregate_region` folds with it, the per-record
        statistics scan calls it, and the bulk scan broadcasts it over a
        whole batch through :meth:`record_weights`.  One statement, so a
        kind cannot weigh its records one way when annotating and another
        when computing statistics.  Pinned by
        test_the_weight_rule_is_stated_once_per_kind.

        **An implementation must be numpy-elementwise** -- an arithmetic
        expression over ``left`` and ``right``, or a constant.  It is
        declared over scalars because that is what its per-record callers
        hand it, but :meth:`record_weights` answers a whole batch by
        handing it the position COLUMNS instead, and only an elementwise
        body gives the same answers that way.

        Most ways of breaking that break loudly -- a body branching on
        ``left`` raises ``ValueError`` on an array's ambiguous truth, one
        calling ``int()`` a ``TypeError``.  The contract is written down
        for the ones that do not: a body REDUCING its arguments
        (``int(np.mean(right - left + 1))``) hands back a plain number,
        which is then broadcast as though it were every record's weight,
        and the two scan paths disagree with nothing raised.  That is what
        test_the_weight_rule_is_stated_once_per_kind's broadcast-agreement
        assertion is there to catch.

        Deriving a weight from the span unconditionally is what this hook
        exists to prevent: it would give a fragment its length as a weight
        and disagree with the fragment score annotator for every fragment
        longer than one base pair.
        """
        raise NotImplementedError

    @classmethod
    def record_weights(
        cls, begins: np.ndarray, ends: np.ndarray,
    ) -> np.ndarray:
        """:meth:`record_weight` over a whole batch's position columns.

        The bulk statistics scan has no record to hand the scalar hook, so
        it weighs a batch here instead.  This does not restate the rule --
        it broadcasts the ONE statement of it, which is why the scan may
        not read the weight anywhere else.

        The widening is the elementwise contract being spent: the hook is
        declared over scalars and its bodies are arithmetic, so the same
        expression answers a column.  A kind whose weight is a CONSTANT
        answers with that constant however it was called, so a 0-d result
        is filled out to the batch's shape rather than treated as an
        error.
        """
        # One marker for one fact -- the scalar-declared hook is being
        # called on whole columns, which is the contract its docstring
        # states and this method exists to spend.
        weights = np.asarray(
            cls.record_weight(begins, ends))  # type: ignore[arg-type]
        if weights.ndim == 0:
            weights = np.full(begins.shape, weights)
        return weights.astype(np.int64, copy=False)

    def aggregate_region(
        self,
        chrom: str,
        pos_begin: int | None = None,
        pos_end: int | None = None,
        scores: list[str | tuple[str, str]] | None = None,
    ) -> list[ScoreValue]:
        """Reduce a region to one value per requested score.

        The aggregating counterpart of :meth:`fetch_region_segments`,
        which it is built on: that method yields one entry per record, this
        one folds
        those entries into a single value per request.

        Each request is either a score id -- aggregated with the resource's
        own default, which ``score_def.finish_scoredefs`` resolved from
        this class's ``DEFAULT_AGGREGATORS`` -- or a ``(score_id, aggregator)``
        pair naming one explicitly.  The aggregator string is whatever the
        config accepts, parametrized forms (``join(,)``) included.

        **Returns a list parallel to ``scores``, not a dict.**  One score
        may legitimately be requested twice with different aggregators --
        ``["s", ("s", "max")]``, which is what an annotation config does
        when it exposes one source as both a min and a max attribute -- and
        a dict keyed by score id would silently drop one of them.

        An empty region is not an error: each aggregator answers for
        itself.  ``list`` returns ``[]``; ``max`` returns ``None``; and so
        does ``count``, which chooses to report nothing rather than 0 for an
        empty region (see ``CountAggregator.get_final``).  This method does
        not second-guess any of them.  That is deliberately unlike the
        per-position reads (``fetch_position_scores``,
        ``fetch_allele_scores``), which return ``None`` where there is no
        data -- aggregating nothing is a well-defined question, reading a
        value where there is none is not.

        Values reach the aggregator exactly as the record carried them,
        ``None`` included, because that is what the annotators do (each
        aggregator decides what a null means for it) and the point of this
        method is to give the answer they would.
        """
        requests = resolve_aggregator_requests(
            scores,
            score_definitions=self.score_definitions,
            all_scores=self.get_all_scores(),
            resource_id=self.resource_id,
        )
        # Built BEFORE the fetch, because the fetch is not lazy: the
        # not-open and unknown-contig guards of fetch_region_segments run
        # when it is CALLED, not on the first next().  An aggregator built
        # afterwards would have a misspelled name reported only for the
        # regions a resource happens to cover, so `mediann` would be a
        # missing-contig error until someone queried a covered contig.
        aggregators = [
            build_region_aggregator(
                score_id, aggregator, resource_id=self.resource_id)
            for score_id, aggregator in requests
        ]
        return fold_region_segments(
            self._aggregation_segments(
                chrom, pos_begin, pos_end, distinct_score_ids(requests)),
            aggregators,
            requests,
            weigh=self.record_weight,
        )

    def _aggregation_segments(
        self,
        chrom: str,
        pos_begin: int | None = None,
        pos_end: int | None = None,
        scores: list[str] | None = None,
    ) -> Iterator[tuple[int, int, list[ScoreValue]]]:
        """The segment stream :meth:`aggregate_region` folds.

        The records as this kind means them, which for everything but a
        position score is :meth:`fetch_region_segments` unchanged: a kind
        that counts a record ONCE counts it wherever the point it collapses
        to falls, window or not (see
        test_an_allele_point_outside_the_window_still_aggregates_once).

        Clipping the records to the window first is a position-score fact,
        so it is stated on :class:`~.position.PositionScore` and nowhere
        else.  This hook is what lets it be: without it the fold would have
        to carry a flag saying which kind it is serving, and that flag
        would be a second statement of the weight rule.

        Underscored, alone among the per-kind hooks, because it is the only
        one that is not also part of the read API: the others answer a
        caller (``fetch_region_segments`` IS
        :meth:`region_values_from_records`; the scan calls
        :meth:`validate_records` and :meth:`record_weight` by name), while
        this one is reached only from :meth:`aggregate_region` in this
        class.  A kind overrides it; nothing else calls it.
        """
        return self.fetch_region_segments(chrom, pos_begin, pos_end, scores)
