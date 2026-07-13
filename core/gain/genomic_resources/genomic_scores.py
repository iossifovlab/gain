# pylint: disable=too-many-lines
from __future__ import annotations

import abc
import copy
import enum
import warnings
from collections.abc import Callable, Generator, Iterator
from dataclasses import dataclass
from functools import lru_cache
from threading import Lock
from types import TracebackType
from typing import (
    Any,
    cast,
)
from urllib.parse import quote

from gain import logging
from gain.genomic_resources.genomic_position_table import (
    Line,
    VCFGenomicPositionTable,
    build_genomic_position_table,
)
from gain.genomic_resources.genomic_position_table.line import (
    BigWigLine,
    LineBase,
)
from gain.genomic_resources.genomic_position_table.record import (
    ALT,
    CHROM,
    PAYLOAD,
    POS_BEGIN,
    POS_END,
    REF,
    Record,
)
from gain.genomic_resources.genomic_position_table.table_vcf import (
    ALLELE_INDEX,
    VARIANT,
)
from gain.genomic_resources.histogram import (
    Histogram,
    HistogramConfig,
    NumberHistogram,
    build_histogram_config,
    load_histogram,
)
from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceRepo,
)
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)
from gain.genomic_resources.resource_implementation import (
    ResourceConfigValidationMixin,
    get_base_resource_schema,
)

from .aggregators import AGGREGATOR_SCHEMA, Aggregator

logger = logging.getLogger(__name__)

ScoreValue = str | int | float | bool | None

VCF_TYPE_CONVERSION_MAP = {
    "Integer": "int",
    "Float": "float",
    "String": "str",
    "Flag": "bool",
}

SCORE_TYPE_PARSERS = {
    "str": str,
    "float": float,
    "int": int,
    "bool": bool,
}


@dataclass
class ScoreDef:
    """Score configuration definition."""

    score_id: str
    desc: str  # string that will be interpretted as md
    value_type: str  # "str", "int", "float"
    pos_aggregator: str | None     # a valid aggregator type
    allele_aggregator: str | None  # a valid aggregator type

    small_values_desc: str | None
    large_values_desc: str | None

    hist_conf: HistogramConfig | None


@dataclass
class _ScoreDef:
    """Private score configuration definition. Includes internals."""

    # pylint: disable=too-many-instance-attributes
    score_id: str
    desc: str  # string that will be interpretted as md
    value_type: str  # "str", "int", "float"
    pos_aggregator: str | None     # a valid aggregator type
    allele_aggregator: str | None  # a valid aggregator type

    small_values_desc: str | None
    large_values_desc: str | None

    hist_conf: HistogramConfig | None

    col_name: str | None                       # internal
    col_index: int | None                      # internal

    value_parser: Any                             # internal
    na_values: Any                                # internal
    score_index: int | str | None = None       # internal

    def to_public(self) -> ScoreDef:
        return ScoreDef(
            self.score_id,
            self.desc,
            self.value_type,
            self.pos_aggregator,
            self.allele_aggregator,
            self.small_values_desc,
            self.large_values_desc,
            self.hist_conf,
        )

    def __post_init__(self) -> None:
        if self.value_type is None:
            return
        default_na_values = {
            "str": {},
            "float": {"", "nan", ".", "NA"},
            "int": {"", "nan", ".", "NA"},
            "bool": {},
        }
        default_pos_aggregators = {
            "float": "mean",
            "int": "mean",
            "str": "list",
            "bool": None,
        }
        default_allele_aggregators = {
            "float": "max",
            "int": "max",
            "str": "list",
            "bool": None,
        }
        if self.pos_aggregator is None:
            self.pos_aggregator = default_pos_aggregators[self.value_type]
        if self.allele_aggregator is None:
            self.allele_aggregator = \
                default_allele_aggregators[self.value_type]
        if self.na_values is None:
            self.na_values = default_na_values[self.value_type]


class ScoreLineBase(abc.ABC):
    """Shared value extraction for the two per-backend score lines.

    A genomic score is read through one of two concrete score lines, chosen
    per backend when the score is opened: :class:`ScoreLine` wraps the line
    adapter that the tabix/VCF/bigWig backends yield, and
    :class:`RecordScoreLine` wraps the immutable record tuple that the
    in-memory backend yields.  They are **siblings**, not parent/child -- the
    only per-backend difference is where a raw score value comes from, and
    that is captured by ``self._get_raw``, a per-instance callable each
    subclass binds once in its own ``__init__`` (the adapter's ``line.get``
    for :class:`ScoreLine`, the record payload's ``__getitem__`` for
    :class:`RecordScoreLine`).

    Everything downstream of that one lookup -- the five core-field
    properties' contract, NA handling, parsing, logging and the
    bulk/single value walks -- lives here so it cannot drift between the two.

    The base declares no ``__init__`` on purpose: each subclass sets
    ``score_defs`` and binds ``_get_raw`` in its own constructor, with no
    ``super().__init__`` call.  A base constructor (even a trivial one) would
    add a per-**line** Python call on the hot ``fetch_lines`` path -- ~0.055us
    /line, doubling the narrow-table overhead below -- for no benefit, since a
    record is not substitutable for an adapter and the two constructors share
    no work.  The substitutability that finding 4 wanted is instead enforced
    structurally: :attr:`GenomicScore._score_line_class` is typed as a callable
    ``(LineBase | Record, dict) -> ScoreLineBase`` and mypy checks both score
    line classes -- :class:`ScoreLine` and :class:`RecordScoreLine` -- against
    that signature.

    That a table routed to :class:`RecordScoreLine` really does yield records
    is a claim about a *backend*, not about a line, so it is not checked at
    runtime at all: it is pinned once, statically, over all four backends by
    test_backend_record_contract.py.  The fetch path simply believes
    ``table.yields_records``.

    Reading a raw value still costs a per-line bound-method allocation
    (``self._get_raw``).  Measured against a byte-faithful reconstruction of
    the pre-refactor ``ScoreLine`` (construct + ``get_values``, min-of-15):
    ~1.06x on a 1-score line, ~1.03x at 5 scores, a wash (~1.01x) by 454
    scores -- largest on the narrow ``position_score`` shape, invisible on
    wide tables.  In absolute terms ~0.035us/line against a ~200us/record
    end-to-end fetch, i.e. invisible in production.  #239 removes
    :class:`ScoreLine`, at which point this base and the split disappear.
    """

    # Set by each subclass in its own __init__ (no shared base constructor --
    # see the class docstring).  ``_get_raw`` is bound once per instance to the
    # raw-value lookup; it is not a method, so each subclass binds it.
    score_defs: dict[str, _ScoreDef]
    _get_raw: Callable[[str | int], Any]

    @property
    @abc.abstractmethod
    def chrom(self) -> str:
        ...

    @property
    @abc.abstractmethod
    def pos_begin(self) -> int:
        ...

    @property
    @abc.abstractmethod
    def pos_end(self) -> int:
        ...

    @property
    @abc.abstractmethod
    def ref(self) -> str | None:
        ...

    @property
    @abc.abstractmethod
    def alt(self) -> str | None:
        ...

    def __repr__(self) -> str:
        """Name the line by its core fields, not by its address.

        Score lines are interpolated into diagnostics (e.g. the OSError in
        ``GenomicScore._line_to_begin_end``); the default object repr would
        print ``<...RecordScoreLine object at 0x7f...>``, which says nothing
        about the offending row.
        """
        ref_alt = ""
        if self.ref is not None or self.alt is not None:
            ref_alt = f" {self.ref}->{self.alt}"
        return (
            f"{type(self).__name__}"
            f"({self.chrom}:{self.pos_begin}-{self.pos_end}{ref_alt})"
        )

    def _extract_value(self, score_def: _ScoreDef) -> ScoreValue:
        """Get and parse one score from the line using a resolved def.

        A null raw value (e.g. an absent VCF INFO key) or a configured NA
        value yields ``None``; a value that fails to parse is logged and
        yields ``None`` rather than aborting the scan.
        """
        key = score_def.score_index
        assert key is not None

        value: str | int | float | None = self._get_raw(key)
        if value is None or value in score_def.na_values:
            return None
        if score_def.value_parser is None:
            return value
        # pylint: disable=broad-except
        try:  # Temporary workaround for GRR generation
            parsed: ScoreValue = score_def.value_parser(value)
        except Exception:
            logger.exception(
                "unable to parse value %s for score %s",
                value, score_def.score_id)
            return None
        return parsed

    def get_values(
        self, score_defs: list[_ScoreDef],
    ) -> list[ScoreValue]:
        """Extract the values for this line for already-resolved score defs.

        The bulk counterpart of :meth:`get_score`: the caller resolves score
        names to :class:`_ScoreDef` objects once per fetch, and this walks the
        resolved defs per line, so the name->definition lookup is hoisted out
        of the per-line loop.  Returns one value per def, in order, applying
        the same NA handling and parsing as :meth:`get_score`.

        Resolving the score names to definitions is the whole win of the
        hoist (it drops three dict lookups per score, per line); the
        single-value logic is delegated to :meth:`_extract_value` so it
        lives in exactly one place and cannot drift from :meth:`get_score`.
        """
        extract = self._extract_value
        return [extract(score_def) for score_def in score_defs]

    def get_score(self, score_id: str) -> ScoreValue:
        """Get and parse configured score from line."""
        return self._extract_value(self.score_defs[score_id])

    def get_available_scores(self) -> tuple[Any, ...]:
        return tuple(self.score_defs.keys())


