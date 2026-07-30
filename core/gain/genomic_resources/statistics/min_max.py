from __future__ import annotations

import numpy as np
import yaml

from gain.genomic_resources.statistics.base_statistic import Statistic


class MinMaxValue(Statistic):
    """Statistic that calculates Min and Max values in a genomic score."""

    def __init__(
        self,
        score_id: str,
        min_value: float = np.nan,
        max_value: float = np.nan,
    ):
        super().__init__("min_max", "Calculates Min and Max values")
        self.score_id = score_id
        self.min = min_value
        self.max = max_value

    def add_value(self, value: float | None) -> None:
        # Skip nan as ``NumberHistogram.add_value`` does: a ``min(nan, x)`` /
        # ``max(nan, x)`` returns nan and would wipe the running extremum (and
        # a trailing nan would nullify the histogram via the view_range check).
        # A nan reaches here only as a literal value token that parsed to nan
        # but is not a configured NA sentinel; both are non-values for min/max.
        if value is None or np.isnan(value):
            return
        self.min = min(value, self.min)
        self.max = max(value, self.max)

    def merge(self, other: Statistic) -> None:
        if not isinstance(other, MinMaxValue):
            raise TypeError("unexpected type of statistics to merge with")
        if self.score_id != other.score_id:
            raise ValueError(
                "Attempting to merge min max values of different scores!",
            )
        if np.isnan(self.min):
            self.min = min(other.min, self.min)
        else:
            self.min = min(self.min, other.min)
        if np.isnan(self.max):
            self.max = max(other.max, self.max)
        else:
            self.max = max(self.max, other.max)

    def serialize(self) -> str:
        return yaml.dump({
            "score_id": self.score_id,
            "min": self.min,
            "max": self.max,
        })

    @staticmethod
    def deserialize(content: str) -> MinMaxValue:
        # Unknown keys are ignored rather than rejected, so a file carrying
        # extra fields still reads.
        data = yaml.safe_load(content)
        return MinMaxValue(
            data["score_id"],
            data["min"],
            data["max"],
        )


class MinMaxValueStatisticMixin:

    @staticmethod
    def get_min_max_file(score_id: str) -> str:
        return f"min_max_{score_id}.yaml"
