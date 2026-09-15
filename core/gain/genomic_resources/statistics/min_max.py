from __future__ import annotations

import numpy as np
import yaml

from gain.genomic_resources.statistics.base_statistic import (
    PYTHON_NUMBER_TYPES,
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

    def add_value(self, value: float | np.generic | None) -> None:
        """Fold one value into the running extremum.

        The same numeric contract as ``NumberHistogram.add_value``, stated
        the same way and in the same order, so that a nullified score's
        reason reads alike whichever twin refused (gain#1313, gain#1358).
        The agreement is pinned value by value in
        ``test_numeric_reducer_twins``.
        """
        # ``np.isnan`` is the first refusal: it raises on text and
        # ``Decimal``, and re-wording its complaint costs ~nothing when it
        # does not fire, where an isinstance guard AHEAD of it costs this
        # per-record path measurably (gain#1313).
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

        # Reached only by values ``np.isnan`` accepted -- which is not the
        # same as values a min/max can fold: a complex or a 0-d array passes
        # it, and ``min()`` would order either without complaint and leave
        # the extremum holding it (gain#1358).  A Python number folds as-is;
        # a numpy scalar folds as the Python value it holds, never as the
        # numpy object, so the extremum a histogram later reads its
        # ``view_range`` from is not in float32 by accident; anything else is
        # refused naming what the CALLER handed over.
        #
        # The allow-list is checked after ``item()``, not before, which is
        # how it admits ``np.float32`` (not a ``float``) and ``np.bool_``
        # (not an ``np.integer``) -- the trap an allow-list ahead of the
        # normalization fell into on the histogram side (gain#1338).
        if not isinstance(value, PYTHON_NUMBER_TYPES):
            folded = value.item() if isinstance(value, np.generic) else value
            if not isinstance(folded, PYTHON_NUMBER_TYPES):
                raise non_numeric_error(value, "a min/max")
            value = folded
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
        self,
        value: float | np.generic | None,  # ruff: ignore[unused-method-argument]
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
