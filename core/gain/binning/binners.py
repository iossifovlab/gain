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
    Aggregator,
    PositionScoreAggregationQuery,
    validate_aggregator,
)
from gain.genomic_resources.genomic_scores.position import PositionScore
from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceRepo,
)
from gain.genomic_resources.resource_query import ResourceQueryParseError
from gain.genomic_resources.score_def import ScoreValue
from gain.utils.regions import (
    BedRegion,
    calc_bin_begin,
    calc_bin_end,
    calc_bin_index,
)

BINNERS_ENTRY_POINT_GROUP = "gain.binning.binners"

NUMERIC_VALUE_TYPES = {"int", "float"}


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


def check_keys(
    label: str, config: Any, known: frozenset[str],
    deferred: frozenset[str] = frozenset(),
) -> None:
    """Refuse a mapping with keys outside ``known``.

    A key in ``deferred`` is one the design accepts but a later slice
    builds; it is refused as not yet supported rather than dropped, so
    what the user wrote never silently changes what the run does.
    """
    if not isinstance(config, dict):
        raise RunDefinitionError(f"{label}: expected a mapping")
    for key in config:
        if key in deferred:
            raise RunDefinitionError(
                f"{label}: {key!r} is not yet supported")
        if key not in known:
            raise RunDefinitionError(
                f"{label}: unknown key {key!r}; known keys: "
                f"{', '.join(sorted(known))}")


def grid_bins(region: BedRegion, bin_size: int) -> list[tuple[int, int]]:
    """The ``(start, end)`` of every grid bin ``region`` touches.

    Bins follow the global grid anchored at position 1, as
    :meth:`PositionScore.get_score_in_bins` does, so bins from different
    runs tile; the edge bins are clipped to the region, so the bounds name
    exactly what was aggregated.
    """
    first = calc_bin_index(bin_size, region.start)
    last = calc_bin_index(bin_size, region.stop)
    return [
        (max(calc_bin_begin(bin_size, index), region.start),
         min(calc_bin_end(bin_size, index), region.stop))
        for index in range(first, last + 1)
    ]


class PositionScoreBinner:
    """Bins ``position_score`` resources matched by a ``resource_query``."""

    kind: ClassVar[str] = "position_score_binner"

    ENTRY_KEYS: ClassVar[frozenset[str]] = frozenset({
        "resource_query", "aggregator", "none_value_replacement"})
    # Accepted by the design (D7), built by the validation slice
    # (gain#1201).
    DEFERRED_KEYS: ClassVar[frozenset[str]] = frozenset({"search_term"})

    @classmethod
    def parse_entry(
        cls, label: str, config: dict[str, Any], grr: GenomicResourceRepo,
    ) -> list[Track]:
        """Resolve one entry's ``resource_query`` into tracks.

        The query is always a repository search -- an exact id is the
        search that matches one resource -- restricted to position scores
        and ordered by resource id, so the track order is deterministic
        whatever the repository yields.  The type restriction is applied
        here because the search's own ``resource_type`` filter is answered
        by the full-text index, which a repository need not have.
        """
        check_keys(label, config, cls.ENTRY_KEYS, cls.DEFERRED_KEYS)
        query = config.get("resource_query")
        if not isinstance(query, str) or not query:
            raise RunDefinitionError(
                f"{label}: resource_query is required and must be a string")
        try:
            found = grr.search_resources(resource_query=query)
        except ResourceQueryParseError as err:
            raise RunDefinitionError(f"{label}: {err}") from err
        matches = sorted(
            (r for r in found if r.get_type() == "position_score"),
            key=lambda resource: resource.resource_id)
        if not matches:
            # The one deliberate departure from the prototype, which
            # silently produced no column for a query matching nothing.
            raise RunDefinitionError(
                f"{label}: resource_query {query!r} matches no "
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
        """
        score = PositionScore(grr.get_resource(track.resource_id))
        with score.open():
            if region.chrom not in score.get_all_chromosomes():
                values = _uncovered_bins(track, region, bin_size)
            else:
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
        Only the two rules that are this slice's own -- exactly one score,
        numeric only -- are checked here.
        """
        score = PositionScore(resource)
        if len(score.score_definitions) != 1:
            raise RunDefinitionError(
                f"{label}: resource {resource.resource_id!r} defines "
                f"{sorted(score.score_definitions)}; binning a resource "
                f"with more than one score is not yet supported")
        (score_id, score_def), = score.score_definitions.items()
        if score_def.value_type not in NUMERIC_VALUE_TYPES:
            raise RunDefinitionError(
                f"{label}: resource {resource.resource_id!r} score "
                f"{score_id!r} is of type {score_def.value_type!r}; "
                f"binning a non-numeric score is not yet supported")
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
        assert replacement is None or isinstance(replacement, int | float)
        return Track(
            name=resource.resource_id,
            resource_id=resource.resource_id,
            score_id=score_id,
            aggregator=aggregator_name,
            none_value_replacement=replacement,
            binner=cls.kind,
        )


def _uncovered_bins(
    track: Track, region: BedRegion, bin_size: int,
) -> list[ScoreValue]:
    """Every bin of a chromosome the score holds no record for.

    A genome-wide run over a track that skips a chromosome is the normal
    case, not an error the table's region read should raise.  Each bin
    is what ``get_score_in_bins`` would yield for a run of uncovered
    positions: the replacement folded through the aggregator over the
    bin's width, or ``None`` when there is no replacement.

    A stand-in: the binned reads of ``PositionScore`` are the one home of
    this fold, and once they treat an absent contig as a single uncovered
    run this function and the guard that calls it go away.
    """
    if track.none_value_replacement is None:
        return [None] * len(grid_bins(region, bin_size))
    aggregator = Aggregator.build(track.aggregator)
    values: list[ScoreValue] = []
    for start, end in grid_bins(region, bin_size):
        aggregator.add(track.none_value_replacement, end - start + 1)
        values.append(aggregator.get_final())
        aggregator.clear()
    return values


def discover_binner_kinds() -> dict[str, type[Binner]]:
    """Map every registered binner kind to its class, by the class's kind."""
    kinds: dict[str, type[Binner]] = {}
    for entry in entry_points(group=BINNERS_ENTRY_POINT_GROUP):
        binner = entry.load()
        kinds[binner.kind] = binner
    return kinds
