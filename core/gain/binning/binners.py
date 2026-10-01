"""Binner kinds: how a run-definition entry becomes tracks and values.

Kinds are discovered through the ``gain.binning.binners`` entry-point
group, so a second kind -- a fragment-score binner, an external plugin --
registers the way every other gain plugin does, without editing the tool.
"""
from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from importlib.metadata import entry_points
from types import TracebackType
from typing import Any, ClassVar, Protocol

import numpy as np
import numpy.typing as npt

from gain.genomic_resources.aggregators import (
    AGGREGATOR_CLASS_DICT,
    NUMERIC_ONLY_AGGREGATORS,
    AggregatorDefinition,
    PositionScoreAggregationQuery,
    get_aggregator_class,
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


def _output_type(name: str, value_type: str) -> str | None:
    """The type the registered aggregator ``name`` answers for a score.

    An aggregator's declared output type, or -- for one that declares
    none and answers in its input's type -- the score's own
    ``value_type``, but only when the aggregator is numeric-only, so that
    the input is known to be a number.  ``mode`` also answers in its
    input's type yet accepts any type, so it answers ``None`` here.
    """
    declared = get_aggregator_class(name).output_value_type
    if declared is None and name in NUMERIC_ONLY_AGGREGATORS:
        return value_type
    return declared


def numeric_aggregators() -> list[str]:
    """The registered aggregators whose result is a number (D11).

    Those that declare a numeric output type, and the numeric-only ones
    that answer in their input's type (``sum``, ``product``) -- a binned
    score is numeric, so they answer a number too.  Read off the
    registry, so a numeric aggregator added to it is accepted here
    without a list to keep in step.
    """
    return sorted(
        name for name in AGGREGATOR_CLASS_DICT
        if _output_type(name, "float") in NUMERIC_VALUE_TYPES)


class RunDefinitionError(ValueError):
    """A run definition that cannot be resolved into a run."""


@dataclass(frozen=True)
class Track:
    """One column of the output: a score of a resource, reduced one way.

    ``binner`` names the kind that produces the column; it is not
    written to the file.
    """

    name: str
    resource_id: str
    score_id: str
    aggregator: str
    none_value_replacement: float | None
    binner: str


@dataclass(frozen=True)
class BinningJob:
    """What one task binds to: tracks read together from one binding.

    ``binner`` names the kind that binds it, which is how the task graph
    finds the binner.  ``tracks`` are the job's columns, in order; the
    block a binding returns for a region has one column per track.
    """

    binner: str
    tracks: tuple[Track, ...]


class BoundBinner(Protocol):
    """A job bound to its resources: bins them one region at a time."""

    def bin_region(
        self, region: BedRegion, bin_size: int,
    ) -> npt.NDArray[np.float64]:
        """Reduce the job's tracks over ``region`` on the global grid.

        Returns a float64 block of shape ``(n_bins, n_tracks)``: one row
        per grid bin of ``region``, one column per track of the job, in
        the job's order.
        """


class Binner(Protocol):
    """What a registered binner kind provides."""

    kind: ClassVar[str]

    @classmethod
    def parse_entry(
        cls, label: str, config: dict[str, Any], grr: GenomicResourceRepo,
    ) -> list[BinningJob]:
        """Resolve one run-definition entry into jobs.

        The entry's tracks are the jobs' tracks, in order.
        ``label`` names the entry in error messages (``binners[2]``).
        Raises :class:`RunDefinitionError` for an entry that cannot be
        resolved, an entry matching nothing included.
        """

    @staticmethod
    def bind(
        job: BinningJob, grr: GenomicResourceRepo,
    ) -> AbstractContextManager[BoundBinner]:
        """Bind ``job`` to its resources for as long as the ``with`` lasts.

        A task binds once and asks for every region of its bundle through
        the bound value, so a resource is opened once per task however
        many regions the bundle holds, and is released when the ``with``
        ends, whether the task succeeded or not.
        """


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


class PositionScoreBinding:
    """One track's score, open for as long as the ``with`` lasts.

    Entering opens the score once, whatever number of regions are then
    binned through it; leaving closes it, on success or failure alike.
    """

    def __init__(self, track: Track, score: PositionScore) -> None:
        self.track = track
        self.score = score

    def __enter__(self) -> PositionScoreBinding:
        self.score.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.score.close()

    def bin_region(
        self, region: BedRegion, bin_size: int,
    ) -> npt.NDArray[np.float64]:
        """Reduce the track over ``region`` to one float64 per grid bin.

        Consumes :meth:`PositionScore.get_score_in_bins` unchanged: it is
        the semantic reference for the global grid, the boundary split and
        first-record-wins.  A bin no record covers comes back ``None`` and
        is stored as NaN, unless the track's replacement made it count.

        Unconditionally, a chromosome the score never mentions included:
        that read folds an absent contig as one uncovered run of its own
        (gain#1211), so the chromosome's bins are uncovered like any
        other's.

        Returned as a block of one column, the job's one track.
        """
        track = self.track
        column = np.fromiter(
            (np.nan if value is None else value
             for _, _, value in self.score.get_score_in_bins(
                 region.chrom, region.start, region.stop, bin_size,
                 score=track.score_id,
                 aggregator=track.aggregator,
                 none_value_replacement=track.none_value_replacement)),
            dtype=np.float64)
        return column.reshape(-1, 1)


class PositionScoreBinner:
    """Bins ``position_score`` resources matched by a ``resource_query``."""

    kind: ClassVar[str] = "position_score_binner"

    ENTRY_KEYS: ClassVar[frozenset[str]] = frozenset({
        "resource_query", "search_term", "aggregator",
        "none_value_replacement"})

    @classmethod
    def parse_entry(
        cls, label: str, config: dict[str, Any], grr: GenomicResourceRepo,
    ) -> list[BinningJob]:
        """Resolve one entry's ``resource_query`` into one job per track.

        The query is always a repository search -- an exact id is the
        search that matches one resource -- restricted to position scores
        by the search's own ``resource_type`` filter, and ordered by
        resource id, so the track order is deterministic whatever the
        repository yields.  That filter needs no index of its own
        (gain#1212).  A ``search_term`` is the full-text index's filter,
        conjoined with the query (D7), and the one key that needs the
        index.
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
            BinningJob(binner=cls.kind, tracks=(cls._track_of(
                label, resource,
                aggregator=config.get("aggregator"),
                none_value_replacement=config.get("none_value_replacement"),
            ),))
            for resource in matches
        ]

    @staticmethod
    def bind(
        job: BinningJob, grr: GenomicResourceRepo,
    ) -> PositionScoreBinding:
        """Bind the job's one track to its score.

        The score is opened when the binding is entered and closed when
        it is left.
        """
        (track,) = job.tracks
        return PositionScoreBinding(
            track, PositionScore(grr.get_resource(track.resource_id)))

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
        output_type = _output_type(
            AggregatorDefinition.from_string(aggregator_name)
            .aggregator_type,
            score_def.value_type)
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
