"""Binner kinds: how a run-definition entry becomes tracks and values.

Kinds are discovered through the ``gain.binning.binners`` entry-point
group, so a second kind -- a fragment-score binner, an external plugin --
registers the way every other gain plugin does, without editing the tool.
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Any, ClassVar, Protocol

import numpy as np
import numpy.typing as npt

from gain.genomic_resources.aggregators import (
    AGGREGATOR_CLASS_DICT,
    Aggregator,
    PositionScoreAggregationQuery,
    validate_aggregator,
)
from gain.genomic_resources.genomic_scores.position import PositionScore
from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceRepo,
    SearchIndexUnavailableError,
    SearchTermError,
)
from gain.genomic_resources.resource_query import ResourceQueryParseError
from gain.utils.regions import BedRegion

BINNERS_ENTRY_POINT_GROUP = "gain.binning.binners"

NUMERIC_VALUE_TYPES = {"int", "float"}


def numeric_aggregators() -> list[str]:
    """The registered aggregators whose result is a number (D11).

    Read off each aggregator's declared output type, so a numeric
    aggregator added to the registry is accepted here without a list to
    keep in step; one that declares no output type of its own (``mode``,
    which answers in the input's type) is not among them.
    """
    return sorted(
        name for name, cls in AGGREGATOR_CLASS_DICT.items()
        if cls.output_value_type in NUMERIC_VALUE_TYPES)


class RunDefinitionError(ValueError):
    """A run definition that cannot be resolved into a run."""


@dataclass(frozen=True)
class Track:
    """One column of the output: a score of a resource, reduced one way.

    ``binner`` names the kind that produces the column; it is how the
    task graph finds the binner and is not written to the file.
    """

    name: str
    resource_id: str
    score_id: str
    aggregator: str
    none_value_replacement: float | None
    binner: str


class Binner(Protocol):
    """What a registered binner kind provides."""

    kind: ClassVar[str]

    @classmethod
    def parse_entry(
        cls, label: str, config: dict[str, Any], grr: GenomicResourceRepo,
    ) -> list[Track]:
        """Resolve one run-definition entry into tracks.

        ``label`` names the entry in error messages (``binners[2]``).
        Raises :class:`RunDefinitionError` for an entry that cannot be
        resolved, an entry matching nothing included.
        """

    @staticmethod
    def bin_track(
        track: Track, region: BedRegion, bin_size: int,
        grr: GenomicResourceRepo,
    ) -> npt.NDArray[np.float64]:
        """Reduce ``track`` to one float64 per grid bin of ``region``."""


def check_keys(label: str, config: Any, known: frozenset[str]) -> None:
    """Refuse a mapping with keys outside ``known``.

    A mistyped key is refused rather than dropped, so what the user wrote
    never silently changes what the run does.
    """
    if not isinstance(config, dict):
        raise RunDefinitionError(f"{label}: expected a mapping")
    for key in config:
        if key not in known:
            raise RunDefinitionError(
                f"{label}: unknown key {key!r}; known keys: "
                f"{', '.join(sorted(known))}")


class PositionScoreBinner:
    """Bins ``position_score`` resources matched by a ``resource_query``."""

    kind: ClassVar[str] = "position_score_binner"

    ENTRY_KEYS: ClassVar[frozenset[str]] = frozenset({
        "resource_query", "search_term", "aggregator",
        "none_value_replacement"})

    @classmethod
    def parse_entry(
        cls, label: str, config: dict[str, Any], grr: GenomicResourceRepo,
    ) -> list[Track]:
        """Resolve one entry's ``resource_query`` into tracks.

        The query is always a repository search -- an exact id is the
        search that matches one resource -- restricted to position scores
        by the search's own ``resource_type`` filter, and ordered by
        resource id, so the track order is deterministic whatever the
        repository yields.  That filter on its own is answered from the
        resources themselves and needs no index (gain#1212); with a
        ``search_term`` beside it the type joins that statement instead.
        A ``search_term`` is the full-text index's filter, conjoined with
        the query (D7), and the one key that needs the index.
        """
        check_keys(label, config, cls.ENTRY_KEYS)
        query = config.get("resource_query")
        if not isinstance(query, str) or not query:
            raise RunDefinitionError(
                f"{label}: resource_query is required and must be a string")
        search_term = config.get("search_term")
        if search_term is not None and not isinstance(search_term, str):
            raise RunDefinitionError(
                f"{label}: search_term must be a string, "
                f"not {search_term!r}")
        # A blank term is an unset one, as the repository reads it (what a
        # shell substitutes for a variable never set); settled once here
        # so the search and the messages below agree.
        if search_term is not None and not search_term.strip():
            search_term = None
        # The search is a generator: the query is checked when it is
        # made, but the term and the index are checked on the first
        # draw, so the consumption sits inside the same try.
        try:
            found = grr.search_resources(
                search_term=search_term, resource_query=query,
                resource_type="position_score")
            matches = sorted(
                found, key=lambda resource: resource.resource_id)
        except (ResourceQueryParseError, SearchTermError) as err:
            raise RunDefinitionError(f"{label}: {err}") from err
        except SearchIndexUnavailableError as err:
            # The repository's own message carries the remedy; only
            # which key needed the index is the entry's to add.
            raise RunDefinitionError(
                f"{label}: search_term {search_term!r} needs the "
                f"repository's full-text index: {err}") from err
        if not matches:
            # The one deliberate departure from the prototype, which
            # silently produced no column for a query matching nothing.
            narrowed = (
                f" with search_term {search_term!r}" if search_term else "")
            raise RunDefinitionError(
                f"{label}: resource_query {query!r}{narrowed} matches no "
                f"position_score resource")
        return [
            cls._track_of(
                label, resource,
                aggregator=config.get("aggregator"),
                none_value_replacement=config.get("none_value_replacement"),
            )
            for resource in matches
        ]

    @staticmethod
    def bin_track(
        track: Track, region: BedRegion, bin_size: int,
        grr: GenomicResourceRepo,
    ) -> npt.NDArray[np.float64]:
        """Reduce ``track`` to one float64 per grid bin of ``region``.

        Consumes :meth:`PositionScore.get_score_in_bins` unchanged: it is
        the semantic reference for the global grid, the boundary split and
        first-record-wins.  A bin no record covers comes back ``None`` and
        is stored as NaN, unless the track's replacement made it count.

        Unconditionally, a chromosome the score never mentions included:
        that read folds an absent contig as one uncovered run of its own
        (gain#1211), so a genome-wide run over a track that skips a
        chromosome needs no case here.  This method used to carry one, and
        with it a second copy of the fold; the read owning both is D14.
        """
        score = PositionScore(grr.get_resource(track.resource_id))
        with score.open():
            values = [
                value
                for _, _, value in score.get_score_in_bins(
                    region.chrom, region.start, region.stop, bin_size,
                    score=track.score_id,
                    aggregator=track.aggregator,
                    none_value_replacement=track.none_value_replacement)
            ]
        return np.array(
            [np.nan if value is None else value for value in values],
            dtype=np.float64)

    @classmethod
    def _track_of(
        cls, label: str, resource: GenomicResource, *,
        aggregator: str | None,
        none_value_replacement: Any,
    ) -> Track:
        """One track per matched resource, validated the score's own way.

        The score resolves the aggregator default and judges the
        replacement against its value type; the resolver stops at the
        aggregator's NAME, so that the name builds is asked separately.
        Only the rules that are the tool's own are checked here: a track
        is exactly one score, and every cell of ``/values`` is a float64
        (D11), so the score must be numeric and the aggregator must
        produce a number.
        """
        score = PositionScore(resource)
        if len(score.score_definitions) != 1:
            raise RunDefinitionError(
                f"{label}: resource {resource.resource_id!r} defines "
                f"{len(score.score_definitions)} scores, "
                f"{sorted(score.score_definitions)}; a track is one score, "
                f"and this binner takes a resource with exactly one")
        (score_id, score_def), = score.score_definitions.items()
        if score_def.value_type not in NUMERIC_VALUE_TYPES:
            raise RunDefinitionError(
                f"{label}: resource {resource.resource_id!r} score "
                f"{score_id!r} is of type {score_def.value_type!r}; "
                f"only a numeric score (int or float) can be binned")
        try:
            resolved = score.resolve_aggregation_queries([
                PositionScoreAggregationQuery(
                    score_id, aggregator, none_value_replacement),
            ])
            _, aggregator_name, replacement = resolved[0]
            validate_aggregator(aggregator_name, score_def.value_type)
        except ValueError as err:
            raise RunDefinitionError(
                f"{label}: resource {resource.resource_id!r}: "
                f"{err.args[0]}") from err
        output_type = Aggregator.resolve_class(
            aggregator_name).output_value_type
        if output_type not in NUMERIC_VALUE_TYPES:
            raise RunDefinitionError(
                f"{label}: resource {resource.resource_id!r}: aggregator "
                f"{aggregator_name!r} does not produce a number; use one "
                f"of {', '.join(numeric_aggregators())}")
        assert replacement is None or isinstance(replacement, int | float)
        return Track(
            name=resource.resource_id,
            resource_id=resource.resource_id,
            score_id=score_id,
            aggregator=aggregator_name,
            none_value_replacement=replacement,
            binner=cls.kind,
        )


def discover_binner_kinds() -> dict[str, type[Binner]]:
    """Map every registered binner kind to its class, by the class's kind."""
    kinds: dict[str, type[Binner]] = {}
    for entry in entry_points(group=BINNERS_ENTRY_POINT_GROUP):
        binner = entry.load()
        kinds[binner.kind] = binner
    return kinds
