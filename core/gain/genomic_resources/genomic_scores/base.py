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
region-aggregation machinery to :mod:`.aggregation`, which the per-kind
aggregating reads drive, each handing it the kind's weight rule; its third
(gain#1114) moved the value-extraction seam -- picking the per-record read,
and addressing each score def to a payload column -- to
:mod:`.value_extraction`, leaving
:meth:`GenomicScore.open` calling both in the order that seam requires and
the two public per-record getters here.  The remaining seams are #1027's
other children.

A score's defs are **finished in place at open**:
:func:`~.value_extraction.resolve_score_indices` writes ``score_index`` onto
the definitions this class already holds rather than handing back new ones
(it says there who reads them).  The earlier half of the lifecycle differs:
``finish_scoredefs`` runs inside :meth:`GenomicScore._build_scoredefs`,
before there is a ``score_definitions`` to write onto, and so returns the
mapping.
"""

# The numpy docstrings of the public members and the design comments they
# displaced (gain#1913) keep this module over pylint's 1500-line cap until
# gain#1027 finishes decomposing the class.
# pylint: disable=too-many-lines

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Generator, Iterator, Sequence
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
    build_bigwig_scoredefs,
    validate_bigwig_scoredefs,
)
from gain.genomic_resources.genomic_position_table import (
    ChromLengthSource,
    PayloadKind,
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
from gain.genomic_resources.resource_errors import inverted_span_error
from gain.genomic_resources.score_def import (
    BULK_PARSEABLE_VALUE_TYPES,
    GenomicScoreDef,
    ScoreValue,
    ValueExtractor,
    build_genomic_score_schema,
    finish_scoredefs,
    parse_scoredef_config,
    refuse_unbuildable_aggregators,
    validate_scoredefs,
)
from gain.genomic_resources.score_filter import (
    ScoreFilter,
    compile_score_filter,
    select_records,
)
from gain.genomic_resources.score_resource import (
    ScoreResource,
    refuse_unfoldable_histograms,
)
from gain.genomic_resources.vcf_scores import (
    parse_vcf_scoredefs,
)

from .batch_budget import value_arrays_batch_rows
from .chrom_lengths import (
    ChromLength,
    ChromLengthAnswer,
    StoredChromLengths,
    as_chrom_length_source,
    best_chrom_length,
    derive_chrom_length,
    derive_chrom_lengths,
    load_current_chrom_lengths,
    refuse_unanswered_source,
)
from .records import (
    RecordArrays,
)
from .value_extraction import (
    resolve_score_indices,
    select_value_extractor,
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


# Notes for the authors of a score kind, moved here from the class docstring
# (gain#1913) so that the rendered page describes the user API only.
#
# A kind whose records read as something other than the span they cover
# states that ONCE, by overriding:
#
# - _score_segments(): what a region's raw records mean for this kind.
#   ``values_from_records`` is the request resolution followed by it,
#   ``fetch_region_segments_scores`` is THAT applied to ``fetch_records``,
#   and the statistics scan is it applied to
#   ``validate_records(score, fetch_records(...))`` -- so a kind states its
#   reading once and every consumer gets it (ADR 0008).  Override this and
#   not ``values_from_records``: the resolving entry is shared by every
#   kind, and a read holding an already-resolved request composes this body
#   without going through it (gain#1282).
# - record_weight(): how many times one record's value counts when a region
#   is aggregated.  Every reader goes through it -- the allele and fragment
#   aggregating reads (``get_allele_scores_in_region_agg``,
#   ``get_fragment_scores_overlapping_region_agg``), the per-record scan,
#   and the bulk scan via ``record_weights`` (gain#1095).
#
# Neither has a default.  A kind that inherited one would be
# weighed by a rule nobody chose for it, which is the failure ADR 0008
# exists to undo.
#
# A kind's two validation rules are not on this list, and not on this class:
# they are registered per kind in
# :mod:`gain.genomic_resources.statistics.record_validation`, whose default
# refuses a kind nobody wrote one for (ADR 0027).
#
# Threading: a score is not safe for concurrent use.  Each factory call
# returns a fresh instance its caller owns; a holder that shares one across
# threads serialises every read, open() and close() itself (see close()).
class GenomicScore(ScoreResource[GenomicScoreDef]):
    """Base class of the position, allele and fragment scores.

    A genomic score gives access to the values of a genomic resource that
    attaches scores to places in the genome. The class owns what the three
    kinds share: the score definitions, the open and close lifecycle, the
    filter compiler, and the record and column-array reads. A score is built
    closed, so call :meth:`open` before the first read.

    The data of the resource is a tabix file, a VCF, a bigWig or an
    in-memory table. A score is not safe for concurrent use.

    Attributes:
        resource (GenomicResource): The genomic resource of the score.

        resource_id (str): The id of the resource.

        config (dict): The validated and normalized configuration.

        table (GenomicPositionTable): The table that holds the data.

        score_definitions (dict[str, GenomicScoreDef]): The definitions of
            the scores, keyed by score id.

        table_loaded (bool): ``True`` while the table is open.

        DEFAULT_AGGREGATORS (dict[str, str | None]): The aggregator that
            each kind of score value gets when its config states none. The
            value types of a score are the keys. Each kind of score sets it.

    See Also:
        PositionScore, AlleleScore, FragmentScore
    """

    # How a value is read off a record.  Installed by :meth:`open`, from the
    # table's ``payload_kind``, and declared here with NO default on
    # purpose: a record's payload means different things -- a raw row, a
    # VCF (variant, allele index) pair, a bigWig value -- so no single
    # extractor reads them all, and a default would have to be wrong for
    # one of them.  Unset until open()
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
        super().__init__(resource)
        self.resource_id = resource.resource_id
        self.config["id"] = resource.resource_id
        self.table_loaded = False
        self.table = build_genomic_position_table(
            self.resource, self.config["table"],
        )
        self.score_definitions = self._build_scoredefs()
        # Loaded by open() when current, dropped by close(); None: live.
        self._stored_chrom_lengths: StoredChromLengths | None = None

    @staticmethod
    def get_schema() -> dict[str, Any]:
        """Return the Cerberus schema of the resource config.

        Each kind of score extends this schema.

        Returns
        -------
        dict[str, Any]
            The schema of the config that this kind of score accepts.

        :meta private:
        """
        return build_genomic_score_schema()

    def _build_scoredefs(self) -> dict[str, GenomicScoreDef]:
        """Route this resource's definitions through their construction path.

        The one piece of the scoredef lifecycle that did NOT move to
        :mod:`~gain.genomic_resources.score_def` in gain#1044: it dispatches
        on the table's payload_kind into ``parse_vcf_scoredefs`` and
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
        kind = self.table.payload_kind
        if kind is PayloadKind.VARIANT:
            merge = bool(self.config.get("merge_vcf_scores", False))

            scoredefs = parse_vcf_scoredefs(
                cast(dict[str, Any], self.table.header),
                config_scoredefs,
                resource_id=self.resource_id,
                merge=merge)
        elif config_scoredefs is None:
            raise ValueError("No scores configured and not using a VCF")
        elif kind is PayloadKind.VALUE:
            scoredefs = build_bigwig_scoredefs(self.config, config_scoredefs)
        else:
            scoredefs = config_scoredefs

        # Both refusals come AFTER finish_scoredefs -- the histogram one
        # because an unstated ``type:`` becomes ``float`` there, and
        # earlier would judge a score by a type it does not end up with;
        # the aggregator one so that what is built is the spelling the
        # score will reduce by, class default included -- and at the
        # convergence of all three construction routes, so a ``scores:``
        # block, a VCF header and a bigWig are held to the one rule
        # (gain#1336).
        scoredefs = finish_scoredefs(scoredefs, self.DEFAULT_AGGREGATORS)
        scoredefs = refuse_unfoldable_histograms(scoredefs, self.resource_id)
        return refuse_unbuildable_aggregators(scoredefs, self.resource_id)

    def get_config(self) -> dict[str, Any]:
        """Return the configuration of the score.

        Returns
        -------
        dict[str, Any]
            The configuration of the resource, as validated and normalized
            when the score was built. It is the object the score holds, not a
            copy.
        """
        return self.config

    def get_default_annotation_attributes(self) -> list[Any]:
        """Return the default annotation attributes of the resource.

        Returns
        -------
        list[Any]
            The ``default_annotation`` entries of the config. When the config
            has none, one entry ``{"source": id, "name": id}`` for each score.

        Raises
        ------
        TypeError
            If ``default_annotation`` of the config is not a list.

        :meta private:
        """
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
        """Return the default annotation attribute name of a score.

        Parameters
        ----------
        score_id : str
            The id of a score of the resource.

        Returns
        -------
        str | None
            The attribute name that the default annotation gives to the
            score. Several attributes of one score are joined by a comma.
            ``None`` if the default annotation does not include the score.

        :meta private:
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

    # A score has one owner: every factory in ``genomic_scores.builders``
    # returns a fresh instance, so closing it affects nobody else, and no method
    # of this class synchronises anything.  gain-web-api's
    # ``ThreadSafePipeline`` is a holder that serialises reads, open() and
    # close() behind one lock per pipeline.  The in-memory scan and the bigWig
    # fetch refuse rather than return partial results when the table is released
    # under a reader, which makes the failure loud; it does not prevent it.
    def close(self) -> None:
        """Close the underlying table and mark the score not open.

        :meth:`open` can run again afterwards. Do not call ``close`` while
        another thread reads the score: a reader that passed :meth:`is_open`
        can find the table released. A holder that shares one score across
        threads must serialise the reads, ``open`` and ``close`` itself.
        """
        self.table.close()
        self.table_loaded = False
        self._stored_chrom_lengths = None

    def is_open(self) -> bool:
        """Report whether the score is open.

        Returns
        -------
        bool
            ``True`` if :meth:`open` has run and :meth:`close` has not run
            since. The answer is a snapshot for the owner of the score. It
            does not protect against a :meth:`close` in another thread.
        """
        return self.table_loaded

    # Validate and route BEFORE opening, and so before publishing.  Every input
    # to both steps is known at construction -- the table's ``payload_kind``
    # ClassVar, and the score definitions -- so neither needs the open handle,
    # and two things fall out of that order:
    #
    # * a refusal costs no handle.  Routing after ``table.open()`` would leave a
    #   caller that is not using the ``with`` form holding an opened pysam
    #   handle it cannot reach: ``table_loaded`` would still be False, so
    #   ``close()`` would not have been reached.  Raising first means there is
    #   nothing to leak.  The bigWig config validation sits here for exactly
    #   that reason.
    # * ``table_loaded = True`` is what makes this score report itself open:
    #   from that write on, a second open() takes the is_open() early return and
    #   its caller reads ``_extract_value`` straight away.  Routing first means
    #   a score that says it is open always has its extractor -- the routing has
    #   no default at all, so the other order would leave an AttributeError for
    #   anything that trusted is_open() in between.  Pinned by
    #   test_the_score_is_routed_before_it_reports_itself_open.
    #
    # None of this makes open() safe to race: ``resolve_score_indices`` still
    # runs after the score has published itself open.  A score is not
    # synchronised and does not claim to be -- a holder sharing one across
    # threads serialises open(), reads and close() itself (see close()).
    def open(self) -> Self:
        """Open the score for reading and return it.

        Call ``open`` before the first read. Entering a ``with`` block does
        not open the score. Opening an open score does nothing.

        Returns
        -------
        GenomicScore
            This score, so the call can chain: ``score.open()``.

        Raises
        ------
        ValueError
            If the score definitions do not fit the table, for example when
            a configured column does not exist.
        """
        if self.is_open():
            logger.info(
                "opening already opened genomic score: %s",
                self.resource.resource_id)
            return self
        kind = self.table.payload_kind

        if kind is PayloadKind.VALUE:
            validate_bigwig_scoredefs(
                self.resource_id, self.score_definitions)
        self._extract_value = select_value_extractor(
            score_definitions=self.score_definitions,
            table=self.table)

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
        #
        # A VCF's is skipped too: its scores have no column address to
        # check, and what an entry may say about one was already refused
        # where the definitions were built (``_refuse_overridden_address``
        # says why).  The ROW route always has a ``scores:`` block --
        # ``_build_scoredefs`` refuses a non-VCF resource without one.
        if kind is PayloadKind.ROW:
            validate_scoredefs(self.config, self.table, self.resource)
        resolve_score_indices(
            self.score_definitions,
            table=self.table,
            resource_id=self.resource_id)
        self._stored_chrom_lengths = load_current_chrom_lengths(self)

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

    def _get_header(self) -> tuple[Any, ...] | None:
        assert self.table is not None
        return self.table.header

    # See :func:`~gain.genomic_resources.score_filter.compile_score_filter` for
    # what compiling settles,
    # ``docs/adr/0017-score-filtering-is-a-score-capability.md`` for why the
    # capability sits on the score, and
    # ``docs/adr/0018-score-filter-grammar-extension.md`` for the language's
    # precedence and what a name may contain.
    def compile_filter(self, expression: str) -> ScoreFilter:
        """Compile a boolean expression into a filter over this score.

        The expression names scores of this resource and relates them with
        ``>``, ``>=``, ``<``, ``<=``, ``==``, ``!=`` and ``in``. The
        relations combine with ``not``, ``and`` and ``or``. Pass the result
        to a read as ``score_filter``.

        Parameters
        ----------
        expression : str
            The filter expression, for example ``"phastCons100way > 0.9"``.

        Returns
        -------
        ScoreFilter
            A filter bound to this score. The reads of another score refuse
            it.

        Raises
        ------
        ScoreFilterError
            If the expression does not parse, or if it names a score that
            the resource does not define.
        """
        return compile_score_filter(self, expression)

    # Nothing here is checked before the first ``next()``, the filter's
    # ownership included: every backend's ``get_records_in_region`` is itself a
    # generator function, so an unknown contig has always been reported from the
    # first record read rather than from the call, and there is no eagerness
    # left to preserve by structuring this any other way.  That is a property of
    # *this* read rather than a rule for the family: the allele reads check
    # their request when called, and
    # ``AlleleScore.get_allele_scores_in_region_agg`` refuses an unknown contig
    # from the call. ``score_filter=None`` yields what the table yields, which
    # is all that this method did before filtering became a score capability.
    def fetch_records(
        self,
        chrom: str,
        pos_begin: int | None,
        pos_end: int | None,
        *,
        score_filter: ScoreFilter | None = None,
    ) -> Generator[Record, None, None]:
        """Yield the records of a region, optionally filtered.

        Read the positional fields of a record from its slots
        (``record[CHROM]``, ``record[POS_BEGIN]``, ...). Read a score value
        with :meth:`get_score_value_from_record`. The generator checks
        nothing before the first ``next()``: a closed score, an unknown
        contig and a filter of another score are all reported there, not
        from the call.

        Parameters
        ----------
        chrom : str
            The chromosome name. It is required. To read every record of the
            table, use ``score.table.get_all_records()``.
        pos_begin : int | None
            The start of the region. ``None`` means no lower bound.
        pos_end : int | None
            The end of the region. ``None`` means no upper bound.
        score_filter : ScoreFilter | None, default None
            A filter from :meth:`compile_filter`. When it is not ``None``,
            the generator yields only the records that the filter accepts.

        Yields
        ------
        Record
            The records that overlap the region, in table order. A region
            without records yields nothing.

        Raises
        ------
        ValueError
            If the score is not open, or if the table does not hold
            ``chrom``.
        ScoreFilterError
            If ``score_filter`` was compiled by another score.
        """
        records = self.table.get_records_in_region(chrom, pos_begin, pos_end)
        yield from select_records(self, records, score_filter)

    def get_score_value_from_record(
        self, record: Record, score_id: str,
    ) -> ScoreValue:
        """Read one score off a record of this score's table.

        Parameters
        ----------
        record : Record
            A record from :meth:`fetch_records` of this score.
        score_id : str
            The id of a score of the resource.

        Returns
        -------
        ScoreValue
            The parsed value. ``None`` if the cell holds no value: a missing
            cell, an NA sentinel of the score, or a cell that does not parse.

        Raises
        ------
        KeyError
            If the resource does not define ``score_id``.
        """
        return self._extract_value(record, self.score_definitions[score_id])

    def _resolve_score_defs(
        self, scores: Sequence[str] | None,
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
        self._guard_score_ids(scores)
        return [self.score_definitions[score_id] for score_id in scores]

    def _resolve_single_score(self, score: str | None) -> str:
        """Resolve a singular method's ``score`` argument to one score id.

        ``None`` means "all the scores this resource has", which a singular
        method can honour only when there is exactly one.

        Here rather than on one kind: the rule is about what a SINGULAR
        read can resolve, which is a property of the score rather than of
        the positions it is read at, and the position, fragment and allele
        planes' singular reads all resolve their ``score`` through it.
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

    # A caller resolves score names to definitions once per fetch and passes
    # them per record, so the name->definition lookup stays out of the
    # per-record loop.
    def get_score_values_from_record(
        self, record: Record, score_defs: list[GenomicScoreDef],
    ) -> list[ScoreValue]:
        """Read several scores off one record, for resolved definitions.

        The bulk form of :meth:`get_score_value_from_record`.

        Parameters
        ----------
        record : Record
            A record from :meth:`fetch_records` of this score.
        score_defs : list[GenomicScoreDef]
            The definitions of the scores to read, taken from
            ``score_definitions``.

        Returns
        -------
        list[ScoreValue]
            One value for each definition, in the order of ``score_defs``. A
            value is ``None`` if the cell holds no value.

        :meta private:
        """
        extract = self._extract_value
        return [extract(record, score_def) for score_def in score_defs]

    # Answers the two things a caller can be wrong about: the backend serves the
    # bulk column-array read, AND every named score is one this facade can
    # parse. A predicate that answered only the first would say True for a call
    # that then refuses -- not a capability query but a trap.
    #
    # It is not a promise the call cannot fail for some OTHER reason.  A score
    # whose configured column index does not exist in its backend's payload
    # still raises (deliberately -- see ``BigWigTable``), and so does a closed
    # score or an unknown contig.  This answers "is this score the kind this
    # method serves", not "is every argument valid".
    #
    # The value-type half is not a consumer's condition leaking in: the facade
    # parses, so it serves the value types ``GenomicScoreDef.parse_array``
    # defines a column parse for (``BULK_PARSEABLE_VALUE_TYPES``) and no others.
    # What a *consumer* additionally needs stays with the consumer: the
    # statistics scan also requires a bounded region, and each of its two entry
    # points asks for its own histogram or min/max pairing (see
    # ``genomic_scores_impl.scan.can_bulk_histogram`` and ``can_bulk_min_max``).
    # The scan does not re-test the resource KIND; ADR 0001 records why.  What
    # it does NOT require is a particular record shape: the accumulator reads
    # the kind's own ``record_weight`` and the scan's door reads the rule
    # registered for the kind in ``statistics.record_validation``, so a
    # position, allele and fragment score are all served.
    #
    # Answerable on an UNOPENED score: the table and the score definitions are
    # both built in ``__init__``, so nothing here touches the file.
    def supports_region_value_arrays(self, scores: list[str]) -> bool:
        """Report whether :meth:`fetch_region_value_arrays` serves these scores.

        The answer is ``True`` only if the backend serves the column-array
        read and every named score has a value type that the array parse
        supports. The method works on a score that is not open.

        Parameters
        ----------
        scores : list[str]
            The ids of the scores to read.

        Returns
        -------
        bool
            ``True`` if :meth:`fetch_region_value_arrays` accepts these
            scores. ``False`` if the backend has no array read, if a score id
            is unknown, or if a score has a value type that the parse does
            not support. An empty list gives the answer of the backend alone.
        """
        if not self.table.supports_value_arrays:
            return False
        for score_id in scores:
            score_def = self.score_definitions.get(score_id)
            if score_def is None \
                    or score_def.value_type not in BULK_PARSEABLE_VALUE_TYPES:
                return False
        return True

    def _require_open(self) -> None:
        """Refuse any read of a score that is not open.

        The one home of this message.  Reads that also need a known contig
        take :meth:`_require_open_and_known_chrom`; the two aggregating
        reads of :class:`~.position.PositionScore`, which accept a contig
        the score never mentions, take this one alone (gain#1211).
        """
        if not self.is_open():
            raise ValueError(f"genomic score <{self.resource_id}> is not open")

    def _require_open_and_known_chrom(self, chrom: str) -> None:
        """Refuse a region read this score cannot answer at all.

        The two conditions every bulk column read shares, stated once for
        the readers that widen it.  Several OLDER reads in this module spell
        the contig half out inline; they are left as they are rather than
        swept into this change, and a few of them word it differently on
        purpose (an allele read names the resource in it).
        """
        self._require_open()
        if not self.has_chromosome(chrom):
            raise ValueError(
                f"{chrom} is not among the available chromosomes.")

    def _guard_region_span(self, start: int, end: int) -> None:
        """Refuse a span no genomic region can mean.

        A 1-based lower bound and an end that does not precede its start.
        There is deliberately no UPPER bound check: the exact chromosome
        length is not knowable for most scores, and a position past the end
        of the data is simply uncovered (see #727).

        Here rather than on the one kind that spelled it first: the rule is
        about what a REGION can be, which is a property of neither the
        positions a score is read at nor the records it holds.  Two kinds
        call it -- the position and fragment logical planes, whose region
        bounds are mandatory ints.  ``AlleleScore`` does not, and that is
        not an omission: it has no logical plane, and its record reads take
        ``int | None`` bounds where ``None`` legitimately means unbounded.

        It also stops a backend's own leniency from reaching a reader --
        the in-memory table tests its bounds for truthiness, so a ``0``
        would otherwise arrive as "unbounded" and answer a caller error
        with a whole contig.

        Called by each read rather than folded into
        :meth:`_region_read_defs`, the seam the shared
        :meth:`values_from_records` runs for every kind -- which sees no
        position at all, being a transform over records already fetched.
        Folding it in would first have to hand the window back to the
        record path, and would then refuse ``fetch_*`` requests that are
        accepted today, on all three kinds at once, which is a behaviour
        change no reader of this slice asked for.  Until that is decided, a
        read that takes a mandatory region calls this first.
        """
        if start < 1:
            raise ValueError(
                f"genomic score <{self.resource_id}> asked for a region "
                f"with start {start}; positions are 1-based")
        if end < start:
            raise ValueError(
                f"genomic score <{self.resource_id}> asked for a region "
                f"whose end {end} precedes its start {start}")

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

    # Parsing is the reason a value type the definition cannot parse as a column
    # is refused, and the reason supports_region_value_arrays asks about the
    # scores and not only about the backend.  The agreement of ``parse_array``
    # with the per-value ``parse_value`` is pinned by
    # test_parse_array_agrees_with_parse_value_fuzz.
    #
    # Not clipping is deliberate: what a partial overlap means depends on what
    # the caller is computing.
    #
    # ``batch_size`` is a HINT: a batch also fetches at most
    # ``VALUE_ARRAYS_CELL_BUDGET`` raw cells.  The budget counts the distinct
    # columns FETCHED; score ids sharing one payload column still get a parsed
    # array each, so it does not bound those (see
    # ``batch_budget.value_arrays_batch_rows()``).  A parse builds one array per
    # id, so two ids sharing a payload column do not alias.
    #
    # The guards below run when this method is CALLED, not on the first
    # ``next()`` -- which is why the streaming half lives in
    # ``_value_array_batches`` rather than a ``yield`` here.
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
        """Fetch a region as column arrays, without a record for each row.

        The bulk form of :meth:`fetch_records`, for a caller that scans a
        whole region and wants columns. Call
        :meth:`supports_region_value_arrays` first. The method checks its
        arguments when it is called, not at the first ``next()``.

        The values are parsed by
        :meth:`~gain.genomic_resources.score_def.GenomicScoreDef.parse_array`.
        The dtype of an array follows the value type of the score, not the
        backend: a ``float`` or ``int`` score gives ``float64`` with ``nan``
        for no value, and a ``str`` score gives an ``object`` array with
        ``None``. The method does not clip: a record that overlaps the start
        of the region arrives whole.

        Parameters
        ----------
        chrom : str
            The chromosome name.
        pos_begin : int | None
            The start of the region. ``None`` means no lower bound.
        pos_end : int | None
            The end of the region. ``None`` means no upper bound.
        scores : list[str]
            The ids of the scores to read. Each id gets an array of its own.
        batch_size : int, default 100000
            A hint for the rows of a batch. It is a ceiling: a batch also
            stays within a budget of raw cells, so a read of many scores gets
            fewer rows. A bigWig backend sizes its batches itself and
            ignores the hint.

        Returns
        -------
        Generator[RecordArrays, None, None]
            Batches of ``(pos_begin, pos_end, {score_id: values})``. The
            first two items are one-based position arrays. The generator
            yields nothing for a region without records.

        Raises
        ------
        TypeError
            If :meth:`supports_region_value_arrays` is ``False`` for
            ``scores``.
        ValueError
            If the score is not open, or if the table does not hold
            ``chrom``.
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
                chrom, pos_begin, pos_end, wanted,
                value_arrays_batch_rows(batch_size, len(wanted))):
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
        """Return the chromosome names that the score's table holds.

        Returns
        -------
        list[str]
            The names, in table order.

        Raises
        ------
        ValueError
            If the score is not open.
        """
        self._require_open()
        return self.table.get_chromosomes()

    # The yes/no half of ``get_all_chromosomes``, and what every contig screen
    # in the read path asks -- the annotators' pre-read screens, the shared
    # region-read refusal, the position kind's absent-contig branch and both
    # allele reads' refusals.  All of them spelled ``chrom not in
    # self.get_all_chromosomes()``, a walk of the ordered list whose cost grew
    # with the resource's contig count and with the contig's index in it, and
    # grew WORST for a contig the resource does not carry (gain#1304).
    #
    # It raises on a score that is not open, exactly as ``get_all_chromosomes``
    # does and for the same reason: those screens got that refusal for free from
    # the accessor they used, and a predicate that answered ``False`` instead
    # would turn "this score was never opened" into "this score does not carry
    # that contig" at every one of them.
    #
    # The ordered list keeps its meaning, its order and its aliasing for the
    # callers that genuinely need the collection -- a statistics scan splitting
    # a genome into region tasks, the resource implementation's contig report.
    # This is for the ones that only ever asked a question.
    def has_chromosome(self, chrom: str) -> bool:
        """Report whether the score's table carries a chromosome.

        Parameters
        ----------
        chrom : str
            The chromosome name.

        Returns
        -------
        bool
            ``True`` if the table holds ``chrom``, ``False`` otherwise.

        Raises
        ------
        ValueError
            If the score is not open.
        """
        self._require_open()
        return self.table.has_chromosome(chrom)

    # Declared on the backend class, so answered on a closed score -- which is
    # what a caller weighing whether to open the table for its lengths at all
    # (coverage's second rung, gain#1448) needs.  The lengths themselves come
    # from ``chrom_lengths.derive_chrom_lengths``.
    @property
    def chrom_length_source(self) -> ChromLengthSource:
        """Return the source of chromosome lengths in the score's own table.

        The answer is known without opening the table, so it works on a
        score that is not open.
        """
        return self.table.chrom_length_source

    # Answered on an open score only: which file is current is decided at
    # open(), so a closed score could only guess.
    @property
    def chrom_length_sources(self) -> list[ChromLengthSource]:
        """The sources that the score can answer a length from, best first.

        The sources of the stored lengths file, or the source of the table
        alone. Reading the property on a score that is not open raises
        ``ValueError``.
        """
        self._require_open()
        stored = self._stored_chrom_lengths
        return [self.chrom_length_source] if stored is None else stored.sources

    # Mirrors ``ReferenceGenome.get_chrom_length``: an ``int`` or a
    # ``ValueError``.
    #
    # The lengths come from the ``chrom_lengths.json`` the last repair stored,
    # when open() found it current: the whole ladder, genome rung included, at
    # the cost of a dict lookup.  Without it the table alone answers, live on
    # every call -- on a tabix score the index probe each time -- and
    # ``REFERENCE_GENOME`` is refused until a repair stores it; the score holds
    # no genome of its own.  The refusal for a contig no source answers is
    # worded by the table, which says whether it is empty or unmeasured.
    def get_chrom_length(
        self, chrom: str,
        source: ChromLengthSource | str | None = None,
    ) -> int:
        """Return the length of a chromosome.

        The best-ranked source answers by default. The source tells how far
        to trust the number: only the reference genome and a bigWig header
        are exact. :meth:`get_chrom_length_source` says which source
        answered.

        Parameters
        ----------
        chrom : str
            The chromosome name.
        source : ChromLengthSource | str | None, default None
            The source to answer from, or its string value. ``None`` means
            the best-ranked source.

        Returns
        -------
        int
            The length of ``chrom``.

        Raises
        ------
        ValueError
            If the score is not open, if the table does not hold ``chrom``,
            if ``source`` is none of the four sources, if the source has no
            answer for ``chrom``, or if no source can measure ``chrom``.
            A ``source`` of ``reference_genome`` raises on a score whose
            repair did not store the lengths.
        """
        self._require_open_and_known_chrom(chrom)
        wanted = as_chrom_length_source(source)
        if wanted is None:
            return self._best_chrom_length(chrom).length
        # Refused before the record is resolved: on a live score that is
        # the probe, and a source the score cannot answer needs none.
        self._require_answerable_source(wanted)
        resolved = self._chrom_length_record(chrom)
        if wanted not in resolved.answers:
            refuse_unanswered_source(
                chrom, wanted, resolved,
                table_source=self.chrom_length_source,
                contigs=self.get_all_chromosomes())
        return resolved.answers[wanted]

    # Mirrors ``ReferenceGenome.get_all_chrom_lengths``.  A contig with no
    # answer from the chosen view is left out, never an error -- a caller that
    # needs to know WHY reads ``chrom_lengths.derive_chrom_lengths``.  The cost
    # is that of get_chrom_length, times the contig count.
    #
    # ``REFERENCE_GENOME`` raises on a score with no stored lengths because it
    # is the one source the table can never supply, so an empty answer would
    # hide the repair that fills it.
    def get_all_chrom_lengths(
        self, source: ChromLengthSource | str | None = None,
    ) -> dict[str, int]:
        """Return the length of every chromosome.

        Parameters
        ----------
        source : ChromLengthSource | str | None, default None
            The source to answer from, or its string value. ``None`` means
            the best-ranked source for each chromosome.

        Returns
        -------
        dict[str, int]
            The lengths, keyed by chromosome in table order. A chromosome
            that the chosen view cannot answer is left out. The dict is empty
            if no chromosome has an answer.

        Raises
        ------
        ValueError
            If the score is not open, if ``source`` is none of the four
            sources, or if ``source`` is ``reference_genome`` on a score
            with no stored lengths.
        """
        self._require_open()
        wanted = as_chrom_length_source(source)
        if wanted is not None:
            self._require_answerable_source(wanted)
        records = self._chrom_length_records()
        if wanted is None:
            return {
                chrom: resolved.best.length
                for chrom, resolved in records.items()
                if resolved.best is not None
            }
        return {
            chrom: resolved.answers[wanted]
            for chrom, resolved in records.items()
            if wanted in resolved.answers
        }

    def get_chrom_length_source(self, chrom: str) -> ChromLengthSource:
        """Return the source that answers :meth:`get_chrom_length`.

        Parameters
        ----------
        chrom : str
            The chromosome name.

        Returns
        -------
        ChromLengthSource
            The best-ranked source that has an answer for ``chrom``.

        Raises
        ------
        ValueError
            For the same reasons as :meth:`get_chrom_length`, including a
            chromosome that no source answers.
        """
        self._require_open_and_known_chrom(chrom)
        return self._best_chrom_length(chrom).source

    def _chrom_length_records(self) -> dict[str, ChromLength]:
        """Every contig's record, stored or live; screened by the caller."""
        stored = self._stored_chrom_lengths
        if stored is not None:
            return stored.lengths
        return derive_chrom_lengths(self)

    def _chrom_length_record(self, chrom: str) -> ChromLength:
        """One contig's record, stored or live; screened by the caller --
        the file is loaded only when its contig list is the table's, so
        a contig that screen admitted is in it."""
        stored = self._stored_chrom_lengths
        if stored is not None:
            return stored.lengths[chrom]
        return derive_chrom_length(self, chrom)

    def _best_chrom_length(self, chrom: str) -> ChromLengthAnswer:
        return best_chrom_length(
            chrom, self._chrom_length_record(chrom),
            self.get_all_chromosomes())

    def _require_answerable_source(self, source: ChromLengthSource) -> None:
        """Refuse the genome's rung on a score with no stored lengths:
        the one source that reaches the score only through the file a
        repair stores, so its absence is actionable and said so.  A
        table source the table does not measure simply answers nothing.
        """
        if (source is ChromLengthSource.REFERENCE_GENOME
                and self._stored_chrom_lengths is None):
            raise ValueError(
                f"genomic score <{self.resource_id}> has no stored "
                "chromosome lengths for its reference genome; repair the "
                "resource to store them")

    # Answered by the table, which knows how it opens: the file set has to be
    # known before any of the files is.
    def resource_files(self) -> set[str]:
        """Return the files of the resource that the score reads.

        Returns
        -------
        set[str]
            The data file, and the index file on a backend that reads one.
            The method works on a score that is not open.

        :meta private:
        """
        return self.table.resource_files()

    # The region read expressed as a function OF a record stream, which is what
    # lets the two consumers of a region differ by what they COMPOSE rather than
    # by a flag: ``fetch_region_segments_scores`` is this applied to
    # ``fetch_records``, and the statistics scan is this applied to
    # ``validate_records(score, fetch_records(...))``.  Neither can quietly
    # acquire the other's behaviour, and no argument travels down to say which
    # of the two is reading (ADR 0008).
    #
    # Not to be confused with ``get_score_values_from_record``, which reads ONE
    # record to its ``list[ScoreValue]``; this yields a segment per record, at
    # the record's own extent, and takes no window: nothing is fetched here, and
    # nothing is reshaped either.  What a partial overlap means belongs to the
    # caller (ADR 0008); a consumer answering a question about the window clips
    # with ``records.clip_span``.
    #
    # The guards run when this is CALLED rather than on the first ``next()`` --
    # the pattern ``fetch_records`` documents -- which is why the streaming half
    # lives in ``_score_segments``.  They stay here rather than moving down into
    # ``fetch_records`` because that is not where the request is: half of what
    # they resolve is the score ids, which ``fetch_records`` is not even given,
    # and the contig half would be a THIRD screen of the same contig in one read
    # -- after this one and before the backend's own, which refuses an unknown
    # contig from ``get_records_in_region`` regardless.  This used to be argued
    # on cost, from a time when each screen walked the ordered contig list;
    # since gain#1304 the screen is a set lookup and the argument is only about
    # where a request is resolved, which is here.
    #
    # What a kind yields is ``_score_segments``, and not this method: the
    # resolution is the same for every kind, the reading is not, and since
    # gain#1282 they are split so that a kind states only the half that is its
    # own.  Override THAT to say what a record means here; overriding this one
    # would take the resolution with it, and would be skipped by a read that
    # enters below it holding definitions it has already resolved.
    def values_from_records(
        self,
        records: Iterator[Record],
        chrom: str,
        scores: Sequence[str] | None = None,
    ) -> Generator[
            tuple[int, int, list[ScoreValue]], None, None]:
        """Extract ``(begin, end, values)`` segments from raw records.

        Unlike :meth:`get_score_values_from_record`, which reads one
        record, this method yields one segment for each record. It takes no
        window: it fetches and reshapes nothing.

        Parameters
        ----------
        records : Iterator[Record]
            Records fetched for ``chrom``.
        chrom : str
            The chromosome the records were fetched for.
        scores : Sequence[str] | None, default None
            The ids of the scores to extract. ``None`` means every score of
            the resource.

        Returns
        -------
        Generator[tuple[int, int, list[ScoreValue]], None, None]
            One segment for each record, at the extent of the record. The
            values follow the order of ``scores``.

        Raises
        ------
        ValueError
            If the score is not open, if the table does not hold ``chrom``,
            or if a score id is unknown. The checks run when the method is
            called.

        :meta private:
        """
        score_defs = self._region_read_defs(chrom, scores)
        return self._score_segments(records, score_defs)

    def _region_read_defs(
        self, chrom: str, scores: Sequence[str] | None,
    ) -> list[GenomicScoreDef]:
        """Refuse a region request this score cannot serve, before any record.

        Shared by every kind's :meth:`values_from_records`, so a
        closed score, an unknown contig and an unknown score id are refused
        alike whatever the kind.  The score ids are resolved once for the
        whole region rather than per record, and before the first record
        rather than on it: a typo answering differently on a populated contig
        than on an empty one is the failure that eagerness prevents.

        That argument is about a read that MATERIALISES a contig's positions,
        and it is why this refusal stays the default.  The two aggregating
        reads of :class:`~.position.PositionScore` are the deliberate
        exception: they answer a question about a WINDOW rather than about
        the contig, and a genome-wide fold over a track that skips a
        chromosome is the normal case, not a typo (gain#1211).  They take
        :meth:`_read_defs_for_any_contig` instead and compose the absent
        contig as one uncovered run; every other read, this method's own
        callers included, keeps the refusal.
        """
        self._require_open_and_known_chrom(chrom)
        return self._resolve_score_defs(scores)

    def _read_defs_for_any_contig(
        self, scores: Sequence[str] | None,
    ) -> list[GenomicScoreDef]:
        """Resolve a read's score ids, without judging any contig.

        :meth:`_region_read_defs` minus the contig refusal, for the reads
        that treat a contig the score never mentions as uncovered rather
        than as an error (gain#1211).  Everything else it checks is checked
        here, in the same order: a closed score and an unknown score id are
        refused on an absent contig exactly as on a populated one, so the
        exemption widens what a contig may be and nothing else.

        Offered by the base because the guards it reuses are, but it is
        :class:`~.position.PositionScore`'s two aggregating reads that may
        take it and no others: "an absent contig is uncovered" is a claim on
        the logical read plane, and a kind without one -- an allele score --
        has nothing for it to mean.  A kind that takes this door instead of
        :meth:`_region_read_defs` answers "no records" to a typo.
        """
        self._require_open()
        return self._resolve_score_defs(scores)

    def _score_segments(
        self,
        records: Iterator[Record],
        score_defs: list[GenomicScoreDef],
    ) -> Generator[
            tuple[int, int, list[ScoreValue]], None, None]:
        """Stream this kind's segments for an already-resolved request.

        **The per-kind hook.**  :meth:`values_from_records` minus
        the resolution -- the half of a region read that differs by kind,
        split from the half that does not.  A kind states its reading by
        overriding THIS, and inherits one resolution of the request rather
        than spelling out a second (gain#1282); an allele score is the one
        kind that does, reading each record as the point it sits at.

        It is also why the two ways into the segment stream cannot drift in
        what a segment is: :meth:`values_from_records` resolves and
        composes this body, and a caller that has resolved already composes
        the same body directly.  A kind that overrode the resolving entry
        instead would be read by one and skipped by the other, which is
        drift by another name.

        Every record is yielded at its full extent, including one that only
        partly overlaps -- or entirely misses -- the region it was fetched
        for.  What a partial overlap means depends on what the caller is
        computing, so deciding it belongs to the window-answering consumers,
        each of which clips with :func:`clip_span` (ADR 0008).  That is the
        BASE body's reading -- what a position score and a fragment score
        both mean by a record -- and an overriding kind replaces it.

        A record whose end precedes its begin is refused: that is a claim
        about the record itself, not about any window.  A record outside
        the queried region is NOT refused -- a backend answering a region
        query with such a record is misconfigured rather than holding bad
        data (a table whose index and ``pos_end`` name different columns,
        gain#553), which ADR 0008 refuses at ``open()`` and deliberately
        not here.

        The hottest loop in the read path, so it reads its record slots
        directly rather than through the helpers that wrap them (gain#823):
        :meth:`get_score_values_from_record` is a method call around a
        comprehension over defs already resolved for the whole region, and it
        remains, unchanged, for its other callers.  The refusal those helpers
        carried is kept here, as one comparison raising the shared
        :func:`~gain.genomic_resources.resource_errors.inverted_span_error`;
        see ``test_segment_path_refuses_a_backwards_record``.
        """
        extract = self._extract_value
        for record in records:
            rec_begin = record[POS_BEGIN]
            rec_end = record[POS_END]
            if rec_end < rec_begin:
                raise inverted_span_error(
                    record[CHROM], rec_begin, rec_end,
                    record[REF], record[ALT])
            yield (rec_begin, rec_end, [
                extract(record, score_def) for score_def in score_defs])

    # The records of the region, read as this kind means them.  A plain read: it
    # checks nothing.  The statistics scan reads the same records through the
    # same transform with ``record_validation.validate_records`` composed in
    # front, and that extra link -- visible at the consumer, in
    # ``genomic_scores_impl/scan.py`` -- is the whole of the difference between
    # the two (ADR 0008).
    #
    # ``score_filter`` travels to ``fetch_records`` and nowhere else: the
    # records it rejects never reach the transform, so a rejected record costs
    # no value extraction.  It reads the RECORD, so it may name any score the
    # resource defines, including one outside ``scores``.  It is not a second
    # kind of read and it selects nothing the record read would not.
    #
    # Its ownership check is the one refusal here that does NOT land on the
    # call.  It rides ``fetch_records``, whose generator body defers it, where
    # the request checks ``values_from_records`` runs are eager.
    #
    # One body per kind, in ``_score_segments``, rather than one per kind per
    # consumer: two that had to agree is how the paths drift.  The fragment
    # plane reads through this method for that reason (gain#1272), and the
    # position plane reaches the same body with an already-resolved request
    # (gain#1282) -- different entries, one reading.
    def fetch_region_segments_scores(
        self,
        chrom: str,
        pos_begin: int | None = None,
        pos_end: int | None = None,
        scores: Sequence[str] | None = None,
        *,
        score_filter: ScoreFilter | None = None,
    ) -> Generator[
            tuple[int, int, list[ScoreValue]], None, None]:
        """Yield ``(begin, end, values)`` for each record that touches a region.

        A segment is one record at its own extent, not one value for each
        position. A record on the edge of the region arrives whole. To
        answer a question about the window, clip the segments with
        ``gain.genomic_resources.genomic_scores.records.clip_span``.

        Parameters
        ----------
        chrom : str
            The chromosome name.
        pos_begin : int | None, default None
            The start of the region. ``None`` means no lower bound.
        pos_end : int | None, default None
            The end of the region. ``None`` means no upper bound.
        scores : Sequence[str] | None, default None
            The ids of the scores to read. ``None`` means every score of
            the resource.
        score_filter : ScoreFilter | None, default None
            A filter from :meth:`compile_filter`. The filter reads the
            whole record, so it can name a score outside ``scores``.

        Returns
        -------
        Generator[tuple[int, int, list[ScoreValue]], None, None]
            The segments, in table order. The values follow the order of
            ``scores``. A region without records yields nothing.

        Raises
        ------
        ValueError
            If the score is not open, if the table does not hold ``chrom``,
            or if a score id is unknown. The call raises these errors.
        ScoreFilterError
            If ``score_filter`` was compiled by another score. The first
            ``next()`` raises this error, not the call.
        """
        return self.values_from_records(
            self.fetch_records(
                chrom, pos_begin, pos_end, score_filter=score_filter),
            chrom, scores)

    # The kind's single statement of the weight rule, and every reader that
    # weighs a RECORD goes through it: the allele and fragment aggregating
    # reads fold with it, the per-record statistics scan calls it, and the bulk
    # scan broadcasts it over a whole batch through ``record_weights``.  One
    # statement, so a kind cannot weigh its records one way in one of those
    # and another way in the next.
    # Pinned by test_the_weight_rule_is_stated_once_per_kind.
    #
    # A position score's logical plane is deliberately NOT among them.  It tiles
    # POSITIONS rather than folding records -- ``get_scores_in_region_agg``
    # weighs each run by its length -- and since gain#1131 that is the path
    # annotation takes.  The two agree for this kind, a clipped record's width
    # being exactly the length of the run it covers, but they agree by
    # arithmetic rather than by reading one statement, so a change here is not
    # automatically a change there.
    #
    # An implementation must be numpy-elementwise -- an arithmetic expression
    # over ``left`` and ``right``, or a constant.  It is declared over scalars
    # because that is what its per-record callers hand it, but
    # ``record_weights`` answers a whole batch by handing it the position
    # COLUMNS instead, and only an elementwise body gives the same answers that
    # way.
    #
    # Most ways of breaking that break loudly -- a body branching on ``left``
    # raises ``ValueError`` on an array's ambiguous truth, one calling ``int()``
    # a ``TypeError``.  The contract is written down for the ones that do not: a
    # body REDUCING its arguments (``int(np.mean(right - left + 1))``) hands
    # back a plain number, which is then broadcast as though it were every
    # record's weight, and the two scan paths disagree with nothing raised.
    # That is what test_the_weight_rule_is_stated_once_per_kind's
    # broadcast-agreement assertion is there to catch.
    #
    # Deriving a weight from the span unconditionally is what this hook exists
    # to prevent: it would give a fragment its length as a weight and disagree
    # with the fragment score annotator for every fragment longer than one base
    # pair.
    @classmethod
    @abstractmethod
    def record_weight(cls, left: int, right: int) -> int:
        """Return how many times one record counts when a region is aggregated.

        A position-score record counts once for each base pair of the
        queried region that it covers. An allele record counts once. A
        fragment counts once, however long it is.

        Implement it with an expression that works on numpy arrays as well
        as on numbers: :meth:`record_weights` calls it with the position
        columns of a batch.

        Parameters
        ----------
        left : int
            The first position of the record, clipped to the region.
        right : int
            The last position of the record, clipped to the region.

        Returns
        -------
        int
            The weight of the record.

        :meta private:
        """
        raise NotImplementedError

    # The bulk statistics scan has no record to hand the scalar hook, so it
    # weighs a batch here instead.  This does not restate the rule -- it
    # broadcasts the ONE statement of it, which is why the scan may not read the
    # weight anywhere else.
    #
    # The widening is the elementwise contract being spent: the hook is declared
    # over scalars and its bodies are arithmetic, so the same expression answers
    # a column.  A kind whose weight is a CONSTANT answers with that constant
    # however it was called, so a 0-d result is filled out to the batch's shape
    # rather than treated as an error.
    @classmethod
    def record_weights(
        cls, begins: np.ndarray, ends: np.ndarray,
    ) -> np.ndarray:
        """Return :meth:`record_weight` for the position columns of a batch.

        Parameters
        ----------
        begins : np.ndarray
            The first positions of the records.
        ends : np.ndarray
            The last positions of the records.

        Returns
        -------
        np.ndarray
            An ``int64`` array with the weight of each record. A kind with a
            constant weight gets the constant repeated to the shape of
            ``begins``.

        :meta private:
        """
        # One marker for one fact -- the scalar-declared hook is being
        # called on whole columns, which is the contract its docstring
        # states and this method exists to spend.
        weights = np.asarray(
            cls.record_weight(begins, ends))  # type: ignore[arg-type]
        if weights.ndim == 0:
            weights = np.full(begins.shape, weights)
        return weights.astype(np.int64, copy=False)