class ScoreLine(ScoreLineBase):
    """Score line wrapping a line adapter (the bigWig backend).

    Binds ``self._get_raw`` to the adapter's ``line.get`` and reads the core
    fields straight off the adapter.  See :class:`ScoreLineBase` for the
    shared value-extraction contract; #238 migrates the last adapter backend
    (bigWig) and #239 then removes this class.
    """

    def __init__(
        self, line: LineBase | Record, score_defs: dict[str, _ScoreDef],
    ):
        assert isinstance(line, (Line, BigWigLine))
        self.line = line
        self.score_defs = score_defs
        self._get_raw = line.get

    @property
    def chrom(self) -> str:
        return self.line.chrom

    @property
    def pos_begin(self) -> int:
        return self.line.pos_begin

    @property
    def pos_end(self) -> int:
        return self.line.pos_end

    @property
    def ref(self) -> str | None:
        return self.line.ref

    @property
    def alt(self) -> str | None:
        return self.line.alt


class RecordScoreLineBase(ScoreLineBase):
    """Core fields of a score line over a record, read from the slots.

    The five decoded slots of a record mean the same thing whichever backend
    built it, so every record-backed score line reads its core fields exactly
    this way -- stated once, here, so the two below cannot drift.  What a
    record's PAYLOAD holds is *not* shared: it means whatever the backend that
    built it says it means (a raw tabular row for the tabix and in-memory
    backends, a ``(variant record, allele index)`` pair for VCF).  So each
    subclass binds ``self._get_raw`` -- and only that -- to the lookup its own
    payload calls for.

    Like :class:`ScoreLineBase`, this declares no ``__init__``: each subclass
    sets ``record``/``score_defs`` and binds ``_get_raw`` in its own
    constructor, so a fetched line pays no base-constructor call.
    """

    record: Record

    @property
    def chrom(self) -> str:
        return cast(str, self.record[CHROM])

    @property
    def pos_begin(self) -> int:
        return cast(int, self.record[POS_BEGIN])

    @property
    def pos_end(self) -> int:
        return cast(int, self.record[POS_END])

    @property
    def ref(self) -> str | None:
        return cast("str | None", self.record[REF])

    @property
    def alt(self) -> str | None:
        return cast("str | None", self.record[ALT])


class RecordScoreLine(RecordScoreLineBase):
    """Score line over a record whose payload is a raw row (tabix/in-memory).

    A raw score column is read from the opaque payload (the raw row) on demand,
    so a wide table decodes only the columns a caller asks for.  Value
    extraction, NA handling and parsing come from :class:`ScoreLineBase`, the
    core fields from :class:`RecordScoreLineBase`; the only difference from its
    sibling :class:`VCFScoreLine` is where a raw value comes from, captured by
    binding ``self._get_raw`` to the payload's indexer.

    The constructor does **not** check that ``line`` is a record.  Whether a
    table yields records is a property of the *backend* -- one shape of thing,
    for every line, forever -- so it is a question about four classes, answered
    once and statically by test_backend_record_contract.py, which opens each
    backend and holds its first line against its ``yields_records`` claim.
    Asking it again per line would put a per-backend question on the hot path,
    which is the cost this whole class exists to avoid.
    """

    def __init__(
        self, line: LineBase | Record, score_defs: dict[str, _ScoreDef],
    ):
        # Bind the raw-value lookup to the payload's __getitem__ (score
        # columns are addressed by resolved integer index), the record-backed
        # counterpart of ``line.get``.  ``line`` is a record: the table said so
        # (yields_records), and that claim is pinned against every backend by
        # test_backend_record_contract.py.  Nothing here may cost more than an
        # attribute store -- a ``cast`` would be a real call, ~15ns.
        self.record: Record = line  # type: ignore[assignment]
        self.score_defs = score_defs
        self._get_raw = self.record[PAYLOAD].__getitem__


