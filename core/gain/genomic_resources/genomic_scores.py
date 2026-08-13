# pylint: disable=too-many-lines
from __future__ import annotations

import copy
import enum
import warnings
from abc import abstractmethod
from collections.abc import Generator, Iterator, Sequence
from itertools import starmap
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
from gain.genomic_resources.resource_errors import (
    backwards_records_error,
    overlapping_records_error,
)
from gain.genomic_resources.resource_implementation import (
    get_base_resource_schema,
)
from gain.genomic_resources.resource_types import (
    FRAGMENT_SCORE_TYPES,
    LEGACY_FRAGMENT_SCORE_TYPE,
    PREFERRED_FRAGMENT_SCORE_TYPE,
    warn_deprecated_spelling,
)
from gain.genomic_resources.score_def import (
    BULK_PARSEABLE_VALUE_TYPES,
    SCORE_TYPE_PARSERS,
    GenomicScoreDef,
    ScoreValue,
    ValueExtractor,
    _parse_column_address,
    extract_column_value,
    normalize_na_values,
)
from gain.genomic_resources.score_filter import (
    ScoreFilter,
    compile_score_filter,
)
from gain.genomic_resources.score_resource import ScoreResource
from gain.genomic_resources.vcf_scores import (
    extract_vcf_value,
    parse_vcf_scoredefs,
)
from gain.utils.regions import (
    calc_bin_begin,
    calc_bin_end,
    calc_bin_index,
)

