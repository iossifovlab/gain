from __future__ import annotations

import numpy as np
import yaml

from gain.genomic_resources.statistics.base_statistic import (
    Statistic,
    non_numeric_error,
)


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
        # State the numeric contract the way the histogram twin states it:
        # ``np.isnan`` is the thing that refuses a value this cannot fold, so
        # the contract is exactly what it accepts, and the statement is a
        # re-wording of its complaint rather than a second opinion about
        # types.  An isinstance check ahead of it would be both slower (this
        # runs per value of every record) and WRONG to write as an
        # allow-list: ``np.float32`` is not a ``float`` and ``np.bool_`` is
        # not an ``np.integer``, yet both fold here perfectly well.
        #
        # Skip nan as ``NumberHistogram.add_value`` does: a ``min(nan, x)`` /
        # ``max(nan, x)`` returns nan and would wipe the running extremum (and
        # a trailing nan would nullify the histogram via the view_range check).
        # A nan reaches here only as a literal value token that parsed to nan
        # but is not a configured NA sentinel; both are non-values for min/max.
        try:
            if value is None or np.isnan(value):
                return
        except TypeError as err:
            raise non_numeric_error(value, "a min/max") from err
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


class NullMinMaxValue(MinMaxValue):
    """A score's min/max, refused rather than measured.

    The min/max twin of
    :class:`~gain.genomic_resources.histogram.NullHistogram`, and it exists for
    the same reason: a reducer that refuses a value has to leave something
    behind that the rest of the pass can keep feeding harmlessly, so one score
    costs one score rather than the resource's whole statistics build
    (gain#1285, gain#1313).

    ``add_value`` is a no-op, which is what makes the refusal cost ONE
    exception rather than one per record -- the score is swapped out for this
    and every later record folds into nothing.  Without that latch a caller
    catching per value would pay a raise per record, which on a genome-scale
    resource is multiples of the pass it is protecting.

    It keeps the inherited nan min and max, so a caller that never learned
    about this type still reads it as a score with no values to bin.
    ``update_hist_confs`` does know about it, and nullifies it in a branch of
    its own -- the same outcome as the nan branch beside it, reached without
    depending on the seed, and worded from ``reason``: "the values could not
    be folded" and "there were no values" are different facts about a
    resource, and a curator reading a nullified score needs to know which.

    It deliberately does NOT override the inherited
    :meth:`MinMaxValue.serialize` / :meth:`MinMaxValue.deserialize`, which
    would write the nan seed and read back a plain ``MinMaxValue`` -- losing
    the refusal.  Those two have no callers on any live path (ADR 0001), and
    a refused min/max never reaches a file; a refusal travels between the
    region tasks and the merge as an OBJECT.  This is the one asymmetry with
    ``NullHistogram``, which avoids the trap by being a sibling of its
    histogram rather than a subclass.
    """

    def __init__(self, score_id: str, reason: str):
        super().__init__(score_id)
        self.reason = reason

    def add_value(
        self, value: float | None,  # ruff: ignore[unused-method-argument]
    ) -> None:
        # pylint: disable=unused-argument
        return

    def merge(
        self, other: Statistic,  # ruff: ignore[unused-method-argument]
    ) -> None:
        # pylint: disable=unused-argument
        # Stays refused: a region that folded values cannot un-refuse a score
        # another region could not fold.  ``merge_min_max`` states the other
        # direction, where the refusal has to travel INTO a live fold.
        return


class MinMaxValueStatisticMixin:

    @staticmethod
    def get_min_max_file(score_id: str) -> str:
        return f"min_max_{score_id}.yaml"