class VCFScoreLine(RecordScoreLineBase):
    """Score line over a VCF record: a score is an INFO field, not a column.

    The VCF backend is the awkward one, and this class is where the whole of
    its awkwardness lives.  A VCF score is not addressed by column index: it is
    an INFO field, looked up **by name** on the variant record, typed by the
    header metadata, and -- for a per-allele field -- selected by the record's
    allele index.  That needs all three of the variant, its header and the
    allele index, which is exactly what a VCF record's PAYLOAD makes reachable:
    it is the ``(variant record, allele index)`` pair, and the header comes off
    the variant (``variant.header.info``), so nothing else has to be carried.

    **This choice is made once, per table, when the score is opened** -- see
    ``GenomicScore.open``, which routes a VCF table here and every other record
    table to :class:`RecordScoreLine`.  Which backend a line came from is a
    property of the table, so it is asked of the table, once; the fetch path
    then does no branching at all.  (Before #237 the same polymorphism lived in
    a per-*line* adapter object, ``VCFLine``, built for every allele of every
    record read.)

    The INFO number cases, all of them, in :meth:`_get_info`.
    """

    def __init__(
        self, line: LineBase | Record, score_defs: dict[str, _ScoreDef],
    ):
        # ``line`` is a VCF record -- the table said so, and the claim is pinned
        # over every backend by test_backend_record_contract.py.  The payload is
        # unpacked lazily, in _get_info: a line whose scores are never read (an
        # allele filtered out by REF/ALT in AlleleScore.fetch_scores, say) pays
        # nothing beyond the three null stores below.
        self.record: Record = line  # type: ignore[assignment]
        self.score_defs = score_defs
        self._get_raw = self._get_info
        # What an INFO lookup needs, resolved on the first score read of this
        # line and reused by every later one (see _get_info).  ``_info`` doubles
        # as the "not resolved yet" flag -- pysam never hands back a null INFO
        # proxy, so ``None`` here can only mean unread.  All three are declared
        # here, null, rather than sprung into existence inside _get_info, so
        # every instance of the class lays the same attributes down in the same
        # order.
        #
        # The memo is not free: at ONE score there is nothing to reuse yet, only
        # the state to set up, and it costs ~0.17us/line; from the second score
        # on it saves ~0.17us per score, which is what it costs to allocate the
        # two proxies again.  So it pays for itself at two scores and compounds
        # from there -- 3000-line fetch_lines + get_values, min-of-9, us/line:
        #
        #     scores      1      5     20     50
        #     no memo   1.47   3.84  12.57  31.08
        #     memo      1.64   3.56  10.89  25.43
        #
        # That is the right trade for this backend: score resources of VCF INFO
        # shape are wide (ClinVar-shaped: ~20 INFO fields), and statistics and
        # histogram generation read every score def of them, so the width is
        # where this read path actually lives.  Resolving on first read rather
        # than in __init__ is the other half of the trade: an allele filtered
        # out by REF/ALT in AlleleScore.fetch_scores is never scored at all, and
        # eager resolution would charge it the two proxies for nothing.
        self._info: Any = None
        self._info_meta: Any = None
        self._allele_index: int | None = None

    def _get_info(self, key: str | int) -> Any:
        """Look one INFO field up on this record's allele.

        The four cases, which are the reason VCF needs a score line of its own:

        * **Number=A** -- one value per ALT allele: select this record's allele.
          A record whose ALT is absent ('.') has no allele index and no
          per-allele value; the raw tuple is handed back untouched, as before.
        * **Number=R** -- one value per allele *including the reference*, which
          occupies offset 0: an ALT allele reads at ``allele_index + 1``, and a
          record with no ALT reads the **reference** value at offset 0.
        * **Number=. and Type=String** -- an unbounded string field, joined on
          '|' into a single value (a VCF-local convention: the shared
          stringifier never sees the tuple).
        * anything else -- handed back as pysam decoded it.

        An absent INFO key yields ``None`` rather than raising: ``info.get``
        and the metadata's ``.get`` both return ``None``, and ``None`` is not a
        tuple, so the number cases are skipped and ``_extract_value`` turns it
        into a null score.  (The metadata is looked up for every key, absent
        ones included -- ``.get`` simply answers ``None`` for those, and only
        the tuple branch below goes on to read a ``.number`` off it.)

        **The two pysam proxies are per-LINE, not per-score.**  Neither
        ``variant.info`` nor ``variant.header.info`` is a cached attribute:
        pysam allocates a fresh proxy on *every* access (~85ns each, and
        ``v.info is v.info`` is False).  Obtaining them once per score would
        therefore put ~170ns of pure re-allocation on every score of every
        line, which is a per-line cost of exactly the kind the record migration
        exists to remove -- and it grows with the width of the table, where VCF
        INFO resources are widest (a ClinVar-shaped resource declares ~20 INFO
        fields, and statistics generation reads them all).  They belong to the
        line, so they are resolved once, on the first score read, and every
        later read of the same line reuses them.  (Pinned by
        test_vcf_score_line_reads_the_pysam_proxies_once_per_line.)

        Resolving them on the first read rather than in ``__init__`` keeps the
        payload unpacking lazy: a line whose scores are never read pays nothing
        at all.
        """
        assert isinstance(key, str)
        info = self._info
        if info is None:
            payload = self.record[PAYLOAD]
            variant = payload[VARIANT]
            # The header metadata is derived from the record, not carried with
            # it.
            info = self._info = variant.info
            self._info_meta = variant.header.info
            self._allele_index = payload[ALLELE_INDEX]
        allele_index = self._allele_index

        value = info.get(key)
        meta = self._info_meta.get(key)
        if isinstance(value, tuple):
            if meta.number == "A" and allele_index is not None:
                value = value[allele_index]
            elif meta.number == "R":
                return value[
                    allele_index + 1
                    if allele_index is not None
                    else 0  # Get reference allele value if ALT is '.'
                ]
            elif meta.number == "." and meta.type == "String":
                return "|".join(value)
        return value


# What a fetched line is wrapped in: a callable, not a ``type[...]``, so mypy
# checks both score line classes against one signature.  We never call
# issubclass on it.
_ScoreLineFactory = Callable[
    [LineBase | Record, dict[str, _ScoreDef]], ScoreLineBase,
]


@dataclass
class PositionScoreQuery:
    score: str
    position_aggregator: str | None = None


@dataclass
class AlleleScoreQuery:
    """Deprecated. Use annotator-level aggregators instead."""

    score: str
    position_aggregator: str | None = None
    allele_aggregator: str | None = None

    def __post_init__(self) -> None:
        warnings.warn(
            "AlleleScoreQuery is deprecated and will be removed in a future "
            "version. Use annotator-level aggregators instead.",
            DeprecationWarning,
            stacklevel=2,
        )


@dataclass
class PositionScoreAggr:
    score: str
    position_aggregator: Aggregator


@dataclass
class AlleleScoreAggr:
    score: str
    position_aggregator: Aggregator
    allele_aggregator: Aggregator


ScoreQuery = PositionScoreQuery | AlleleScoreQuery


