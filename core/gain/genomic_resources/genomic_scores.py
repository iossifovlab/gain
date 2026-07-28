# pylint: disable=too-many-lines
from __future__ import annotations

import abc
import copy
import enum
from collections.abc import Generator, Iterator
from dataclasses import dataclass
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
from gain.genomic_resources.resource_implementation import (
    get_base_resource_schema,
)
from gain.genomic_resources.score_def import (
    SCORE_TYPE_PARSERS,
    GenomicScoreDef,
    ScoreValue,
    ValueExtractor,
    _parse_column_address,
    extract_column_value,
    normalize_na_values,
)
from gain.genomic_resources.score_resource import ScoreResource
from gain.genomic_resources.vcf_scores import (
    extract_vcf_value,
    parse_vcf_scoredefs,
)

from .aggregators import AGGREGATOR_SCHEMA

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
    _extract_value: ValueExtractor

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
                # ``fetch_size`` is a budget in RECORDS per range query -- the
                # bigWig backend adapts its base-pair window toward it.  It was
                # called ``direct_fetch_size`` while a second, buffered fetch
                # strategy existed; that strategy is gone, so the name is too.
                # The rename is deliberately NOT aliased: the capability still
                # exists, so a config naming it the old way means something
                # specific, and failing validation lets the operator rename it
                # rather than silently getting the default.
                "fetch_size": {"type": "integer", "min": 1},
                # The buffered strategy's two knobs, kept in the schema and
                # ignored.  Unlike the rename above, these configure a feature
                # that no longer EXISTS, so there is nothing for an operator to
                # rename to -- refusing the resource would take it offline to
                # tell it that.  ``build_genomic_position_table`` warns.
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

            return parse_vcf_scoredefs(
                cast(dict[str, Any], self.table.header),
                config_scoredefs,
                merge=merge)

        if config_scoredefs is None:
            raise ValueError("No scores configured and not using a VCF")

        if isinstance(self.table, BigWigTable):
            return build_bigwig_scoredefs(self.config, config_scoredefs)

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
        error: since #239 there is no adapter score line to fall back to, so a
        backend leaving the flag False has nothing that can read it and we
        refuse rather than guess.  (Nothing in the tree reaches it: it guards a
        backend added later without its migration.)
        """
        if is_vcf:
            return extract_vcf_value
        if is_bigwig:
            if any(score_def.na_values
                   for score_def in self.score_definitions.values()):
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
            # that attribute directly.  This branch used to copy the same
            # string into ``score_index`` as well, which is what made that
            # field ``int | str`` and forced an ``isinstance`` assert at the
            # other end; the copy said nothing the original did not.  So all
            # that is left here is the invariant the copy used to assert.
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
          pysam handle it can no longer reach: ``table_loaded`` would still be
          False, so ``close()`` would not have been reached.  Raising first
          means there is nothing to leak.  The bigWig config validation sits
          here for exactly that reason.
        * ``table_loaded = True`` is what makes this score look open to
          everyone else: from that write on, another caller's open() takes the
          is_open() early return above and reads ``_extract_value`` straight
          away.  Routed last, that caller could catch the score
          published-but-unrouted -- and since #239 left the routing with no
          default at all, that caller reads an AttributeError.  Scores are
          shared across threads (the process-wide in-memory CNV cache;
          gain-web-api's thread pool), so the window is reachable; this
          ordering keeps the ROUTING out of it.  Pinned by
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
        self.na_values = normalize_na_values(
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
