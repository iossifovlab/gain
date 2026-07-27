# pylint: disable=too-many-lines
from __future__ import annotations

import abc
import contextlib
import copy
import enum
from collections.abc import Callable, Generator, Iterator
from dataclasses import dataclass, field
from threading import Lock
from types import TracebackType
from typing import (
    TYPE_CHECKING,
    Any,
    Self,
    cast,
)

import numpy as np

from gain import logging
from gain.genomic_resources.genomic_position_table import (
    VCFGenomicPositionTable,
    build_genomic_position_table,
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
    INFO,
    INFO_META,
)
from gain.genomic_resources.histogram import (
    build_histogram_config,
)
from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceRepo,
)
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)
from gain.genomic_resources.resource_implementation import (
    get_base_resource_schema,
)
from gain.genomic_resources.score_resource import ScoreDef, ScoreResource

from .aggregators import AGGREGATOR_SCHEMA

if TYPE_CHECKING:
    # Only ever needed to type the VCF INFO proxies in annotations.  pysam
    # is a hard runtime dep and is already imported by the VCF table anyway, but
    # keeping it behind TYPE_CHECKING makes it unambiguous that the annotations
    # cost nothing at runtime.
    pass

logger = logging.getLogger(__name__)

ScoreValue = str | int | float | bool | None

# Default rows-per-batch hint for GenomicScore.fetch_region_value_arrays.  Big
# enough that the per-batch numpy overhead disappears against the per-row work
# it replaces, small enough that one batch's arrays stay comfortably in cache.
DEFAULT_VALUE_ARRAYS_BATCH_SIZE = 100_000

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

_DEFAULT_NA_VALUES: dict[str, tuple[str, ...]] = {
    "str": (),
    "float": ("", "nan", ".", "NA"),
    "int": ("", "nan", ".", "NA"),
    "bool": (),
}

# Value types whose text sentinels are also coerced to the parsed representation
# so a numeric raw payload (e.g. a bigWig ``float``) matches by value, not text.
_NA_COERCIBLE_TYPES = ("int", "float")


def _normalize_na_values(na_values: Any, value_type: str) -> set[Any]:
    """Normalize a configured ``na_values`` into a type-aware sentinel set.

    The resource schema permits ``na_values`` as a bare scalar
    (``na_values: "-1"``) or a list.  A bare ``str`` left un-normalized turns
    the NA membership test in :func:`_extract_vcf_value` into a
    SUBSTRING test (``"1" in "-1"`` is ``True``) and raises ``TypeError`` when
    matched against a non-string raw payload (bigWig floats).  This wraps a
    scalar into a one-element collection and returns a set that carries, for
    every configured sentinel, both its text form (matched against string
    backends) and -- for numeric score types -- its parsed form (matched
    against a ``float``/``int`` raw payload).  So a sentinel is matched against
    whichever representation the incoming raw value presents, never by
    substring.

    ``na_values`` of ``None`` selects the per-value-type default set verbatim:
    the defaults are non-numeric tokens (``""``, ``"nan"``, ``"."``, ``"NA"``)
    that a numeric backend never presents as a raw value, so they are left as a
    pure-text set -- coercing them would only add a spurious parsed ``nan`` and
    change the default behaviour.

    A ``set`` input is treated as ALREADY normalized and returned as a copy
    without re-coercion, so normalization is idempotent (a fixed point).  This
    is what the VCF ``scores``-block merge path relies on: it rebuilds a
    ``GenomicScoreDef`` from an already-normalized ``na_values`` set, whose
    ``__post_init__`` re-runs this function -- a second coercion pass would
    otherwise grow the set (e.g. parsing the default ``"nan"`` text token into a
    ``float('nan')``) and silently change the statistics hash.  Config-supplied
    ``na_values`` never arrive as a ``set`` (the schema permits only ``None``,
    ``str`` or ``list``), so a ``set`` can only be a prior normalization result.
    """
    if na_values is None:
        return set(_DEFAULT_NA_VALUES.get(value_type, ()))
    if isinstance(na_values, set):
        return set(na_values)
    if isinstance(na_values, (list, tuple)):
        raw_sentinels: tuple[Any, ...] = tuple(na_values)
    else:
        # Any bare scalar -- a str ("-1"), or a non-iterable numeric sentinel
        # built in code -- is wrapped into a one-element collection, as the
        # docstring promises; iterating it directly would raise TypeError.
        raw_sentinels = (na_values,)

    sentinels: set[Any] = set()
    parser = SCORE_TYPE_PARSERS.get(value_type) \
        if value_type in _NA_COERCIBLE_TYPES else None
    for sentinel in raw_sentinels:
        text = str(sentinel)
        sentinels.add(text)
        if parser is not None:
            with contextlib.suppress(ValueError, TypeError):
                sentinels.add(parser(text))
    return sentinels


def _parse_column_address(
    score_conf: dict[str, Any],
) -> tuple[str | None, int | None]:
    """Read a score's configured column address as ``(name, index)``.

    A score is addressed either by column NAME or by column INDEX, never both
    -- the resource schema declares the two mutually exclusive, and each has a
    modern spelling (``column_name`` / ``column_index``) plus a legacy alias
    (``name`` / ``index``) that is still accepted.  Exactly one of the returned
    pair is non-``None`` for a well-formed config.

    **Index 0 is why this is a function.**  The obvious spelling of "modern key
    or legacy alias, converted" is

    .. code-block:: python

        col_index_str = conf.get("column_index") or conf.get("index")
        col_index = int(col_index_str) if col_index_str else None

    and it silently discards a legitimate ``column_index: 0``, twice over:
    ``0`` is falsy, so the ``or`` falls through to the legacy key (usually
    absent, giving ``None``), and the ternary would drop it even when reached
    directly.  Both tests have to be ``is None`` / ``is not None``, because the
    value being looked for is itself falsy.

    That was a real defect, not a hypothetical: with both keys discarded the
    score def carried ``col_index=None`` AND ``col_name=None``, so ``open()``
    took its by-name branch and died on a message-less assertion naming
    neither the resource nor the score.  Column 0 is a legal address -- the
    validator explicitly permits ``0 <= column_index`` -- so any resource
    whose score sits in the first column could not be opened at all.

    This lives at module level, not on a class, because ``CnvCollection``
    overrides ``_parse_scoredef_config`` with its own near-copy; parsed in one
    place, the two cannot drift, and the bug above cannot be fixed in one of
    them only.  (Pinned by test_column_index_zero_is_a_real_address.)
    """
    col_name = score_conf.get("column_name")
    if col_name is None:
        col_name = score_conf.get("name")

    col_index_raw = score_conf.get("column_index")
    if col_index_raw is None:
        col_index_raw = score_conf.get("index")
    col_index = int(col_index_raw) if col_index_raw is not None else None

    return col_name, col_index