class GenomicScore(ResourceConfigValidationMixin):
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

        - **type**: Resource type (position_score, allele_score, np_score)
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
        - **position_aggregator**: Default aggregator for positions (optional)
        - **allele_aggregator**: Default aggregator for alleles (optional)

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
        score_definitions (dict[str, _ScoreDef]): Mapping of score IDs to
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

    Abstract Methods:
        Subclasses must implement:
        - _fetch_region_values(): Core method for retrieving score values
          in a genomic region, used for statistics computation

    See Also:
        - PositionScore: For position-based genomic scores
        - AlleleScore: For variant-specific genomic scores
        - GenomicResource: Base resource abstraction
        - GenomicPositionTable: Table format abstraction
    """

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
        # What each fetched line is wrapped in; the record-yielding backends use
        # RecordScoreLine, the adapter-yielding ones ScoreLine.  Selected in
        # open(), from the table's yields_records claim -- this default holds
        # only until then.
        self._score_line_class: _ScoreLineFactory = ScoreLine

    @staticmethod
    def get_schema() -> dict[str, Any]:
        scores_schema = {
            "type": "list", "schema": {
                "type": "dict",
                "schema": {
                    "id": {"type": "string"},
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
                    "type": {"type": "string"},
                    "desc": {"type": "string"},
                    "na_values": {"type": ["string", "list"]},
                    "large_values_desc": {"type": "string"},
                    "small_values_desc": {"type": "string"},
                    "histogram": {"type": "dict", "schema": {
                        "type": {"type": "string"},
                        "plot_function": {"type": "string"},
                        "number_of_bins": {
                            "type": "number",
                            "dependencies": {"type": "number"},
                        },
                        "view_range": {"type": "dict", "schema": {
                            "min": {"type": "number"},
                            "max": {"type": "number"},
                        }, "dependencies": {"type": "number"}},
                        "x_log_scale": {
                            "type": "boolean",
                            "dependencies": {"type": "number"},
                        },
                        "y_log_scale": {
                            "type": "boolean",
                            "dependencies": {
                                "type": ["number", "categorical"]},
                        },
                        "x_min_log": {
                            "type": "number",
                            "dependencies": {
                                "type": ["number", "categorical"]},
                        },
                        "label_rotation": {
                            "type": "integer",
                            "dependencies": {"type": "categorical"},
                        },
                        "value_order": {
                            "type": "list",
                            "schema": {"type": ["string", "integer"]},
                            "dependencies": {"type": "categorical"},
                        },
                        "displayed_values_count": {
                            "type": "integer",
                            "dependencies": {"type": "categorical"},
                        },
                        "displayed_values_percent": {
                            "type": "number",
                            "dependencies": {"type": "categorical"},
                        },
                        "reason": {
                            "type": "string",
                            "dependencies": {"type": "null"},
                        },
                    }},
                },
            },
        }
        return {
            **get_base_resource_schema(),
            "table": {"type": "dict", "schema": {
                "filename": {"type": "string"},
                "index_filename": {"type": "string"},
                "zero_based": {"type": "boolean"},
                "desc": {"type": "string"},
                "format": {"type": "string"},
                "header_mode": {"type": "string"},
                "header": {"type": ["string", "list"]},
                "chrom": {"type": "dict", "schema": {
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
                }},
                "pos_begin": {"type": "dict", "schema": {
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
                }},
                "pos_end": {"type": "dict", "schema": {
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
                }},
                "chrom_mapping": {"type": "dict", "schema": {
                    "filename": {
                        "type": "string",
                        "excludes": ["add_prefix", "del_prefix"],
                    },
                    "add_prefix": {"type": "string"},
                    "del_prefix": {"type": "string", "excludes": "add_prefix"},
                }},
            }},
            "scores": scores_schema,
            "default_annotation": {
                "type": ["dict", "list"], "allow_unknown": True,
            },
        }

    @staticmethod
    def _parse_scoredef_config(
        config: dict[str, Any],
    ) -> dict[str, _ScoreDef]:
        """Parse ScoreDef configuration."""
        scores = {}

        for score_conf in config["scores"]:
            value_parser = SCORE_TYPE_PARSERS[score_conf.get("type", "float")]

            col_name = score_conf.get("column_name") \
                or score_conf.get("name")
            col_index_str = score_conf.get("column_index") \
                or score_conf.get("index")
            col_index = int(col_index_str) if col_index_str else None

            hist_conf = build_histogram_config(score_conf)
            nuc_aggregator = score_conf.get("nucleotide_aggregator")
            allele_aggregator = score_conf.get("allele_aggregator")
            if nuc_aggregator is not None:
                logger.warning(
                    "Use of 'nucleotide_aggregator' is deprecated, use "
                    "'allele_aggregator' instead.")
                assert allele_aggregator is None
                allele_aggregator = nuc_aggregator

            score_def = _ScoreDef(
                score_id=score_conf["id"],
                desc=score_conf.get("desc", ""),
                value_type=score_conf.get("type"),
                pos_aggregator=score_conf.get("position_aggregator"),
                allele_aggregator=allele_aggregator,
                small_values_desc=score_conf.get("small_values_desc"),
                large_values_desc=score_conf.get("large_values_desc"),
                col_name=col_name,
                col_index=col_index,
                hist_conf=hist_conf,
                value_parser=value_parser,
                na_values=score_conf.get("na_values"),
            )

            scores[score_conf["id"]] = score_def
        return scores

    def _parse_vcf_scoredefs(
        self,
        vcf_header_info: dict[str, Any] | None,
        config_scoredefs: dict[str, _ScoreDef] | None, *,
        merge: bool = False,
    ) -> dict[str, _ScoreDef]:
        def converter(val: Any) -> Any:
            try:
                if isinstance(val, tuple):
                    return "|".join(map(str, val))
            except TypeError:
                pass

            return val

        vcf_scoredefs = {}

        assert vcf_header_info is not None

        for key, value in vcf_header_info.items():
            value_parser: Callable[[str], Any] | None = converter
            if value.number in (1, "A", "R"):
                value_parser = None

            vcf_scoredefs[key] = _ScoreDef(
                score_id=key,
                col_name=key,
                col_index=None,
                desc=value.description or "",
                value_type=VCF_TYPE_CONVERSION_MAP[value.type],
                value_parser=value_parser,
                na_values=(),
                pos_aggregator=None,
                allele_aggregator=None,
                small_values_desc=None,
                large_values_desc=None,
                hist_conf=None,
            )
        if config_scoredefs is None:
            return vcf_scoredefs

        # allow overriding of vcf-generated scoredefs
        scoredefs = {}
        for score, config_scoredef in config_scoredefs.items():
            vcf_scoredef = vcf_scoredefs[score]

            value_type = config_scoredef.value_type or vcf_scoredef.value_type

            scoredef = _ScoreDef(
                score_id=vcf_scoredef.score_id,
                desc=config_scoredef.desc or vcf_scoredef.desc,
                value_type=value_type,

                pos_aggregator=config_scoredef.pos_aggregator,
                allele_aggregator=config_scoredef.allele_aggregator,

                small_values_desc=config_scoredef.small_values_desc,
                large_values_desc=config_scoredef.large_values_desc,
                col_name=vcf_scoredef.col_name,
                col_index=vcf_scoredef.col_index,
                hist_conf=config_scoredef.hist_conf,
                value_parser=config_scoredef.value_parser,
                na_values=config_scoredef.na_values or vcf_scoredef.na_values,
            )
            scoredefs[score] = scoredef

        if merge:
            for score, vcf_scoredef in vcf_scoredefs.items():
                if score in scoredefs:
                    continue
                scoredefs[score] = vcf_scoredef

        return scoredefs

    def _validate_scoredefs(self) -> None:
        assert "scores" in self.config
        if self.table.header_mode == "none":
            assert all("name" not in score
                       for score in self.config["scores"]), \
                ("Cannot configure score columns by"
                 " name when header_mode is 'none'!")
        elif self.table.header is None:
            # Table has no header (e.g. BigWig); column-name references are
            # invalid, but index-based scores are fine — open() validates them.
            return
        else:
            for score in self.config["scores"]:

                if "name" in score:
                    score["column_name"] = score["name"]
                    logger.debug(
                        "%s: Using 'name' to configure score columns is"
                        " outdated, use 'column_name' instead.",
                        self.resource.get_full_id(),
                    )
                elif "index" in score:
                    score["column_index"] = score["index"]
                    logger.debug(
                        "%s: Using 'index' to configure score columns is"
                        " outdated, use 'column_index' instead.",
                        self.resource.get_full_id(),
                    )

                if "column_name" in score:
                    assert score["column_name"] in self.table.header, (
                        score, self.table.header)
                elif "column_index" in score:
                    assert 0 <= score["column_index"] < len(self.table.header)
                else:
                    raise AssertionError("Either an index or name must"
                                         " be configured for scores!")

    def _build_scoredefs(self) -> dict[str, _ScoreDef]:
        config_scoredefs = None
        if "scores" in self.config:
            config_scoredefs = self._parse_scoredef_config(self.config)

        if isinstance(self.table, VCFGenomicPositionTable):
            merge = bool(self.config.get("merge_vcf_scores", False))

            return self._parse_vcf_scoredefs(
                cast(dict[str, Any], self.table.header),
                config_scoredefs,
                merge=merge)

        if config_scoredefs is None:
            raise ValueError("No scores configured and not using a VCF")

        return config_scoredefs

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

    def get_score_definition(self, score_id: str) -> _ScoreDef | None:
        return self.score_definitions.get(score_id)

    def close(self) -> None:
        self.table.close()
        self.table_loaded = False

    def is_open(self) -> bool:
        return self.table_loaded

    def open(self) -> GenomicScore:
        """Open genomic score resource and returns it."""
        if self.is_open():
            logger.info(
                "opening already opened genomic score: %s",
                self.resource.resource_id)
            return self
        self.table.open()
        is_vcf = isinstance(self.table, VCFGenomicPositionTable)
        # Choose the score line class per backend -- ONE decision, per table,
        # made here rather than per line.  A VCF table's scores are INFO fields,
        # so it goes to the VCFScoreLine that performs the INFO lookup; any
        # other record-yielding table's scores are columns of the record's raw
        # row, so it goes to RecordScoreLine; an adapter-yielding table (bigWig,
        # until #238) to ScoreLine.  This is decided at open time, alongside the
        # table's own parser/transform selection, and the table's yields_records
        # claim is simply believed -- that every backend's claim matches what it
        # really yields is pinned statically, over all four of them, by
        # test_backend_record_contract.py, so the fetch path pays nothing.
        #
        # Route BEFORE publishing.  ``table_loaded = True`` is what makes this
        # score look open to everyone else: from that write on, another caller's
        # open() takes the is_open() early return above and reads
        # _score_line_class straight away.  Written the other way round, that
        # caller can catch the score published-but-unrouted and read the
        # __init__ default (ScoreLine) for a record-yielding table -- a record
        # tuple into an adapter score line.  Scores are shared across threads
        # (the process-wide in-memory CNV cache; gain-web-api's thread pool), so
        # the window is reachable; this ordering keeps the ROUTING out of it.
        # Pinned by test_the_score_is_routed_before_it_reports_itself_open.
        #
        # It does not make open() as a whole safe to race, and does not claim
        # to: the score_index assignment below still runs after the score has
        # published itself open, so a caller that catches that window reads a
        # score def whose score_index is still None.  That window is older than
        # this routing and untouched by it -- open() is not synchronised, and
        # making it so is a separate change.
        if is_vcf:
            self._score_line_class = VCFScoreLine
        elif self.table.yields_records:
            self._score_line_class = RecordScoreLine
        else:
            self._score_line_class = ScoreLine
        self.table_loaded = True
        if "scores" in self.config:
            self._validate_scoredefs()

        if is_vcf:
            # A VCF score is addressed by INFO key, not by column index.
            for score_def in self.score_definitions.values():
                assert score_def.col_name is not None
                score_def.score_index = score_def.col_name
        else:
            for score_def in self.score_definitions.values():
                if score_def.col_index is None:
                    assert self.table.header is not None
                    assert score_def.col_name is not None
                    score_def.score_index = self.table.header.index(
                        score_def.col_name)
                else:
                    assert score_def.col_name is None
                    score_def.score_index = score_def.col_index
        return self

    def __enter__(self) -> GenomicScore:
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
    def _line_to_begin_end(line: ScoreLineBase) -> tuple[str, int, int]:
        if line.pos_end < line.pos_begin:
            raise OSError(
                f"The resource line {line} has a region "
                f"with end {line.pos_end} smaller than the "
                f"beginning {line.pos_begin}.")
        return line.chrom, line.pos_begin, line.pos_end

    def _get_header(self) -> tuple[Any, ...] | None:
        assert self.table is not None
        return self.table.header

    def fetch_lines(
        self,
        chrom: str | None,
        pos_begin: int | None,
        pos_end: int | None,
    ) -> Iterator[ScoreLineBase]:
        """Fetch lines in a region and wrap them in ScoreLines."""
        try:
            for line in self.table.get_records_in_region(
                chrom, pos_begin, pos_end,
            ):
                yield self._score_line_class(line, self.score_definitions)
        except Exception:
            logger.exception(
                "Error fetching lines for region %s:%s-%s in resource %s",
                chrom, pos_begin, pos_end, self.resource_id)
            raise

    def get_all_chromosomes(self) -> list[str]:
        if not self.is_open():
            raise ValueError(f"genomic score <{self.resource_id}> is not open")

        return self.table.get_chromosomes()

    def get_all_scores(self) -> list[str]:
        return list(self.score_definitions)

    def _fetch_region_lines(
        self,
        chrom: str | None,
        pos_begin: int | None,
        pos_end: int | None,
        scores: list[str] | None = None,
    ) -> Generator[
            tuple[str, int, int, list[ScoreValue] | None, ScoreLineBase],
            None, None]:
        """Return score values in a region."""
        if not self.is_open():
            raise ValueError(f"genomic score <{self.resource_id}> is not open")

        if chrom is not None and chrom not in self.get_all_chromosomes():
            raise ValueError(
                f"{chrom} is not among the available chromosomes.")

        if scores is None:
            scores = self.get_all_scores()
        # Hoist the score name->definition resolution out of the per-line
        # loop: it is fixed for the whole scan.  Resolve lazily on first
        # line so that an empty region does not touch score_definitions --
        # matching the base behaviour where an unknown score id is only
        # rejected when there is a line to extract it from.
        score_defs: list[_ScoreDef] | None = None

        for line in self.fetch_lines(chrom, pos_begin, pos_end):
            line_chrom, line_begin, line_end = self._line_to_begin_end(line)
            if pos_begin is not None and line_end < pos_begin:
                continue

            if score_defs is None:
                score_defs = [
                    self.score_definitions[scr_id] for scr_id in scores]
            val = line.get_values(score_defs)

            if pos_begin is not None:
                left = max(pos_begin, line_begin)
            else:
                left = line_begin
            right = min(pos_end, line_end) if pos_end is not None else line_end
            yield (line_chrom, left, right, val, line)

    @abc.abstractmethod
    def fetch_region_values(
        self,
        chrom: str | None = None,
        pos_begin: int | None = None,
        pos_end: int | None = None,
        scores: list[str] | None = None,
    ) -> Generator[
            tuple[int, int, list[ScoreValue] | None], None, None]:
        """Return score values - either all available or in a specific region.

        This method is used for calculation of score statistics.
        """

    @lru_cache(maxsize=64)
    def get_score_range(
        self, score_id: str,
    ) -> tuple[float, float] | None:
        """Return the value range for a numeric score."""
        if score_id not in self.get_all_scores():
            raise ValueError(
                f"unknown score {score_id}; "
                f"available scores are {self.get_all_scores()}")
        hist = self.get_score_histogram(score_id)
        if isinstance(hist, NumberHistogram):
            return (hist.min_value, hist.max_value)
        return None

    def get_histogram_filename(self, score_id: str) -> str:
        """Return the histogram filename for a genomic score."""
        filename = f"statistics/histogram_{score_id}.yaml"
        if filename in self.resource.get_manifest():
            return filename
        return f"statistics/histogram_{score_id}.json"

    @lru_cache(maxsize=64)
    def get_score_histogram(self, score_id: str) -> Histogram:
        """Return defined histogram for a score."""
        if score_id not in self.score_definitions:
            raise ValueError(
                f"unexpected score ID {score_id}; available scores are: "
                f"{self.score_definitions.keys()}")

        hist_filename = self.get_histogram_filename(score_id)
        return load_histogram(self.resource, hist_filename)

    def get_histogram_image_filename(self, score_id: str) -> str:
        return f"statistics/histogram_{score_id}.png"

    def _histogram_image_url(self, score_id: str, repo_url: str) -> str:
        return (
            f"{repo_url}/"
            f"{quote(self.get_histogram_image_filename(score_id))}"
        )

    def get_histogram_image_url(self, score_id: str) -> str | None:
        return self._histogram_image_url(
            score_id, self.resource.get_url())

    def get_histogram_image_public_url(self, score_id: str) -> str:
        """Return the histogram image URL on the resource's public mirror.

        Unlike :meth:`get_histogram_image_url`, this is built from the
        resource's public URL so it is reachable from a browser even when
        the GRR is a local directory repository.
        """
        return self._histogram_image_url(
            score_id, self.resource.get_public_url())


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
        ...     values = score.fetch_scores("chr1", 12345)
        ...     # Fetch scores across a region
        ...     for pos_begin, pos_end, scores in score.fetch_region(
        ...         "chr1", 10000, 20000
        ...     ):
        ...         print(f"{pos_begin}-{pos_end}: {scores}")
        ...     # Aggregate scores over a region
        ...     aggs = score.fetch_scores_agg("chr1", 10000, 20000)

    Attributes:
        resource: The underlying GenomicResource object
        resource_id: Unique identifier for the resource
        config: Configuration dictionary for the score
        table: GenomicPositionTable for data access
        score_definitions: Dictionary mapping score IDs to their definitions

    Key Methods:
        fetch_scores: Get score values at a specific position
        fetch_region: Iterate over score values in a genomic region
        fetch_scores_agg: Aggregate score values over a region
        get_region_scores: Get all scores in a region for a specific score ID
    """

    @staticmethod
    def get_schema() -> dict[str, Any]:
        schema = copy.deepcopy(GenomicScore.get_schema())
        scores_schema = schema["scores"]["schema"]["schema"]
        scores_schema["position_aggregator"] = AGGREGATOR_SCHEMA
        return schema

    def open(self) -> PositionScore:
        return cast(PositionScore, super().open())

    def fetch_region_values(
        self,
        chrom: str | None = None,
        pos_begin: int | None = None,
        pos_end: int | None = None,
        scores: list[str] | None = None,
    ) -> Generator[
            tuple[int, int, list[ScoreValue] | None], None, None]:
        """Return position score values in a region."""
        returned_region: tuple[
            str | None, int | None, int | None, list[ScoreValue] | None,
        ] = (None, None, None, None)
        for lchrom, left, right, val, _ in self._fetch_region_lines(
            chrom, pos_begin, pos_end, scores,
        ):
            prev_chrom = returned_region[0]
            if prev_chrom and lchrom != prev_chrom:
                returned_region = (lchrom, None, None, None)
            prev_end = returned_region[2]

            if prev_end and left <= prev_end:
                logger.warning(
                    "multiple values for positions %s:%s-%s",
                    chrom, left, right)
                raise ValueError(
                    f"multiple values for positions "
                    f"{chrom}:{left}-{right}")
            returned_region = (lchrom, left, right, val)
            yield (left, right, val)

    def fetch_region(
        self, chrom: str | None,
        pos_begin: int | None,
        pos_end: int | None,
        scores: list[str] | None = None,
    ) -> Generator[
            tuple[int, int, list[ScoreValue] | None], None, None]:
        """Return position score values in a region."""
        yield from self.fetch_region_values(chrom, pos_begin, pos_end, scores)

    def get_region_scores(
        self,
        chrom: str,
        pos_beg: int,
        pos_end: int,
        score_id: str,
    ) -> list[ScoreValue]:
        """Return score values in a region."""
        result: list[ScoreValue | None] = [None] * (pos_end - pos_beg + 1)
        for b, e, v in self.fetch_region(
                chrom, pos_beg, pos_end, [score_id]):
            e = min(e, pos_end)
            if v is None:
                continue
            result[b - pos_beg:e - pos_beg + 1] = [v[0]] * (e - b + 1)

        return result

    def fetch_scores(
        self, chrom: str, position: int,
        scores: list[str] | None = None,
    ) -> list[ScoreValue] | None:
        """Fetch score values at specific genomic position."""
        if chrom not in self.get_all_chromosomes():
            raise ValueError(
                f"{chrom} is not among the available chromosomes.")

        if scores is None:
            scores = self.get_all_scores()
        else:
            scores = [
                s.score if isinstance(s, PositionScoreQuery) else s
                for s in scores]
        assert all(isinstance(s, str) for s in scores)

        lines = list(self.fetch_lines(chrom, position, position))
        if not lines:
            return None

        if len(lines) > 1:
            logger.warning(
                "multiple values for positions %s:%s",
                chrom, position)
            raise ValueError(
                f"multiple values ({len(lines)}) for positions "
                f"{chrom}:{position}")

        line = lines[0]

        requested_scores = scores or self.get_all_scores()
        # Resolve names to definitions once for this point fetch.
        score_defs = [
            self.score_definitions[scr] for scr in requested_scores]
        return line.get_values(score_defs)

    def _build_scores_agg(
        self, scores: list[str] | list[PositionScoreQuery],
    ) -> list[PositionScoreAggr]:
        score_aggs = []
        aggregator_type: str | None
        for score in scores:
            if isinstance(score, str):
                aggregator_type = self.score_definitions[score].pos_aggregator
                assert aggregator_type is not None
                score_aggs.append(PositionScoreAggr(
                    score,
                    Aggregator.build(aggregator_type),
                ))
                continue

            assert isinstance(score, PositionScoreQuery)
            if score.position_aggregator is not None:
                aggregator_type = score.position_aggregator
            else:
                aggregator_type = \
                    self.score_definitions[score.score].pos_aggregator
            assert aggregator_type is not None
            score_aggs.append(
                PositionScoreAggr(
                    score.score,
                    Aggregator.build(aggregator_type)),
            )
        return score_aggs

    def fetch_scores_agg(  # pylint: disable=too-many-arguments,too-many-locals
            self, chrom: str, pos_begin: int, pos_end: int,
            scores: list[str] | list[PositionScoreQuery] | None = None,
    ) -> list[Aggregator]:
        """Fetch score values in a region and aggregates them.

        Case 1:
           res.fetch_scores_agg("1", 10, 20) -->
              all score with default aggregators
        Case 2:
           res.fetch_scores_agg("1", 10, 20,
                                non_default_aggregators={"bla":"max"}) -->
              all score with default aggregators but 'bla' should use 'max'
        """
        if chrom not in self.get_all_chromosomes():
            raise ValueError(
                f"{chrom} is not among the "
                f"available chromosomes.")
        if scores is None:
            scores = [
                PositionScoreQuery(score_id)
                for score_id in self.get_all_scores()]

        score_aggs = self._build_scores_agg(scores)

        for line in self.fetch_lines(chrom, pos_begin, pos_end):
            _line_chrom, line_begin, line_end = self._line_to_begin_end(line)
            for sagg in score_aggs:
                val = line.get_score(sagg.score)

                left = (
                    max(pos_begin, line_begin)
                )
                right = (
                    min(pos_end, line_end)
                )
                for _ in range(left, right + 1):
                    sagg.position_aggregator.add(val)

        return [squery.position_aggregator for squery in score_aggs]


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
        ...     values = score.fetch_scores(
        ...         "chr1", 12345, "A", "T"
        ...     )
        ...     # Iterate over variants in a region
        ...     for pos, ref, alt, scores in score.fetch_region(
        ...         "chr1", 10000, 20000
        ...     ):
        ...         print(f"{pos} {ref}>{alt}: {scores}")
        ...     # Aggregate scores over a region
        ...     from gain.genomic_resources.genomic_scores import (
        ...         AlleleScoreQuery
        ...     )
        ...     queries = [
        ...         AlleleScoreQuery(
        ...             "cadd_raw",
        ...             position_aggregator="mean",
        ...             allele_aggregator="max"
        ...         )
        ...     ]
        ...     aggs = score.fetch_scores_agg(
        ...         "chr1", 10000, 20000, queries
        ...     )

    Attributes:
        resource: The underlying GenomicResource object
        resource_id: Unique identifier for the resource
        config: Configuration dictionary for the score
        table: GenomicPositionTable for data access (typically VCF)
        score_definitions: Dictionary mapping score IDs to their definitions
        mode: Operating mode (SUBSTITUTIONS or ALLELES)

    Key Methods:
        fetch_scores: Get score values for a specific variant
        fetch_region: Iterate over variant scores in a genomic region
        fetch_scores_agg: Aggregate scores over a region with position and
                         allele aggregation
        substitutions_mode: Check if operating in SUBSTITUTIONS mode
        alleles_mode: Check if operating in ALLELES mode

    Configuration:
        The resource configuration should specify:
        - table.filename: Path to the data file (usually VCF)
        - table.reference: Column/field containing reference alleles
        - table.alternative: Column/field containing alternative alleles
        - allele_score_mode: Either "substitutions" or "alleles" (optional)
        - scores: List of score definitions with optional position_aggregator
                 and allele_aggregator specifications
    """

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
        if resource.get_type() not in {"allele_score", "np_score"}:
            raise ValueError(
                "The resrouce provided to AlleleScore should be of"
                f"'allele_score' type, not a '{resource.get_type()}'")
        if resource.get_type() == "np_score":
            logger.warning(
                "The resource type `np_score` is deprecated. "
                "Please use `allele_score` instead for resource %s.",
                resource.get_id())
        super().__init__(resource)
        if self.config.get("allele_score_mode") is None:
            if resource.get_type() == "np_score":
                self.mode = AlleleScore.Mode.SUBSTITUTIONS
            elif resource.get_type() == "allele_score":
                self.mode = AlleleScore.Mode.ALLELES
            else:
                raise ValueError(
                    f"unknown resource type {resource.get_type()}")
        else:
            self.mode = AlleleScore.Mode.from_name(
                self.config.get("allele_score_mode", "substitutions"))

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
        scores_schema["position_aggregator"] = AGGREGATOR_SCHEMA
        scores_schema["allele_aggregator"] = AGGREGATOR_SCHEMA
        scores_schema["nucleotide_aggregator"] = AGGREGATOR_SCHEMA
        return schema

    def open(self) -> AlleleScore:
        return cast(AlleleScore, super().open())

    def fetch_region_values(
        self,
        chrom: str | None = None,
        pos_begin: int | None = None,
        pos_end: int | None = None,
        scores: list[str] | None = None,
    ) -> Generator[
            tuple[int, int, list[ScoreValue] | None], None, None]:
        """Return score values in a region."""
        for pos, _, _, values in self.fetch_region(
                chrom, pos_begin, pos_end, scores):
            yield pos, pos, values

    def fetch_region(
        self,
        chrom: str | None,
        pos_begin: int | None,
        pos_end: int | None,
        scores: list[str] | None = None,
    ) -> Generator[
            tuple[int, str | None, str | None, list[ScoreValue] | None],
            None, None]:
        """Return position score values in a region."""
        region_lines = self._fetch_region_lines(
            chrom, pos_begin, pos_end, scores,
        )
        first_line = next(region_lines, None)
        if first_line is None:
            return
        lchrom, _left, _right, val, line = first_line
        pos = line.pos_begin

        returned_region: tuple[
            str, int | None, int | None, list[ScoreValue] | None,
            set[tuple[str | None, str | None]],
        ] = (lchrom, pos, pos, val, {(line.ref, line.alt)})
        yield (pos, line.ref, line.alt, val)

        for lchrom, _left, _right, val, line in region_lines:
            pos = line.pos_begin
            returned_nucleotides = (line.ref, line.alt)
            if (pos, pos) == (returned_region[1], returned_region[2]):
                if returned_nucleotides in returned_region[4]:
                    logger.debug(
                        "multiple values for positions %s:%s "
                        "and nucleotides %s",
                        chrom, pos, returned_nucleotides)

                returned_region[4].add((line.ref, line.alt))
                yield (pos, line.ref, line.alt, val)
                continue
            prev_chrom = returned_region[0]
            if lchrom != prev_chrom:
                returned_region = (lchrom, None, None, None, set())
            prev_right = returned_region[2]
            if prev_right is not None and pos < prev_right:
                raise ValueError(
                    f"multiple values for positions [{pos}, {prev_right}]")
            returned_region = (
                lchrom, pos, pos, val, {(line.ref, line.alt)})
            yield (pos, line.ref, line.alt, val)

    def fetch_allele_line(
        self, chrom: str, pos: int, ref: str, alt: str,
    ) -> ScoreLineBase | None:
        """Fetch the exact score line matching the given allele."""
        for line in self.fetch_lines(chrom, pos, pos):
            if line.ref == ref and line.alt == alt:
                return line
        return None

    def fetch_scores(
        self, chrom: str, position: int,
        reference: str, alternative: str,
        scores: list[str] | None = None,
    ) -> dict[str, ScoreValue] | None:
        """Fetch score values at specified genomic position and nucleotide."""
        if chrom not in self.get_all_chromosomes():
            raise ValueError(
                f"{chrom} is not among the available chromosomes for "
                f"NP Score resource {self.resource_id}")

        lines = list(self.fetch_lines(chrom, position, position))
        if not lines:
            return None

        selected_line = None
        for line in lines:
            if line.ref == reference and line.alt == alternative:
                selected_line = line
                break

        if not selected_line:
            return None
        requested_scores = scores or self.get_all_scores()
        # Resolve names to definitions once for this point fetch.
        score_defs = [
            self.score_definitions[sc] for sc in requested_scores]
        return dict(zip(
            requested_scores,
            selected_line.get_values(score_defs),
            strict=True))

    def build_scores_agg(
        self, score_queries: list[AlleleScoreQuery],
    ) -> dict[str, AlleleScoreAggr]:
        """Deprecated. Use annotator-level aggregators instead."""
        warnings.warn(
            "build_scores_agg is deprecated and will be removed in a future "
            "version. Use annotator-level aggregators instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        score_aggs = {}
        for squery in score_queries:
            scr_def = self.score_definitions[squery.score]

            if squery.position_aggregator is not None:
                aggregator_type = squery.position_aggregator
            else:
                assert scr_def.pos_aggregator is not None
                aggregator_type = scr_def.pos_aggregator
            position_aggregator = Aggregator.build(aggregator_type)

            if squery.allele_aggregator is not None:
                aggregator_type = squery.allele_aggregator
            else:
                assert scr_def.allele_aggregator is not None
                aggregator_type = scr_def.allele_aggregator
            allele_aggregator = Aggregator.build(aggregator_type)
            score_aggs[squery.score] = AlleleScoreAggr(
                squery.score, position_aggregator, allele_aggregator)
        return score_aggs

    def fetch_scores_agg(
            self, chrom: str, pos_begin: int, pos_end: int,
            scores: list[AlleleScoreQuery] | None = None,
    ) -> list[Aggregator]:
        """Deprecated. Use annotator-level aggregators instead."""
        warnings.warn(
            "fetch_scores_agg is deprecated and will be removed in a future "
            "version. Use annotator-level aggregators instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        # pylint: disable=too-many-locals
        if chrom not in self.get_all_chromosomes():
            raise ValueError(
                f"{chrom} is not among the available chromosomes for "
                f"NP Score resource {self.resource_id}")

        if scores is None:
            scores = [
                AlleleScoreQuery(score_id)
                for score_id in self.get_all_scores()]

        score_aggs = self.build_scores_agg(scores)

        score_lines = list(self.fetch_lines(chrom, pos_begin, pos_end))
        if not score_lines:
            return [sagg.position_aggregator for sagg in score_aggs.values()]

        def aggregate_alleles() -> None:
            for sagg in score_aggs.values():
                sagg.position_aggregator.add(
                    sagg.allele_aggregator.get_final())
                sagg.allele_aggregator.clear()

        last_pos: int = score_lines[0].pos_begin
        for line in score_lines:
            if line.pos_begin != last_pos:
                aggregate_alleles()

            for sagg in score_aggs.values():
                val = line.get_score(sagg.score)
                left = (
                    max(pos_begin, line.pos_begin)
                )
                right = (
                    min(pos_end, line.pos_end)
                )
                for _ in range(left, right + 1):
                    sagg.allele_aggregator.add(val)
            last_pos = line.pos_begin
        aggregate_alleles()

        return [sagg.position_aggregator for sagg in score_aggs.values()]


@dataclass
class CNV:
    """Copy number object from a cnv_collection."""

    chrom: str
    pos_begin: int
    pos_end: int
    attributes: dict[str, Any]

    @property
    def size(self) -> int:
        return self.pos_end - self.pos_begin


@dataclass
class _CNVScoreDef(_ScoreDef):

    def __post_init__(self) -> None:
        if self.value_type is None:
            return
        default_na_values = {
            "str": {},
            "float": {"", "nan", ".", "NA"},
            "int": {"", "nan", ".", "NA"},
            "bool": {},
        }
        default_pos_aggregators = {
            "float": "mean",
            "int": "mean",
            "str": "join(,)",
            "bool": None,
        }
        default_allele_aggregators = {
            "float": "max",
            "int": "max",
            "str": "join(,)",
            "bool": None,
        }
        if self.pos_aggregator is None:
            self.pos_aggregator = default_pos_aggregators[self.value_type]
        if self.allele_aggregator is None:
            self.allele_aggregator = \
                default_allele_aggregators[self.value_type]
        if self.na_values is None:
            self.na_values = default_na_values[self.value_type]


class CnvCollection(GenomicScore):
    """A collection of CNVs."""

    def __init__(self, resource: GenomicResource):
        if resource.get_type() != "cnv_collection":
            raise ValueError(
                "The resource provided to CnvCollection should be of "
                f"'cnv_collection' type, not a '{resource.get_type()}'")
        super().__init__(resource)

    @staticmethod
    def get_schema() -> dict[str, Any]:
        schema = copy.deepcopy(GenomicScore.get_schema())
        scores_schema = schema["scores"]["schema"]["schema"]
        scores_schema["allele_aggregator"] = AGGREGATOR_SCHEMA
        return schema

    def open(self) -> CnvCollection:
        return cast(CnvCollection, super().open())

    def fetch_region_values(
        self,
        chrom: str | None = None,
        pos_begin: int | None = None,
        pos_end: int | None = None,
        scores: list[str] | None = None,
    ) -> Generator[
            tuple[int, int, list[ScoreValue] | None], None, None]:
        """Return score values in a region."""
        for _, start, stop, values, _ in self._fetch_region_lines(
                chrom, pos_begin, pos_end, scores):
            yield start, stop, values

    def fetch_cnvs(
        self, chrom: str,
        start: int, stop: int,
        scores: list[str] | None = None,
    ) -> list[CNV]:
        """Return list of CNVs that overlap with the provided region."""
        if not self.is_open():
            raise ValueError(f"The resource <{self.resource_id}> is not open")
        cnvs: list = []
        if chrom not in self.table.get_chromosomes():
            return cnvs

        lines = list(self.fetch_lines(chrom, start, stop))
        if not lines:
            return cnvs

        requested_scores = scores or self.get_all_scores()
        # Resolve names to definitions once for this fetch.
        score_defs = [
            self.score_definitions[score_id]
            for score_id in requested_scores]

        for line in lines:
            attributes = dict(zip(
                requested_scores, line.get_values(score_defs), strict=True))
            cnvs.append(CNV(line.chrom, line.pos_begin, line.pos_end,
                            attributes))
        return cnvs

    @staticmethod
    def _parse_scoredef_config(
        config: dict[str, Any],
    ) -> dict[str, _ScoreDef]:
        """Parse ScoreDef configuration."""
        scores = {}

        for score_conf in config["scores"]:
            value_parser = SCORE_TYPE_PARSERS[score_conf.get("type", "float")]

            col_name = score_conf.get("column_name") \
                or score_conf.get("name")
            col_index_str = score_conf.get("column_index") \
                or score_conf.get("index")
            col_index = int(col_index_str) if col_index_str else None

            hist_conf = build_histogram_config(score_conf)
            nuc_aggregator = score_conf.get("nucleotide_aggregator")
            allele_aggregator = score_conf.get("allele_aggregator")
            if nuc_aggregator is not None:
                logger.warning(
                    "Use of 'nucleotide_aggregator' is deprecated, use "
                    "'allele_aggregator' instead.")
                assert allele_aggregator is None
                allele_aggregator = nuc_aggregator

            score_def = _CNVScoreDef(
                score_id=score_conf["id"],
                desc=score_conf.get("desc", ""),
                value_type=score_conf.get("type"),
                pos_aggregator=score_conf.get("position_aggregator"),
                allele_aggregator=allele_aggregator,
                small_values_desc=score_conf.get("small_values_desc"),
                large_values_desc=score_conf.get("large_values_desc"),
                col_name=col_name,
                col_index=col_index,
                hist_conf=hist_conf,
                value_parser=value_parser,
                na_values=score_conf.get("na_values"),
            )

            scores[score_conf["id"]] = score_def
        return cast(dict[str, _ScoreDef], scores)


_INMEMORY_CNV_CACHE: dict[str, GenomicScore] = {}
_INMEMORY_CNV_CACHE_LOCK = Lock()


def build_score_from_resource(
    resource: GenomicResource,
) -> GenomicScore:
    """Build a genomic score resource and return the coresponding score."""
    if resource.get_type() == "position_score":
        return PositionScore(resource)
    if resource.get_type() == "np_score":
        logger.warning(
            "The resource type `np_score` is deprecated. "
            "Please use `allele_score` instead for resource %s.",
            resource.get_id())
        return AlleleScore(resource)
    if resource.get_type() == "allele_score":
        return AlleleScore(resource)

    if resource.get_type() == "cnv_collection":
        cache_id = f"{resource.get_id()}_{resource.get_repo_url()}"

        with _INMEMORY_CNV_CACHE_LOCK:
            if cache_id not in _INMEMORY_CNV_CACHE:
                score = CnvCollection(resource)
                if score is None:
                    raise ValueError(
                        f"Resource {resource.get_id()} is not of score type")
                _INMEMORY_CNV_CACHE[cache_id] = score
            return _INMEMORY_CNV_CACHE[cache_id]

    raise ValueError(
        f"Resource {resource.get_id()} is not of score type; "
        f"unexpected resource type {resource.get_type()}")


def build_score_from_resource_id(
    resource_id: str, grr: GenomicResourceRepo | None = None,
) -> GenomicScore:
    if grr is None:
        grr = build_genomic_resource_repository()
    return build_score_from_resource(grr.get_resource(resource_id))