from .aggregators import (
    AGGREGATOR_SCHEMA,
    Aggregator,
    PositionScoreAggregationQuery,
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

# One batch as :meth:`GenomicScore.fetch_region_value_arrays` produces it:
# the RAW one-based begin and end columns, plus one parsed value array per
# requested score id.  Named because the vectorized scan validators are
# transducers over a stream of these.
RecordArrays = tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]]


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
          this kind.  ``fetch_region_segment_scores`` is it applied to
          ``fetch_records``, and the statistics scan is it applied to
          ``validate_records(fetch_records(...))`` -- so a kind states its
          reading once and both consumers get it (ADR 0008).
        - validate_records(): the rule this kind's records must hold to,
          which only the statistics scan applies.
        - validate_record_arrays(): the same rule over a batch's columns,
          which only the statistics scan's vectorized path applies.

        The last two have no default.  A kind that inherited one would be
        validated by a rule nobody chose for it, which is the failure
        ADR 0008 exists to undo.

    See Also:
        - PositionScore: For position-based genomic scores
        - AlleleScore: For variant-specific genomic scores
        - GenomicResource: Base resource abstraction
        - GenomicPositionTable: Table format abstraction
    """

    # How much one of this kind's records counts when a region is aggregated:
    # the "one record, one count" rule that everything except a position score
    # follows.  This is also where :meth:`_record_weight` -- the per-record
    # weight the annotators' aggregation applies -- reads the rule from, so
    # the two cannot disagree.
    #
    # It is a MEASURE, not a rule about what the records may be; the latter is
    # each kind's own ``validate_records`` / ``validate_record_arrays`` body.
    RECORD_WEIGHT_IS_SPAN: ClassVar[bool] = False

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
    # Applied by :meth:`_apply_default_aggregator` to every score whose config
    # does not state an ``aggregator:``, which today is every deployed score:
    # 0 of 16502 resource configs set one.
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
                # bigWig fetch tuning.  ``fetch_size`` is a budget in
                # RECORDS per range query -- the bigWig backend adapts its
                # base-pair window toward it (see ``table_bigwig``).
                "fetch_size": {"type": "integer", "min": 1},
                # Accepted and ignored: they configure no capability.  They
                # stay in the schema because refusing the resource would take
                # it offline merely to report a dead key, and there is nothing
                # to rename them to.  ``build_genomic_position_table`` warns.
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
            # ``None`` means the config did not state a type, which is NOT
            # the same as "float" here: for a VCF score, silence means "take
            # the type the file's header declares" (see
            # ``parse_vcf_scoredefs``, which prefers the config's type and
            # falls back to the header's).  Defaulting at this point would
            # override an INFO field's declared ``int`` with ``float``.
            # ``_finish_scoredefs`` resolves it once the merge has happened.
            #
            # The value PARSER defaults to float regardless, as it always
            # has -- an unstated type has always been read as a float.
            value_parser = SCORE_TYPE_PARSERS[score_conf.get("type", "float")]

            col_name, col_index = _parse_column_address(score_conf)

            hist_conf = build_histogram_config(score_conf)

            score_def = GenomicScoreDef(
                score_id=score_conf["id"],
                desc=score_conf.get("desc", ""),
                value_type=score_conf.get("type"),
                # Left as the config stated it, ``None`` when unstated;
                # ``_build_scoredefs`` fills the resource type's default.
                aggregator=score_conf.get("aggregator"),
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

    def _finish_scoredefs(
        self, score_defs: dict[str, GenomicScoreDef],
    ) -> dict[str, GenomicScoreDef]:
        """Fill in what a definition cannot decide for itself.

        **The value type.**  ``type:`` is optional, and an unstated one is
        recorded as ``float`` -- what the value parser defaults to anyway.
        Recording it matters rather than leaving it ``None``:
        ``GenomicScoreDef.__post_init__`` returns early on a ``None`` type,
        which would skip ``na_values`` normalization and leave the raw config
        string in place.  That turns the NA check into a SUBSTRING test, so a
        score configured ``na_values: "-1"`` would read a real value of 1 as
        a null -- the exact defect ``normalize_na_values`` prevents.

        It is resolved HERE rather than at parse time because for a VCF
        score an unstated type means "the type the file's header declares",
        and defaulting before the merge would override a declared ``int``
        with ``float``.  Filling it afterwards leaves that inheritance
        intact and still leaves no definition without a type.

        **The aggregator.**

        The default depends on how the score is reduced, which is fixed by
        the resource type -- ``mean`` over a region of positions, ``max``
        over the alleles at one, ``join(,)`` rather than ``list`` for a
        fragment score's strings -- so it belongs to the score CLASS, not to
        the definition.  Resolving it here rather than in
        ``GenomicScoreDef.__post_init__`` is what lets one field replace the
        ``pos_aggregator``/``allele_aggregator`` pair.

        Applied at the convergence point of all three construction routes --
        the ``scores:`` block, a VCF header, a bigWig -- because a default
        applied in only one of them is the same bug in a new place: a
        VCF-derived def would arrive with ``aggregator=None``, and the
        fragment score annotator drops an attribute whose aggregator is None
        *silently*.
        """
        for score_def in score_defs.values():
            if score_def.value_type is None:
                score_def.value_type = "float"
                # __post_init__ skipped this when the type was unknown.
                # normalize_na_values is idempotent, so re-running it on an
                # already-normalized set is a no-op for every other score.
                score_def.na_values = normalize_na_values(
                    score_def.na_values, score_def.value_type)
            if score_def.aggregator is None:
                score_def.aggregator = \
                    self.DEFAULT_AGGREGATORS[score_def.value_type]
        return score_defs

    def _build_scoredefs(self) -> dict[str, GenomicScoreDef]:
        config_scoredefs = None
        if "scores" in self.config:
            config_scoredefs = self._parse_scoredef_config(self.config)

        if isinstance(self.table, VCFGenomicPositionTable):
            merge = bool(self.config.get("merge_vcf_scores", False))

            return self._finish_scoredefs(parse_vcf_scoredefs(
                cast(dict[str, Any], self.table.header),
                config_scoredefs,
                merge=merge))

        if config_scoredefs is None:
            raise ValueError("No scores configured and not using a VCF")

        if isinstance(self.table, BigWigTable):
            return self._finish_scoredefs(
                build_bigwig_scoredefs(self.config, config_scoredefs))

        return self._finish_scoredefs(config_scoredefs)

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
        :meth:`_resolve_score_indices` still runs after the score has published
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
            self._validate_scoredefs()
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
    def _backwards_record_error(record: Record) -> OSError:
        """Build the refusal for a record whose end precedes its begin.

        Returned rather than raised, so the raise stays at the site that read
        the slots -- and so the several sites that perform this check share
        one message.  Off the hot path by construction: a caller compares two
        integers per record and only calls this when the comparison fails.

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

        Three slot reads, no method call and no per-record object.

        For a caller that wants the two positions and not the chrom, this is
        the wrong door: it allocates a 3-tuple to carry a value the caller
        drops on the next line, which is what the segment and allele-point
        loops used to do per record (gain#823).  Those read their slots
        directly and raise :meth:`_backwards_record_error` themselves.  The
        method stays for the callers that DO want all three.
        """
        chrom = record[CHROM]
        pos_begin = record[POS_BEGIN]
        pos_end = record[POS_END]
        if pos_end < pos_begin:
            raise GenomicScore._backwards_record_error(record)
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

        Raises :class:`ScoreFilterError` on an expression that does not
        parse or that names a score this resource does not define.  See
        :func:`compile_score_filter` for what compiling settles,
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
        preserve by structuring this any other way.
        """
        records = self.table.get_records_in_region(chrom, pos_begin, pos_end)
        if score_filter is None:
            yield from records
            return
        score_filter.require_owner(self)
        for record in records:
            if score_filter(record):
                yield record

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
        :meth:`GenomicScoreDef.parse_array` defines a column parse for
        (:data:`BULK_PARSEABLE_VALUE_TYPES`) and no others.
        What a *consumer* additionally needs stays with the consumer: the
        statistics scan also requires a bounded region and a resource kind it
        is exercised against, and it keeps asking that itself (see
        ``GenomicScoreImplementation._bulk_scan_eligible``).  What it does
        NOT require is a particular record shape: the accumulator reads the
        kind's own ``RECORD_WEIGHT_IS_SPAN`` and the scan's door reads the
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
        Each column goes through :meth:`GenomicScoreDef.parse_array`, whose
        agreement with the per-value :meth:`GenomicScoreDef.parse_value` is
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
                else "not every requested score has a value type the column "
                     f"parse serves {sorted(BULK_PARSEABLE_VALUE_TYPES)}")
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
        # No cast needed: ``score_index`` is an ``int``.  A VCF score is
        # addressed by ``col_name`` and has none, which is how the type
        # already says the VCF backend does not reach here.
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
            RecordArrays, None, None]:
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

    def region_values_from_records(
        self,
        records: Iterator[Record],
        chrom: str,
        pos_begin: int | None = None,
        pos_end: int | None = None,
        scores: list[str] | None = None,
    ) -> Generator[
            tuple[int, int, list[ScoreValue]], None, None]:
        """Extract this kind's ``(begin, end, values)`` from raw records.

        The region read expressed as a function OF a record stream, which is
        what lets the two consumers of a region differ by what they COMPOSE
        rather than by a flag: :meth:`fetch_region_segment_scores` is this
        applied to :meth:`fetch_records`, and the statistics scan is this
        applied to
        ``validate_records(fetch_records(...))``.  Neither can quietly
        acquire the other's behaviour, and no argument travels down to say
        which of the two is reading (ADR 0008).

        ``chrom``, ``pos_begin`` and ``pos_end`` name the region the records
        were asked for.  Nothing is fetched here, but the span is what the
        values are clipped to, and it is what the guards below are about.

        The guards run when this is CALLED rather than on the first
        ``next()`` -- the pattern :meth:`fetch_records` documents -- which is
        why the streaming half lives in :meth:`_clipped_score_values`.  They
        stay here rather than moving down into ``fetch_records``: that method
        is on the annotation hot path, where a per-call
        ``get_all_chromosomes()`` membership scan is a real cost.

        This base body yields every record clipped to the queried region,
        which is what a position score and a fragment score both mean by it.
        """
        score_defs = self._region_read_defs(chrom, scores)
        return self._clipped_score_values(
            records, pos_begin, pos_end, score_defs)

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

    def _clipped_score_values(
        self,
        records: Iterator[Record],
        pos_begin: int | None,
        pos_end: int | None,
        score_defs: list[GenomicScoreDef],
    ) -> Generator[
            tuple[int, int, list[ScoreValue]], None, None]:
        """Stream the clipped spans of an already-checked region request.

        A record ending before the query is dropped rather than clipped.
        Clipping one would put its begin at the query's start and its end
        behind it -- an inverted span, whose width as a weight is negative.
        The vectorized scan masks the same records on the same edge
        (``pos_end >= start``), so the two paths measure a region alike; a
        histogram must not depend on which of them a resource was eligible
        for.

        This does not refuse the record, because a backend answering a
        region query with a record outside it is misconfigured rather than
        holding bad data -- a table whose index and ``pos_end`` name
        different columns (gain#553), which ADR 0008 refuses at ``open()``
        and deliberately not here.

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
                raise self._backwards_record_error(record)
            if pos_begin is not None and rec_end < pos_begin:
                continue

            val = [extract(record, score_def) for score_def in score_defs]

            left = max(pos_begin, rec_begin) \
                if pos_begin is not None else rec_begin
            right = min(pos_end, rec_end) if pos_end is not None else rec_end
            yield (left, right, val)

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
        raises :meth:`_backwards_record_error` on a record whose end precedes
        its begin; there is no array counterpart, because no backend the bulk
        path reads can produce one (tabix refuses to index such a row, and a
        bigWig cannot express it).  If that ever stops being true, this is
        where the check belongs.

        ``chrom`` is what the batches were read for.  A bulk scan reads one
        region, which lies within one contig, so the implementations carry
        their ordering state across batches but never across contigs.

        Every kind states this itself; there is deliberately no default.
        """
        raise NotImplementedError

    def fetch_region_segment_scores(
        self,
        chrom: str,
        pos_begin: int | None = None,
        pos_end: int | None = None,
        scores: list[str] | None = None,
    ) -> Generator[
            tuple[int, int, list[ScoreValue]], None, None]:
        """Yield ``(begin, end, values)`` per record touching the region.

        One tuple per underlying RECORD -- a segment, with that record's own
        clipped span -- not one value per position.  The region's records,
        read as this kind means them.  A plain read: it checks nothing.  The
        statistics scan reads the same records through the same transform
        with :meth:`validate_records` composed in front, and that extra link
        -- visible at the consumer, in ``genomic_scores_impl.py`` -- is the
        whole of the difference between the two (ADR 0008).

        One body per kind, in :meth:`region_values_from_records`, rather than
        one per kind per consumer: two that had to agree is how the paths
        drift.
        """
        return self.region_values_from_records(
            self.fetch_records(chrom, pos_begin, pos_end),
            chrom, pos_begin, pos_end, scores)

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
            Use :meth:`fetch_region_segment_scores` instead -- the same
            read under a name that says it yields one tuple per record
            (a segment), not one value per position.  Retained as a thin
            compatibility alias only because the published
            ``docs/source/python_interface.rst`` showed this name to
            external readers; no in-tree or known cross-repo caller
            remains.  Removal is tracked as gain#730.
        """
        warnings.warn(
            "GenomicScore.fetch_region_values is deprecated; use "
            "fetch_region_segment_scores instead. It is retained only "
            "for readers of the published documentation, until gain#730 "
            "removes it.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.fetch_region_segment_scores(
            chrom, pos_begin, pos_end, scores)

    @classmethod
    def _record_weight(cls, left: int, right: int) -> int:
        """How many times one record's value counts when aggregating.

        The rule is a property of the resource TYPE, and ``WeightedValues``
        already states it: "a position-score record counts once per base
        pair of the queried region it covers, an allele line counts once, a
        fragment counts once however long it is".  One record, one count,
        is the answer for everything except a position score.

        **It is not stated here.**  This reads
        :attr:`RECORD_WEIGHT_IS_SPAN`, the kind's single statement of the
        weight rule, and turns it into the per-record number
        :meth:`aggregate_region` needs.  The statistics scan cannot call
        this -- the bulk path weighs a whole batch of records at once, with
        no record to hand it -- so it reads the flag directly.  Two
        readings, one rule: a kind that overrode this hook without the flag
        (or the reverse) would weigh its records one way when annotating and
        another when computing statistics, silently.  Pinned by
        test_the_weight_rule_is_stated_once_per_kind.

        This hook exists at all so :meth:`aggregate_region` can live on the
        base class and still agree with the annotators, which apply exactly
        this rule.  Deriving a weight from ``pos_begin``/``pos_end``
        unconditionally would give a fragment its length as a weight and
        silently disagree with the fragment score annotator for every
        fragment longer than one base pair.
        """
        return right - left + 1 if cls.RECORD_WEIGHT_IS_SPAN else 1

    def aggregate_region(
        self,
        chrom: str,
        pos_begin: int | None = None,
        pos_end: int | None = None,
        scores: list[str | tuple[str, str]] | None = None,
    ) -> list[ScoreValue]:
        """Reduce a region to one value per requested score.

        The aggregating counterpart of :meth:`fetch_region_segment_scores`,
        which it is built on: that method yields one entry per record, this
        one folds
        those entries into a single value per request.

        Each request is either a score id -- aggregated with the resource's
        own default, which :meth:`_finish_scoredefs` resolved from the score
        class's ``DEFAULT_AGGREGATORS`` -- or a ``(score_id, aggregator)``
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
        requests = self._resolve_aggregator_requests(scores)
        aggregators = list(starmap(self._build_region_aggregator, requests))
        # One fetch serves every request: ask for each DISTINCT score once,
        # then fan each record out to the requests that want it.  Two
        # requests for one score share the fetch and keep separate
        # accumulators.
        score_ids = list(dict.fromkeys(sid for sid, _ in requests))
        column_of = {sid: i for i, sid in enumerate(score_ids)}
        targets = [
            (aggregator, column_of[score_id])
            for aggregator, (score_id, _) in zip(
                aggregators, requests, strict=True)
        ]

        for left, right, values in self.fetch_region_segment_scores(
                chrom, pos_begin, pos_end, score_ids):
            weight = self._record_weight(left, right)
            if weight <= 0:
                continue
            for aggregator, column in targets:
                aggregator.add(values[column], weight)

        return [aggregator.get_final() for aggregator in aggregators]

    def _resolve_aggregator_requests(
        self, scores: list[str | tuple[str, str]] | None,
    ) -> list[tuple[str, str]]:
        """Normalize the request list to ``(score_id, aggregator)`` pairs."""
        if scores is None:
            scores = list(self.get_all_scores())

        requests = []
        for request in scores:
            score_id, aggregator = (
                (request, None) if isinstance(request, str) else request)
            score_def = self.score_definitions.get(score_id)
            if score_def is None:
                raise ValueError(
                    f"score {score_id!r} is not defined by resource "
                    f"{self.resource_id!r}; it has "
                    f"{sorted(self.score_definitions)}")
            resolved = aggregator or score_def.aggregator
            if resolved is None:
                # Every score has a value type, so the only way to get here
                # is a type whose class default is deliberately None --
                # ``bool``, which has no meaningful reduction to pick for
                # the caller.  Name one and it works.
                raise ValueError(
                    f"score {score_id!r} of resource {self.resource_id!r} "
                    f"has no default aggregator for value type "
                    f"{score_def.value_type!r}; name one explicitly as "
                    f"(score_id, aggregator)")
            requests.append((score_id, resolved))
        return requests

    def _build_region_aggregator(
        self, score_id: str, aggregator: str,
    ) -> Aggregator:
        """Build a FRESH aggregator, naming the resource if it cannot.

        Fresh per call, not reused: an aggregator is a mutable accumulator
        and explicitly not thread-safe (see :class:`Aggregator`).  Reuse is
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
                f"score {score_id!r} of resource {self.resource_id!r} asks "
                f"for aggregator {aggregator!r}, which is not valid: "
                f"{err}") from err


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
        ...     region = score.fetch_region_segment_scores(
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
        fetch_region_segment_scores: Iterate over score segments in a
            genomic region
        fetch_region_weighted_values: Iterate over ``(values, weight)`` pairs
            in a genomic region, for a caller that aggregates it
    """

    # A record counts once per base pair of the queried region it covers.
    # That one value per position is what a position score PROMISES is not
    # stated here but in ``validate_records`` / ``validate_record_arrays``,
    # the only places that enforce it.
    RECORD_WEIGHT_IS_SPAN: ClassVar[bool] = True

    # No ``_record_weight`` override: the base derives the per-record weight
    # from ``RECORD_WEIGHT_IS_SPAN`` above, so the span rule is stated once
    # and both the aggregation hook and the statistics scan read it there.

    # A region of positions reduces by ``mean``: each position's value counts
    # once per base pair it covers (see RECORD_WEIGHT_IS_SPAN).
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
        when the region is aggregated.  It is derived here, from the
        clipped bounds this layer already computes, so that a caller
        aggregating a region never re-clips a record nor materialises one
        copy of a value per base pair.
        """
        for left, right, values in self.fetch_region_segment_scores(
            chrom, pos_begin, pos_end, scores,
        ):
            weight = self._record_weight(left, right)
            if weight <= 0:
                continue
            yield (values, weight)

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
        for left, right, values in self.fetch_region_segment_scores(
                chrom, start, end, scores):
            if right < cursor:
                continue
            left = max(left, cursor)
            if left > right:
                # A record whose clipped span inverts -- a backend
                # answering outside the queried region, which the region
                # read yields through (see _clipped_score_values) and the
                # sibling consumers drop as a non-positive weight.
                continue
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

        An ``aggregator`` of ``None`` resolves to the score's own default
        from its definition; a ``none_value_replacement`` that does not
        match the score's ``value_type`` is refused, following
        ``validate_aggregator``'s precedent.
        """
        resolved = []
        for query in queries:
            score_def = self.score_definitions.get(query.score)
            if score_def is None:
                raise ValueError(
                    f"score {query.score!r} is not defined by resource "
                    f"{self.resource_id!r}; it has "
                    f"{sorted(self.score_definitions)}")
            self._validate_none_value_replacement(
                query.score, score_def.value_type,
                query.none_value_replacement)
            aggregator = query.aggregator or score_def.aggregator
            if aggregator is None:
                raise ValueError(
                    f"score {query.score!r} of resource "
                    f"{self.resource_id!r} has no default aggregator for "
                    f"value type {score_def.value_type!r}; name one on the "
                    f"query")
            resolved.append((
                query.score,
                self._build_region_aggregator(query.score, aggregator),
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
        ``aggregate_region`` allows.
        """
        resolved = self._resolve_aggregation_queries(queries)
        score_ids = [
            score_def.score_id
            for score_def in self._region_read_defs(
                chrom, list(dict.fromkeys(sid for sid, _, _ in resolved)))
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
        fetch_allele_scores: Get score values for a specific variant
        fetch_region_segment_scores: Iterate over allele scores in a
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

    # Several records share a position -- one per ref/alt pair -- and each
    # weighs 1.  Structurally so: :meth:`fetch_region_segment_scores` yields
    # ``(pos, pos, values)``, collapsing the record to a point however wide an
    # optional ``pos_end`` column reaches, so a span weight would not merely be
    # a different choice, it would disagree with the per-record read.
    RECORD_WEIGHT_IS_SPAN: ClassVar[bool] = False

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
        pos_begin: int | None = None,  # noqa: ARG002
        pos_end: int | None = None,  # noqa: ARG002
        scores: list[str] | None = None,
    ) -> Generator[
            tuple[int, int, list[ScoreValue]], None, None]:
        """Read each allele record as the point it sits at.

        Several records legitimately share a position -- one per ref/alt pair
        -- so each is yielded separately, and the span is the point
        ``(pos, pos)``: an allele's value stands for its ref/alt pair, not for
        the bases an optional ``pos_end`` column may cover.  A caller that
        needs the nucleotides themselves reads ``record[REF]`` /
        ``record[ALT]`` off :meth:`fetch_records`.

        Nothing is clipped, there being no span left to clip.  ``pos_begin``
        and ``pos_end`` are still taken, because they are what
        :meth:`GenomicScore.region_values_from_records` means by a region and
        this is one kind's answer to it.

        Nothing is checked either: every record is read, whatever its
        position is next to the one before it.  The rule an allele score's
        records hold to lives in :meth:`validate_records`, which the
        statistics scan composes over the stream it reads and no reader
        composes at all (ADR 0008).
        """
        score_defs = self._region_read_defs(chrom, scores)
        return self._allele_point_values(records, score_defs)

    def _allele_point_values(
        self,
        records: Iterator[Record],
        score_defs: list[GenomicScoreDef],
    ) -> Generator[
            tuple[int, int, list[ScoreValue]], None, None]:
        """Stream one point per allele record, for a checked request."""
        extract = self._extract_value
        for record in records:
            # The point is POS_BEGIN, but the ordering is still checked: a
            # record whose end precedes its begin is a different rule from
            # anything the scan validates, and one no reader can proceed past.
            # Read off the slots rather than through ``_record_to_begin_end``,
            # which would allocate a 3-tuple to carry back two values this
            # loop drops (gain#823); the extractor is likewise hoisted out of
            # the loop instead of reached through
            # ``get_score_values_from_record`` per record.
            pos = record[POS_BEGIN]
            if record[POS_END] < pos:
                raise self._backwards_record_error(record)
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

        Internal to the allele read: it hands back a RECORD, which is this
        class's own currency and not something a caller outside it should
        have to know how to read.  :meth:`fetch_allele_scores` is the
        allele read; a caller that wants records for a whole region asks
        :meth:`GenomicScore.fetch_records`.
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


class FragmentScore(GenomicScore):
    """A genomic score over fragments -- intervals carrying attributes.

    Nothing here is copy-number specific; a CNV collection is one
    application of it.  Accepts either resource type in
    :data:`FRAGMENT_SCORE_TYPES`, warning once per resource on the
    deprecated one.
    """

    # A fragment weighs 1 however long it is.
    RECORD_WEIGHT_IS_SPAN: ClassVar[bool] = False

    # As AlleleScore, except that strings join rather than list -- a fragment
    # score's string attributes are rendered into one cell.  Owned by the
    # score class, so no score-definition subclass is needed to carry them.
    DEFAULT_AGGREGATORS: ClassVar[dict[str, str | None]] = {
        "float": "max",
        "int": "max",
        "str": "join(,)",
        "bool": None,
    }

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

    @staticmethod
    def get_schema() -> dict[str, Any]:
        schema = copy.deepcopy(GenomicScore.get_schema())
        scores_schema = schema["scores"]["schema"]["schema"]
        scores_schema["aggregator"] = AGGREGATOR_SCHEMA
        return schema

    def validate_records(
        self, records: Iterator[Record],
    ) -> Generator[Record, None, None]:
        """Refuse a fragment that begins before the one before it.

        Fragments overlap freely and several may share a start, so only the
        BEGINS are compared, and only against each other.  A fragment's own end
        takes no part: an interval reaching back over its predecessor is the
        normal case, not a data error.

        The comparison is against RAW spans, the layer at which this rule and
        the vectorized one can ever be stated once (ADR 0008), and it starts
        afresh at every contig: "begins after the one before it" is a claim
        about one contig, and a second contig starting lower than the first
        ended is most resources.
        """
        prev_chrom: str | None = None
        prev_begin: int | None = None
        for record in records:
            chrom, begin, _end = self._record_to_begin_end(record)
            if chrom != prev_chrom:
                prev_begin = None
            if prev_begin is not None and begin < prev_begin:
                raise backwards_records_error(
                    self.resource_id, chrom, begin, prev_begin,
                    "a fragment score's")
            prev_chrom, prev_begin = chrom, begin
            yield record

    def validate_record_arrays(
        self, batches: Iterator[RecordArrays], chrom: str,
    ) -> Generator[RecordArrays, None, None]:
        """Refuse a fragment beginning before the one before it, vectorized.

        The same rule as :meth:`validate_records`, over a batch's columns:
        only the RAW begins are compared, and only against each other.  A
        fragment's own end takes no part -- an interval reaching back over
        its predecessor is the normal case for this kind, not a data error.
        A violation straddling a batch boundary is caught on the carried
        begin.
        """
        prev_begin: int | None = None
        for batch in batches:
            pos_begin, _pos_end, _cells = batch
            if pos_begin.size:
                if prev_begin is not None and int(pos_begin[0]) < prev_begin:
                    raise backwards_records_error(
                        self.resource_id, chrom, int(pos_begin[0]),
                        prev_begin, "a fragment score's")
                backwards = pos_begin[1:] < pos_begin[:-1]
                if bool(backwards.any()):
                    first = int(np.argmax(backwards))
                    raise backwards_records_error(
                        self.resource_id, chrom, int(pos_begin[first + 1]),
                        int(pos_begin[first]), "a fragment score's")
                prev_begin = int(pos_begin[-1])
            yield batch

    def fetch_fragment_scores(
        self, chrom: str,
        start: int, stop: int,
        scores: list[str] | None = None,
        *,
        score_filter: ScoreFilter | None = None,
    ) -> list[dict[str, ScoreValue]]:
        """Fetch score values for every fragment overlapping a region.

        One dict per overlapping fragment, keyed by score id, as
        :meth:`AlleleScore.fetch_allele_scores` keys one allele's values --
        the list is per fragment, not per score.  A region no fragment
        overlaps gives ``[]``; unlike the two per-position reads there is no
        ``None``, because several fragments overlapping is the normal case
        and "none of them" is a count of zero rather than absent data.

        ``score_filter`` -- from :meth:`GenomicScore.compile_filter` -- drops
        the fragments it rejects, which are then simply not among the dicts.
        It reads the RECORD, so it may name any score the resource defines,
        including one outside ``scores``, and a rejected fragment costs no
        extraction.

        A contig this resource does not have is a different answer: that is
        the caller asking about something that does not exist, and it is
        refused, as the per-position reads refuse it.  Answering ``[]`` would
        make "no fragments here" and "no such contig" indistinguishable.

        A fragment's own span is not reported.  Callers want the values it
        carries; a caller that needs the intervals themselves reads records
        through :meth:`fetch_records`.
        """
        if not self.is_open():
            raise ValueError(f"The resource <{self.resource_id}> is not open")
        if chrom not in self.get_all_chromosomes():
            raise ValueError(
                f"{chrom} is not among the available chromosomes.")

        requested_scores = scores or self.get_all_scores()
        score_defs = self._resolve_score_defs(requested_scores)

        records = list(self.fetch_records(
            chrom, start, stop, score_filter=score_filter))
        if not records:
            return []

        return [
            dict(zip(
                requested_scores,
                self.get_score_values_from_record(record, score_defs),
                strict=True))
            for record in records
        ]


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


def build_fragment_score_from_resource(
    resource: GenomicResource,
) -> FragmentScore:
    """Build a fragment score from a fragment-score resource.

    A fresh score every call, as the position and allele factories give:
    the caller owns what it gets back and may close it without affecting
    anyone else.
    """
    return FragmentScore(resource)


def build_fragment_score_from_resource_id(
    resource_id: str, grr: GenomicResourceRepo | None = None,
) -> FragmentScore:
    """Build a fragment score from a `cnv_collection` resource id."""
    if grr is None:
        grr = build_genomic_resource_repository()
    return build_fragment_score_from_resource(grr.get_resource(resource_id))


def build_score_from_resource(
    resource: GenomicResource,
) -> GenomicScore:
    """Build a genomic score resource and return the coresponding score.

    Dispatches on the resource type to the corresponding typed factory. Use
    the typed factories directly when the resource type is known statically;
    this one exists for callers handed a resource of unknown type.

    Every kind yields a fresh instance per call, so the caller owns the
    score it gets back and closing it affects nothing else.
    """
    resource_type = resource.get_type()
    if resource_type == "position_score":
        return build_position_score_from_resource(resource)
    if resource_type in {"allele_score", "np_score"}:
        return build_allele_score_from_resource(resource)
    if resource_type in FRAGMENT_SCORE_TYPES:
        return build_fragment_score_from_resource(resource)

    raise ValueError(
        f"Resource {resource.get_id()} is not of score type; "
        f"unexpected resource type {resource_type}")


def build_score_from_resource_id(
    resource_id: str, grr: GenomicResourceRepo | None = None,
) -> GenomicScore:
    if grr is None:
        grr = build_genomic_resource_repository()
    return build_score_from_resource(grr.get_resource(resource_id))