@dataclass
class GenomicScoreDef(ScoreDef):
    """A genomic score definition. Includes backend loading internals.

    Extends the shared :class:`ScoreDef` (score id, value type, description
    and histogram config) with the concerns that are genomic-only: the
    per-position and per-allele default aggregators, and the internal column
    addressing / parsing state used when reading a value off a table backend.
    """

    # pylint: disable=too-many-instance-attributes
    pos_aggregator: str | None     # a valid aggregator type
    allele_aggregator: str | None  # a valid aggregator type

    col_name: str | None                       # internal
    col_index: int | None                      # internal

    value_parser: Any                             # internal
    na_values: Any                                # internal
    # The resolved payload column this score is read from, filled in by
    # ``GenomicScore.open`` -- the single form the read path uses, as against
    # ``col_name``/``col_index``, which are the two forms a config may state.
    #
    # ``init=False`` with no default, so the attribute does not EXIST until
    # open() resolves it.  That is the honest encoding of "not resolved yet":
    # reading it early raises ``AttributeError`` naming the attribute, where a
    # sentinel would have to be a real int -- and every candidate is a valid
    # index into a payload (``-1`` most treacherously, since it would quietly
    # read the last column instead of failing).
    #
    # Only column-addressed backends set it.  A VCF score is addressed by INFO
    # *name*, which is ``col_name``, and ``_extract_vcf_value`` reads that
    # directly;
    # a VCF score def therefore never has this attribute at all.  Nothing else
    # reads it: ``fetch_region_value_arrays`` does, but the VCF backend does
    # not serve that call (``supports_region_value_arrays``).
    score_index: int = field(init=False)        # internal

    def __post_init__(self) -> None:
        if self.value_type is None:
            return
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
        self.na_values = _normalize_na_values(
            self.na_values, self.value_type)

    def parse_value(self, value: str | int | float | None) -> ScoreValue:
        """Turn one raw cell into this score's value.

        ``None`` for a null raw value (an absent VCF INFO key), for a
        configured NA sentinel, and for a cell that fails to parse -- a bad
        cell is logged and skipped rather than aborting a whole scan.

        The scalar half of this definition's parsing contract; the column half
        is :meth:`parse_array`.  Both live here, on the object that owns the
        two inputs they need (``value_parser`` and ``na_values``), so neither
        can be changed against a config the other did not see.
        """
        if value is None or value in self.na_values:
            return None
        if self.value_parser is None:
            return value
        # pylint: disable=broad-except
        try:  # Temporary workaround for GRR generation
            parsed: ScoreValue = self.value_parser(value)
        except Exception:
            logger.exception(
                "unable to parse value %s for score %s",
                value, self.score_id)
            return None
        return parsed

    def _na_mask(self, cells: np.ndarray) -> np.ndarray:
        """Which cells are configured NA sentinels, vectorized.

        The array form of ``value in self.na_values``, and it has to be built
        by hand because ``np.isin(cells, list(self.na_values))`` is NOT that
        test.  ``na_values`` deliberately holds BOTH representations of each
        sentinel (``na_values: "-1"`` normalizes to ``{"-1", -1.0}``, so that a
        sentinel matches whichever form the backend presents), and handing that
        mixed list to numpy makes it coerce the lot to one dtype -- which broke
        both branches in opposite directions:

        * text cells: the float ``-1.0`` was stringified to ``"-1.0"``, so that
          spelling became an NA token the scalar test never treats as one, and
          real values were dropped;
        * float cells: every sentinel became a string, so ``isin`` compared
          float64 against ``<U32`` and was ALWAYS False -- the NA config simply
          did not apply, and a declared non-value was binned as real data.

        So each sentinel is matched against the representation the cells
        actually carry, which is what ``_normalize_na_values`` stores both
        forms for in the first place.  (``pd.Series.isin``, which this replaced
        in gain#385, is hash-based and had neither problem; the coercion came
        in with the switch to numpy.)
        """
        if cells.dtype.kind == "f":
            numeric = np.array(
                [value for value in self.na_values
                 if not isinstance(value, str)],
                dtype=np.float64)
            return np.isin(cells, numeric)
        text = np.array(
            [value for value in self.na_values if isinstance(value, str)],
            dtype=object)
        return np.isin(cells, text)

    def parse_array(self, cells: np.ndarray) -> np.ndarray:
        """Turn a whole column of raw cells into values, vectorized.

        The column half of this definition's parsing contract, and the reason
        the bulk statistics scan is worth having.  Equivalent to
        ``[parse_value(c) for c in cells]`` with ``None`` rendered as ``nan``
        -- a float64 array has no ``None``, and for every consumer a non-value
        and a nan are the same skip.  That equivalence is not an aspiration:
        test_parse_array_agrees_with_parse_value_fuzz asserts it token by
        token, over several ``na_values`` configs and several array widths.

        **Parsed with numpy, deliberately NOT with ``pd.to_numeric``**, which
        is not correctly rounded -- it returns 9.999999999999999e-26 for
        ``1e-25`` and truncates ``0.00000071009127180852`` to ten significant
        digits.  ``ndarray.astype`` agrees with ``float()`` on every token
        tested, including the PEP-515 underscores and Unicode digits pandas
        rejects outright.

        Float scores only, which is what ``_bulk_scan_eligible`` gates on: an
        ``int`` score would need ``int()`` semantics (``int("3.5")`` raises
        where ``float("3.5")`` does not).  Opening that gate is gain#405's
        follow-up, and this assert is what makes the limit visible until then.
        """
        assert self.value_type == "float", (
            f"parse_array is float-only; score {self.score_id} is "
            f"{self.value_type}")

        if cells.dtype.kind == "f":
            # Already numeric (a bigWig payload): nothing to parse, and the
            # per-record path does not parse it either.
            values = cells.astype(np.float64, copy=True)
            values[self._na_mask(cells)] = np.nan
            return values

        raw = np.asarray(cells, dtype=object)
        na_mask = self._na_mask(raw)
        work = raw.copy()
        # Substitute a parseable stand-in for each NA cell.  This one line
        # does both jobs: it is what makes an NA cell come out as nan, AND it
        # keeps a single "." sentinel from making the bulk astype raise and
        # sending the whole batch down the per-cell path below.  There used to
        # be a second ``values[na_mask] = np.nan`` after the parse as well; it
        # could never change an outcome, and two rounds of mutation testing
        # caught the comment here describing the pair's division of labour
        # wrongly, so it is gone rather than explained a third time.
        work[na_mask] = "nan"
        try:
            values = work.astype(np.float64)
        except (TypeError, ValueError):
            values = np.empty(work.shape, dtype=np.float64)
            failed = 0
            for idx, cell in enumerate(work):
                try:
                    values[idx] = float(cell)
                except (TypeError, ValueError):
                    values[idx] = np.nan
                    failed += 1
            if failed:
                # Once per batch, with a count.  The per-record path logs a
                # traceback per bad cell, which on a corrupt column means one
                # per row; saying it once keeps the signal that the bulk path
                # used to drop entirely without reproducing that flood.
                logger.warning(
                    "unable to parse %s of %s values for score %s",
                    failed, values.size, self.score_id)
        return values


