"""The typed definition of a fragment-score track (``binner_def``)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Self

from gain.binning.binners import canonical_json

__all__ = ["FragmentScoreDef"]


@dataclass(frozen=True)
class FragmentScoreDef:
    """A fragment-score track's definition.

    ``score_id`` is the score a fragment contributes, ``None`` for the
    constant value.  ``resource_query``, ``pool`` and ``name`` come from
    the entry.  The definition keeps them so it tells entries apart.  The
    definition does not store the track's in-memory name.
    """

    aggregator: str
    mode: str
    uncovered_value: float | None
    score_id: str | None
    resource_query: str
    pool: bool
    name: str | None = None

    def to_json(self) -> str:
        """The definition as canonical JSON."""
        return canonical_json({
            "aggregator": self.aggregator,
            "mode": self.mode,
            "uncovered_value": self.uncovered_value,
            "score_id": self.score_id,
            "resource_query": self.resource_query,
            "pool": self.pool,
            "name": self.name,
        })

    @classmethod
    def from_json(cls, text: str) -> Self:
        """The definition ``to_json`` wrote."""
        fields = json.loads(text)
        return cls(
            aggregator=fields["aggregator"], mode=fields["mode"],
            uncovered_value=fields["uncovered_value"],
            score_id=fields["score_id"],
            resource_query=fields["resource_query"], pool=fields["pool"],
            name=fields["name"])
