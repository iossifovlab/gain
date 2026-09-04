"""Binner kinds: how a run-definition entry becomes tracks and values.

Kinds are discovered through the ``gain.binning.binners`` entry-point
group, so a second kind -- a fragment-score binner, an external plugin --
registers the way every other gain plugin does, without editing the tool.
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Any

from gain.genomic_resources.genomic_scores.position import PositionScore
from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceRepo,
)

BINNERS_ENTRY_POINT_GROUP = "gain.binning.binners"


@dataclass(frozen=True)
class Track:
    """One column of the output: a score of a resource, reduced one way."""

    name: str
    resource_id: str
    score_id: str
    aggregator: str
    none_value_replacement: float | None


class PositionScoreBinner:
    """Bins ``position_score`` resources matched by a ``resource_query``."""

    kind = "position_score_binner"

    @classmethod
    def parse_entry(
        cls, config: dict[str, Any], grr: GenomicResourceRepo,
    ) -> list[Track]:
        """Resolve one entry's ``resource_query`` into tracks.

        The query is always a repository search -- an exact id is the
        search that matches one resource -- restricted to position scores
        and ordered by resource id, so the track order is deterministic
        whatever the repository yields.

        The type restriction is applied here rather than through the
        search's ``resource_type`` filter: that filter is answered by the
        full-text index, and a ``resource_query`` on its own works on a
        repository that has no index at all.
        """
        matches = sorted(
            (
                resource
                for resource in grr.search_resources(
                    resource_query=config["resource_query"])
                if resource.get_type() == "position_score"
            ),
            key=lambda resource: resource.resource_id)
        return [
            cls._track_of(
                resource,
                aggregator=config.get("aggregator"),
                none_value_replacement=config.get("none_value_replacement"),
            )
            for resource in matches
        ]

    @staticmethod
    def _track_of(
        resource: GenomicResource, *,
        aggregator: str | None,
        none_value_replacement: float | None,
    ) -> Track:
        score = PositionScore(resource)
        (score_id, score_def), = score.score_definitions.items()
        if aggregator is None:
            aggregator = score_def.aggregator
        assert aggregator is not None
        return Track(
            name=resource.resource_id,
            resource_id=resource.resource_id,
            score_id=score_id,
            aggregator=aggregator,
            none_value_replacement=none_value_replacement,
        )


def discover_binner_kinds() -> dict[str, type[PositionScoreBinner]]:
    """Map every registered binner kind to its class."""
    return {
        entry.name: entry.load()
        for entry in entry_points(group=BINNERS_ENTRY_POINT_GROUP)
    }