def _extract_column_value(
    record: Record, score_def: GenomicScoreDef,
) -> ScoreValue:
    """Read one score off a record whose PAYLOAD is a raw row.

    The tabular backends and bigWig: a score is a CELL of the payload,
    addressed by the integer column ``GenomicScore.open`` resolved into
    ``score_index``.  Turning that cell into a value is the definition's job
    (:meth:`GenomicScoreDef.parse_value`), so that this read and the bulk
    column read cannot drift apart.

    A pure function of ``(record, score_def)`` -- it holds no state and needs
    none, which is what let the per-line score-line objects go.  There is no
    "score_index not resolved yet" check: the attribute does not exist until
    ``open()`` sets it, so an unopened def raises ``AttributeError`` naming it.
    """
    return score_def.parse_value(record[PAYLOAD][score_def.score_index])


def _extract_vcf_value(
    record: Record, score_def: GenomicScoreDef,
) -> ScoreValue:
    """Read one score off a VCF record: an INFO field, not a column.

    VCF is the awkward backend and this function is where the whole of its
    awkwardness lives.  A VCF score is addressed by INFO **name** -- which is
    ``col_name``, the string the config gave -- looked up on the variant,
    typed by the header metadata, and for a per-allele field selected by the
    record's allele index.

    The four cases:

    * **Number=A** -- one value per ALT allele: select this record's allele.
      A record whose ALT is absent ('.') has no allele index and so no
      applicable value -- under the VCF spec such a record has *zero* ALT
      alleles, so a Number=A field on it carries zero values and a row that
      supplies one anyway is malformed.  It yields ``None``, a null score,
      however many values the field carries and whatever the score def's
      declared type (#256).  Returning the null HERE also keeps the raw tuple
      from escaping as a score value.  The check is a crash guard too: without
      it the tuple is indexed with ``None`` and the read dies with
      ``TypeError``.
    * **Number=R** -- one value per allele *including the reference*, which
      occupies offset 0: an ALT allele reads at ``allele_index + 1``, and a
      record with no ALT reads the **reference** value at offset 0.
    * **Number=. and Type=String** -- an unbounded string field, joined on
      '|' into a single value (a VCF-local convention).
    * anything else -- handed to ``parse_value`` as pysam decoded it.

    A key the header **declares** but this record does not carry yields
    ``None`` rather than raising: ``info.get`` returns ``None``, ``None`` is
    not a tuple, so the number cases are skipped and ``parse_value`` turns it
    into a null score.  For a key the header does NOT declare, pysam's
    ``info.get`` raises ``ValueError: Invalid header`` -- but nothing in this
    tree can ask for one, since a VCF table's score defs are built FROM the
    header and a configured score naming an undeclared field is rejected when
    the score is opened (pinned by test_vcf_check_for_missing_score_columns).

    **The metadata lookup stays inside the tuple branch.**  ``INFO_META.get``
    builds a fresh pysam ``VariantMetadata`` for the key, per score, per
    record.  A ``Number=1`` field decodes to a scalar, never reaches that
    branch, and must not pay for a metadata object it will never read; that is
    the common shape of a score-bearing INFO field, and hoisting the lookup
    out of it took a 50-score read of a 3000-row VCF from 26.65 to
    19.83us/line.  (Pinned by
    test_vcf_reads_the_info_metadata_only_for_a_tuple_value.)

    The two pysam proxies this reads -- ``INFO`` and ``INFO_META`` -- are
    resolved once per record by the VCF backend and carried in the payload,
    because pysam allocates a fresh proxy on every ``variant.info`` access.
    See ``table_vcf`` for that measurement and why they live there.
    """
    key = score_def.col_name
    # ``col_name`` is declared ``str | None`` because a column-addressed score
    # leaves it None; ``open()`` refuses to open a VCF score without one.
    assert key is not None

    payload = record[PAYLOAD]
    value = payload[INFO].get(key)
    if isinstance(value, tuple):
        allele_index = payload[ALLELE_INDEX]
        meta = payload[INFO_META].get(key)
        number = meta.number
        if number == "A":
            if allele_index is None:
                return None
            value = value[allele_index]
        elif number == "R":
            value = value[
                allele_index + 1
                if allele_index is not None
                else 0  # Get reference allele value if ALT is '.'
            ]
        elif number == "." and meta.type == "String":
            value = "|".join(value)
    return score_def.parse_value(value)


# How a score's value is read off a record: chosen once per opened score by
# ``GenomicScore.open``, from the table's type, and called per value.
#
# This replaces the four score-line CLASSES the score layer used to route
# between (#239 had already reduced them to records plus a wrapper; this
# removes the wrapper).  A score line existed to hold two things -- which
# payload slot a score lives in, and, for VCF, the per-record pysam proxies --
# and neither needs an object any more: the first is ``score_index`` on the
# definition, the second is in the payload.  What is left is a value read that
# is a pure function of ``(record, score_def)``, so the per-line allocation
# goes and the routing stays exactly where it was.
_ValueExtractor = Callable[[Record, GenomicScoreDef], ScoreValue]


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

    # What each fetched line is wrapped in.  Installed by :meth:`open`, from
    # the table's ``yields_records`` claim, and declared here with NO default
    # on purpose: there is no longer a score line that suits an unrouted score.
    # Every backend yields records (#239 deleted the adapters), but a record's
    # payload still means two different things -- a raw row or a VCF
    # (variant, allele index) pair -- so there is no class that reads both, and
    # a default would have to be wrong for one of them.  Unset until open()
    # routes, an unopened score raises AttributeError rather than silently
    # reading a VCF record as a row; open() installs it *before* publishing
    # table_loaded, so no caller can observe the gap (see open()).
    _extract_value: _ValueExtractor

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
                    "histogram": ScoreResource.histogram_schema(),
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
                # bigWig fetch tuning.  The two ``*_fetch_size`` keys are
                # budgets in RECORDS per range query -- the bigWig backend
                # adapts its base-pair window toward them (see
                # ``table_bigwig``).  The backend has always read all three off
                # the table definition; before #259 the schema rejected them as
                # unknown fields, so configuring one failed validation outright.
                "direct_fetch_size": {"type": "integer", "min": 1},
                "buffer_fetch_size": {"type": "integer", "min": 1},
                "use_buffered_threshold": {"type": "integer", "min": 0},
            }},
            "scores": scores_schema,
            "default_annotation": {
                "type": ["dict", "list"], "allow_unknown": True,
            },
        }

    @staticmethod
    def _parse_scoredef_config(
        config: dict[str, Any],
    ) -> dict[str, GenomicScoreDef]:
        """Parse ScoreDef configuration."""
        scores = {}

        for score_conf in config["scores"]:
            value_parser = SCORE_TYPE_PARSERS[score_conf.get("type", "float")]

            col_name, col_index = _parse_column_address(score_conf)

            hist_conf = build_histogram_config(score_conf)
            nuc_aggregator = score_conf.get("nucleotide_aggregator")
            allele_aggregator = score_conf.get("allele_aggregator")
            if nuc_aggregator is not None:
                logger.warning(
                    "Use of 'nucleotide_aggregator' is deprecated, use "
                    "'allele_aggregator' instead.")
                assert allele_aggregator is None
                allele_aggregator = nuc_aggregator

            score_def = GenomicScoreDef(
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
        config_scoredefs: dict[str, GenomicScoreDef] | None, *,
        merge: bool = False,
    ) -> dict[str, GenomicScoreDef]:
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

            vcf_scoredefs[key] = GenomicScoreDef(
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

            scoredef = GenomicScoreDef(
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

    def _build_scoredefs(self) -> dict[str, GenomicScoreDef]:
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

    def close(self) -> None:
        self.table.close()
        self.table_loaded = False

    def is_open(self) -> bool:
        return self.table_loaded

    def open(self) -> Self:
        """Open genomic score resource and returns it."""
        if self.is_open():
            logger.info(
                "opening already opened genomic score: %s",
                self.resource.resource_id)
            return self
        is_vcf = isinstance(self.table, VCFGenomicPositionTable)
        # Choose the score line class per backend -- ONE decision, per table,
        # made here rather than per line.  A VCF table's scores are INFO fields,
        # so it goes to the extractor that performs the INFO lookup; any
        # other record-yielding table's scores are read out of the record's
        # payload by index, so it goes to the column extractor (every other
        # backend -- in-memory, tabix and bigWig).  This is decided at open
        # time, alongside the table's own parser/transform selection, and the
        # table's yields_records claim is simply believed -- that every
        # backend's claim matches what it really yields is pinned statically,
        # over all four of them, by test_backend_record_contract.py, so the
        # fetch path pays nothing.
        #
        # A table that claims neither is a programming error, not a data error:
        # since #239 there is no adapter score line to fall back to, so a
        # backend that leaves yields_records False has nothing that can read it
        # and we refuse to open rather than guess.  (Nothing in the tree reaches
        # this: it guards a backend added later without its migration.)
        #
        # Route BEFORE opening, and so before publishing.  Both inputs are
        # known at construction -- the table's class, and yields_records, a
        # ClassVar -- so routing needs nothing from the open handle and can
        # precede it.  Two things fall out of that order:
        #
        # * the refusal below costs no handle.  Routing after ``table.open()``
        #   would leave a caller that is not using the ``with`` form holding an
        #   opened pysam handle it can no longer reach: ``table_loaded`` would
        #   still be False, so ``close()`` would not have been reached.
        #   Raising first means there is nothing to leak.
        # * ``table_loaded = True`` is what makes this score look open to
        #   everyone else: from that write on, another caller's open() takes the
        #   is_open() early return above and reads _extract_value straight
        #   away.  Routed last, that caller can catch the score
        #   published-but-unrouted -- and since #239 left the routing with
        #   no default at all, that caller reads an AttributeError.  Scores are
        #   shared across threads (the process-wide in-memory CNV cache;
        #   gain-web-api's thread pool), so the window is reachable; this
        #   ordering keeps the ROUTING out of it.  Pinned by
        #   test_the_score_is_routed_before_it_reports_itself_open.
        #
        # It does not make open() as a whole safe to race, and does not claim
        # to: the score_index assignment below still runs after the score has
        # published itself open, so a caller that catches that window reads a
        # score def whose score_index is still None.  That window is older than
        # this routing and untouched by it -- open() is not synchronised, and
        # making it so is a separate change.
        if is_vcf:
            self._extract_value = _extract_vcf_value
        elif self.table.yields_records:
            self._extract_value = _extract_column_value
        else:
            raise TypeError(
                f"{type(self.table).__name__} does not yield records, so "
                f"there is no score line that can read it. A genomic "
                f"position table backend must set yields_records = True "
                f"and yield six-slot record tuples: see the record "
                f"contract in gain.genomic_resources."
                f"genomic_position_table.record, and "
                f"test_backend_record_contract.py for what that backend "
                f"is held to.")
        self.table.open()
        self.table_loaded = True
        if "scores" in self.config:
            self._validate_scoredefs()

        if is_vcf:
            # A VCF score has no column to resolve: it is addressed by INFO
            # KEY, which is ``col_name``, and :func:`_extract_vcf_value`
            # reads that
            # attribute directly.  This branch used to copy the same string
            # into ``score_index`` as well, which is what made that field
            # ``int | str`` and forced an ``isinstance`` assert at the other
            # end; the copy said nothing the original did not.  So all that is
            # left here is the invariant the copy used to assert.
            for score_def in self.score_definitions.values():
                if score_def.col_name is None:
                    raise ValueError(
                        f"score {score_def.score_id!r} of VCF resource "
                        f"{self.resource_id!r} has no INFO key; a VCF score "
                        f"is addressed by name")
        else:
            # Resolve each score's configured address to a payload column.
            #
            # Index first, because it needs nothing from the table -- only the
            # by-NAME case has to consult the header.  These raise rather than
            # assert: an assert here reported a misconfigured resource with a
            # message-less AssertionError naming neither the resource nor the
            # score, and ``python -O`` strips it altogether, leaving the by-name
            # branch to call ``header.index(None)`` on a table whose header may
            # itself be ``None``.  A resource config is data, and bad data is
            # reported, not asserted away.
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
    def _record_to_begin_end(record: Record) -> tuple[str, int, int]:
        """Read a record's three positional slots, checking their order.

        Three slot reads, no method call and no per-line object -- the score
        line this used to go through cost five property dispatches per record,
        each of which called ``typing.cast``.

        The message names the record by its DECODED slots rather than
        interpolating it.  A record's last slot is the backend's payload, so
        ``f"{record}"`` would print a whole ``pysam.VariantRecord`` (its repr
        is the entire VCF line) or a ``TupleProxy``; the retired score line
        had a ``__repr__`` written to avoid exactly that, and this reproduces
        what it said.
        """
        chrom = record[CHROM]
        pos_begin = record[POS_BEGIN]
        pos_end = record[POS_END]
        if pos_end < pos_begin:
            ref, alt = record[REF], record[ALT]
            ref_alt = f" {ref}->{alt}" if ref is not None or alt is not None \
                else ""
            raise OSError(
                f"The resource record {chrom}:{pos_begin}-{pos_end}{ref_alt} "
                f"has a region with end {pos_end} smaller than the "
                f"beginning {pos_begin}.")
        return chrom, pos_begin, pos_end

    def _get_header(self) -> tuple[Any, ...] | None:
        assert self.table is not None
        return self.table.header

    def fetch_records(
        self,
        chrom: str | None,
        pos_begin: int | None,
        pos_end: int | None,
    ) -> Iterator[Record]:
        """Fetch the records of a region.

        **Renamed from ``fetch_lines``, which no longer exists.**  That method
        wrapped every record in a per-line score-line object; this one hands
        the record itself over.  A caller reads the positional fields from the
        record's slots (``record[CHROM]``, ``record[POS_BEGIN]``, ...) and a
        score through :meth:`get_score_from_record` /
        :meth:`get_values_from_record` on this score.  There is deliberately
        no shim -- one would hand a caller back the exact per-line allocation
        this removal exists to avoid, which is the trade #239 examined and
        rejected for the line adapters.

        This adds nothing to what the table yields; it exists for the error
        context, and so that a caller need not reach past the score to its
        table.
        """
        try:
            yield from self.table.get_records_in_region(
                chrom, pos_begin, pos_end)
        except Exception:
            logger.exception(
                "Error fetching records for region %s:%s-%s in resource %s",
                chrom, pos_begin, pos_end, self.resource_id)
            raise

    def get_score_from_record(
        self, record: Record, score_id: str,
    ) -> ScoreValue:
        """Read one configured score off a record of this score's table."""
        return self._extract_value(record, self.score_definitions[score_id])

    def get_values_from_record(
        self, record: Record, score_defs: list[GenomicScoreDef],
    ) -> list[ScoreValue]:
        """Read several scores off one record, for ALREADY-resolved defs.

        The bulk counterpart of :meth:`get_score_from_record`: a caller
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
        facade parses, so it is float-only (an ``int`` score needs ``int()``
        semantics -- ``int("3.5")`` raises where ``float("3.5")`` does not).
        What a *consumer* additionally needs stays with the consumer: the
        statistics scan also requires a position score, because its
        accumulators assume a span weight and one value per position, and it
        keeps asking that itself.

        Answerable on an UNOPENED score: the table and the score definitions
        are both built in ``__init__``, so nothing here touches the file.
        """
        if not self.table.supports_value_arrays:
            return False
        for score_id in scores:
            score_def = self.score_definitions.get(score_id)
            if score_def is None or score_def.value_type != "float":
                return False
        return True

    def fetch_region_value_arrays(
        self,
        chrom: str,
        pos_begin: int | None,
        pos_end: int | None,
        scores: list[str],
        *,
        batch_size: int = DEFAULT_VALUE_ARRAYS_BATCH_SIZE,
    ) -> Generator[
            tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]], None, None]:
        """Fetch a region as column arrays, without building a record per row.

        The bulk counterpart of :meth:`fetch_records`, for a caller that scans a
        whole region and wants columns rather than rows -- statistics, above
        all.  Each batch is ``(pos_begin, pos_end, {score_id: values})``: the
        one-based position arrays, plus one ``float64`` array of **parsed**
        values per requested score.

        **Values are parsed, by the same contract the per-record read uses.**
        Each column goes through :meth:`GenomicScoreDef.parse_array`, whose
        agreement with the per-value :meth:`GenomicScoreDef.parse_value` is
        pinned by test_parse_array_agrees_with_parse_value_fuzz.  So NA
        sentinels and unparseable cells arrive as ``nan`` -- the array
        contract's "no value", the per-record contract's ``None`` -- and every
        backend yields ``float64``, whatever it stores underneath.

        That parse is why this is float-only, and why
        :meth:`supports_region_value_arrays` asks about the scores and not
        only about the backend.

        **It does NOT clip.**  A record overlapping the region's start is
        yielded whole, exactly as :meth:`fetch_records` yields it; trimming to
        ``[pos_begin, pos_end]`` is the caller's, because what a partial
        overlap means depends on what the caller is computing.

        ``batch_size`` is a HINT.  A backend whose read granularity is fixed by
        its own windowing -- ``BigWigTable``, whose batches are sized by its
        adaptive fetch window -- ignores it.

        Each score id gets an array of its own -- the parse builds one per
        id, so two ids sharing a payload column no longer alias, as they did
        while this method returned the backend's raw cells.

        The guards below run when this method is CALLED, not on the first
        ``next()`` -- which is why the streaming half lives in
        :meth:`_value_array_batches` rather than a ``yield`` here.
        """
        if not self.supports_region_value_arrays(scores):
            # Refuse here rather than let the call reach the table.  A VCF
            # table INHERITS the tabix implementation, so an unguarded call
            # does not fail cleanly -- it trips that method's
            # ``assert isinstance(self.pysam_file, pysam.TabixFile)`` and
            # yields a message-less AssertionError (nothing at all under
            # ``python -O``).  Probing this capability by catching is therefore
            # not viable; ask supports_region_value_arrays() first.
            reason = (
                f"its {type(self.table).__name__} backend leaves "
                f"supports_value_arrays False"
                if not self.table.supports_value_arrays
                else "not every requested score is a float score")
            raise TypeError(
                f"genomic score <{self.resource_id}> does not serve "
                f"fetch_region_value_arrays for {sorted(scores)}: {reason}. "
                f"Ask supports_region_value_arrays(scores) before calling.")
        if not self.is_open():
            raise ValueError(f"genomic score <{self.resource_id}> is not open")
        if chrom not in self.get_all_chromosomes():
            raise ValueError(
                f"{chrom} is not among the available chromosomes.")

        # score id -> payload column index, resolved once for the whole scan.
        # No cast: ``score_index`` IS an int.  It used to be ``int | str``,
        # the str being a VCF INFO name, and this had to assert -- by cast --
        # that the VCF backend never reaches here.  A VCF score is now
        # addressed by ``col_name`` and has no ``score_index`` at all, so the
        # claim is carried by the type instead of by a comment.
        columns = {
            score_id: self.score_definitions[score_id].score_index
            for score_id in scores
        }
        return self._value_array_batches(
            columns, (chrom, pos_begin, pos_end), batch_size)

    def _value_array_batches(
        self,
        columns: dict[str, int],
        region: tuple[str, int | None, int | None],
        batch_size: int,
    ) -> Generator[
            tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]], None, None]:
        """Stream the batches for an already-validated request.

        Split out so :meth:`fetch_region_value_arrays` is a plain function and
        its guards fire when it is CALLED.  Were it a generator itself, every
        one of those checks would be deferred to the first ``next()``, so a
        caller that built the generator and passed it elsewhere would be handed
        the refusal at some arbitrary later point, far from the mistake.
        """
        chrom, pos_begin, pos_end = region
        defs = {
            score_id: self.score_definitions[score_id]
            for score_id in columns
        }
        for begin, end, cells in self.table.get_region_value_arrays(
                chrom, pos_begin, pos_end, set(columns.values()), batch_size):
            yield begin, end, {
                score_id: defs[score_id].parse_array(cells[column])
                for score_id, column in columns.items()
            }

    def get_all_chromosomes(self) -> list[str]:
        if not self.is_open():
            raise ValueError(f"genomic score <{self.resource_id}> is not open")

        return self.table.get_chromosomes()

    def _fetch_region_records(
        self,
        chrom: str | None,
        pos_begin: int | None,
        pos_end: int | None,
        scores: list[str] | None = None,
    ) -> Generator[
            tuple[str, int, int, list[ScoreValue] | None, Record],
            None, None]:
        """Return score values in a region, with the record they came from.

        The last element used to be a score line; it is the record itself now.
        Two of the three callers discard it and the third reads only
        positional slots off it, so nothing was lost with the object.
        """
        if not self.is_open():
            raise ValueError(f"genomic score <{self.resource_id}> is not open")

        if chrom is not None and chrom not in self.get_all_chromosomes():
            raise ValueError(
                f"{chrom} is not among the available chromosomes.")

        if scores is None:
            scores = self.get_all_scores()
        # Hoist the score name->definition resolution out of the per-record
        # loop: it is fixed for the whole scan.  Resolve lazily on the first
        # record so that an empty region does not touch score_definitions --
        # matching the base behaviour where an unknown score id is only
        # rejected when there is a record to extract it from.
        score_defs: list[GenomicScoreDef] | None = None

        for record in self.fetch_records(chrom, pos_begin, pos_end):
            rec_chrom, rec_begin, rec_end = self._record_to_begin_end(record)
            if pos_begin is not None and rec_end < pos_begin:
                continue

            if score_defs is None:
                score_defs = [
                    self.score_definitions[scr_id] for scr_id in scores]
            val = self.get_values_from_record(record, score_defs)

            if pos_begin is not None:
                left = max(pos_begin, rec_begin)
            else:
                left = rec_begin
            right = min(pos_end, rec_end) if pos_end is not None else rec_end
            yield (rec_chrom, left, right, val, record)

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
        fetch_scores: Get score values at a specific position
        fetch_region: Iterate over score values in a genomic region
        fetch_region_weighted_values: Iterate over ``(values, weight)`` pairs
            in a genomic region, for a caller that aggregates it
    """

    def __init__(self, resource: GenomicResource):
        if resource.get_type() != "position_score":
            raise ValueError(
                "The resource provided to PositionScore should be of "
                f"'position_score' type, not a '{resource.get_type()}'")
        super().__init__(resource)

    @staticmethod
    def get_schema() -> dict[str, Any]:
        schema = copy.deepcopy(GenomicScore.get_schema())
        scores_schema = schema["scores"]["schema"]["schema"]
        scores_schema["position_aggregator"] = AGGREGATOR_SCHEMA
        return schema

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
        for lchrom, left, right, val, _ in self._fetch_region_records(
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

    def fetch_region_weighted_values(
        self,
        chrom: str | None = None,
        pos_begin: int | None = None,
        pos_end: int | None = None,
        scores: list[str] | None = None,
    ) -> Generator[
            tuple[list[ScoreValue] | None, int], None, None]:
        """Yield ``(values, weight)`` for every record touching the region.

        The weight of a position-score record is the number of base pairs
        of the queried region it covers -- how many times its value counts
        when the region is aggregated.  It is derived here, from the
        clipped bounds this layer already computes, so that a caller
        aggregating a region never re-clips a record nor materialises one
        copy of a value per base pair.
        """
        for left, right, values in self.fetch_region_values(
            chrom, pos_begin, pos_end, scores,
        ):
            weight = right - left + 1
            if weight <= 0:
                continue
            yield (values, weight)

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
        assert all(isinstance(s, str) for s in scores)

        records = list(self.fetch_records(chrom, position, position))
        if not records:
            return None

        if len(records) > 1:
            logger.warning(
                "multiple values for positions %s:%s",
                chrom, position)
            raise ValueError(
                f"multiple values ({len(records)}) for positions "
                f"{chrom}:{position}")

        requested_scores = scores or self.get_all_scores()
        # Resolve names to definitions once for this point fetch.
        score_defs = [
            self.score_definitions[scr] for scr in requested_scores]
        return self.get_values_from_record(records[0], score_defs)


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

    Aggregating those values over the region is the *annotator's* job, not the
    resource's -- see ``gain.annotation.score_annotator``.

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
                "The resource provided to AlleleScore should be of "
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
        region_records = self._fetch_region_records(
            chrom, pos_begin, pos_end, scores,
        )
        first = next(region_records, None)
        if first is None:
            return
        lchrom, _left, _right, val, record = first
        pos = record[POS_BEGIN]

        returned_region: tuple[
            str, int | None, int | None, list[ScoreValue] | None,
            set[tuple[str | None, str | None]],
        ] = (lchrom, pos, pos, val, {(record[REF], record[ALT])})
        yield (pos, record[REF], record[ALT], val)

        for lchrom, _left, _right, val, record in region_records:
            pos = record[POS_BEGIN]
            returned_nucleotides = (record[REF], record[ALT])
            if (pos, pos) == (returned_region[1], returned_region[2]):
                if returned_nucleotides in returned_region[4]:
                    logger.debug(
                        "multiple values for positions %s:%s "
                        "and nucleotides %s",
                        chrom, pos, returned_nucleotides)

                returned_region[4].add((record[REF], record[ALT]))
                yield (pos, record[REF], record[ALT], val)
                continue
            prev_chrom = returned_region[0]
            if lchrom != prev_chrom:
                returned_region = (lchrom, None, None, None, set())
            prev_right = returned_region[2]
            if prev_right is not None and pos < prev_right:
                raise ValueError(
                    f"multiple values for positions [{pos}, {prev_right}]")
            returned_region = (
                lchrom, pos, pos, val, {(record[REF], record[ALT])})
            yield (pos, record[REF], record[ALT], val)

    def fetch_allele_record(
        self, chrom: str, pos: int, ref: str, alt: str,
    ) -> Record | None:
        """Fetch the record matching the given allele exactly.

        Renamed from ``fetch_allele_line``, which returned a score line; the
        record's REF/ALT slots carry what its ``ref``/``alt`` properties did,
        and its scores are read through
        :meth:`GenomicScore.get_score_from_record`.
        """
        for record in self.fetch_records(chrom, pos, pos):
            if record[REF] == ref and record[ALT] == alt:
                return record
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

        selected = self.fetch_allele_record(
            chrom, position, reference, alternative)
        if selected is None:
            return None

        requested_scores = scores or self.get_all_scores()
        # Resolve names to definitions once for this point fetch.
        score_defs = [
            self.score_definitions[sc] for sc in requested_scores]
        return dict(zip(
            requested_scores,
            self.get_values_from_record(selected, score_defs),
            strict=True))


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
class _CNVScoreDef(GenomicScoreDef):

    def __post_init__(self) -> None:
        if self.value_type is None:
            return
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
        self.na_values = _normalize_na_values(
            self.na_values, self.value_type)


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

    def fetch_region_values(
        self,
        chrom: str | None = None,
        pos_begin: int | None = None,
        pos_end: int | None = None,
        scores: list[str] | None = None,
    ) -> Generator[
            tuple[int, int, list[ScoreValue] | None], None, None]:
        """Return score values in a region."""
        for _, start, stop, values, _ in self._fetch_region_records(
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

        records = list(self.fetch_records(chrom, start, stop))
        if not records:
            return cnvs

        requested_scores = scores or self.get_all_scores()
        # Resolve names to definitions once for this fetch.
        score_defs = [
            self.score_definitions[score_id]
            for score_id in requested_scores]

        for record in records:
            attributes = dict(zip(
                requested_scores,
                self.get_values_from_record(record, score_defs),
                strict=True))
            cnvs.append(CNV(record[CHROM], record[POS_BEGIN],
                            record[POS_END], attributes))
        return cnvs

    @staticmethod
    def _parse_scoredef_config(
        config: dict[str, Any],
    ) -> dict[str, GenomicScoreDef]:
        """Parse ScoreDef configuration."""
        scores = {}

        for score_conf in config["scores"]:
            value_parser = SCORE_TYPE_PARSERS[score_conf.get("type", "float")]

            col_name, col_index = _parse_column_address(score_conf)

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
        return cast(dict[str, GenomicScoreDef], scores)


_INMEMORY_CNV_CACHE: dict[tuple[str, str], CnvCollection] = {}
_INMEMORY_CNV_CACHE_LOCK = Lock()


def build_position_score_from_resource(
    resource: GenomicResource,
) -> PositionScore:
    """Build a position score from a `position_score` resource."""
    return PositionScore(resource)


def build_position_score_from_resource_id(
    resource_id: str, grr: GenomicResourceRepo | None = None,
) -> PositionScore:
    """Build a position score from a `position_score` resource id."""
    if grr is None:
        grr = build_genomic_resource_repository()
    return build_position_score_from_resource(grr.get_resource(resource_id))


def build_allele_score_from_resource(
    resource: GenomicResource,
) -> AlleleScore:
    """Build an allele score from an `allele_score` resource.

    The deprecated `np_score` resource type is accepted as well. It builds
    an `AlleleScore` that defaults to substitutions mode -- unless the
    resource configures `allele_score_mode` explicitly, which is honoured
    for either resource type.
    """
    return AlleleScore(resource)


def build_allele_score_from_resource_id(
    resource_id: str, grr: GenomicResourceRepo | None = None,
) -> AlleleScore:
    """Build an allele score from an `allele_score` resource id."""
    if grr is None:
        grr = build_genomic_resource_repository()
    return build_allele_score_from_resource(grr.get_resource(resource_id))


def build_cnv_collection_from_resource(
    resource: GenomicResource,
) -> CnvCollection:
    """Build a CNV collection from a `cnv_collection` resource.

    CNV collections are cached in memory and shared process-wide, keyed by
    versioned resource id and repository URL. Callers must not assume they
    own the returned object's lifecycle -- in particular, closing it closes
    it for every other holder (see gain#350).

    The key uses ``get_full_id()`` rather than ``get_id()``: the latter is
    version-less, so two versions of one resource id would share a single
    cache entry and the second caller would receive the first version's
    data.
    """
    cache_id = (resource.get_full_id(), resource.get_repo_url())

    with _INMEMORY_CNV_CACHE_LOCK:
        if cache_id not in _INMEMORY_CNV_CACHE:
            _INMEMORY_CNV_CACHE[cache_id] = CnvCollection(resource)
        return _INMEMORY_CNV_CACHE[cache_id]


def build_cnv_collection_from_resource_id(
    resource_id: str, grr: GenomicResourceRepo | None = None,
) -> CnvCollection:
    """Build a CNV collection from a `cnv_collection` resource id."""
    if grr is None:
        grr = build_genomic_resource_repository()
    return build_cnv_collection_from_resource(grr.get_resource(resource_id))


def build_score_from_resource(
    resource: GenomicResource,
) -> GenomicScore:
    """Build a genomic score resource and return the coresponding score.

    Dispatches on the resource type to the corresponding typed factory. Use
    the typed factories directly when the resource type is known statically;
    this one exists for callers handed a resource of unknown type.

    Beware the asymmetry inherited from the typed factories: a
    `cnv_collection` resource yields a process-wide CACHED, shared
    `CnvCollection`, while `position_score` and `allele_score` yield a fresh
    instance every call. A caller that closes what it got back therefore
    closes it for every other holder of the cached collection (see
    gain#350).
    """
    resource_type = resource.get_type()
    if resource_type == "position_score":
        return build_position_score_from_resource(resource)
    if resource_type in {"allele_score", "np_score"}:
        return build_allele_score_from_resource(resource)
    if resource_type == "cnv_collection":
        return build_cnv_collection_from_resource(resource)

    raise ValueError(
        f"Resource {resource.get_id()} is not of score type; "
        f"unexpected resource type {resource_type}")


def build_score_from_resource_id(
    resource_id: str, grr: GenomicResourceRepo | None = None,
) -> GenomicScore:
    if grr is None:
        grr = build_genomic_resource_repository()
    return build_score_from_resource(grr.get_resource(resource_id))
