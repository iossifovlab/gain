"""The count, sum and sum of squares a number histogram folds beside its
bars, and the mean and standard deviation read off them (gain#1589)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from gain import logging

logger = logging.getLogger(__name__)


@dataclass
class Moments:
    """Running count, sum and sum of squares of a weighted value stream.

    Weighted exactly as a histogram's bars are -- by the ``count`` handed
    to ``add_value`` -- so ``count`` is the bars' total plus the
    out-of-range counts, and an out-of-range value still enters ``sum``
    and ``sum_of_squares`` at its real value.  Only the three
    accumulators are stored; :attr:`mean` and :attr:`std` are derived on
    read.

    Plain doubles suffice: measured over chr21 of CADD and AVI (1.2e8
    values a score), the cancellation in ``sum_of_squares / count -
    mean**2`` costs one digit, and a per-value fold agrees with the
    vectorized one to 1e-11.

    A stream nobody has folded into is a ``Moments`` at zero; a histogram
    whose stored file predates these keys has NO moments, which its
    owner spells ``None`` rather than a zero here.
    """

    count: int = 0
    sum: float = 0.0
    sum_of_squares: float = 0.0

    def add_batch(self, values: np.ndarray, weights: np.ndarray) -> None:
        """Fold ``(value, weight)`` pairs, vectorized.

        ``values`` are float64 and already free of nan.  The per-term
        products are ``value * weight`` and ``value * value * weight``,
        in that order, so a per-value fold that spells them the same way
        rounds each term identically and differs only by the order of the
        additions.
        """
        self.count += int(weights.sum())
        self.sum += float((values * weights).sum())
        self.sum_of_squares += float((values * values * weights).sum())

    def merge(self, other: Moments) -> None:
        """Add ``other``'s accumulators to this one's."""
        self.count += other.count
        self.sum += other.sum
        self.sum_of_squares += other.sum_of_squares

    @property
    def mean(self) -> float | None:
        """The weighted mean; ``None`` when nothing was folded."""
        if not self.count:
            return None
        return self.sum / self.count

    @property
    def std(self) -> float | None:
        """The POPULATION standard deviation; ``None`` when nothing folded.

        Divides by ``count``, not ``count - 1``: the histogram describes
        the whole resource, not a sample drawn from it.  A variance that
        rounds below zero -- possible only when the values barely vary
        around a large mean -- is clamped to zero, with a warning.
        """
        if not self.count:
            return None
        mean = self.sum / self.count
        variance = self.sum_of_squares / self.count - mean * mean
        if variance < 0:
            logger.warning(
                "negative variance %s from cancellation in "
                "sum_of_squares/count - mean**2 (count=%s, mean=%s); "
                "reporting an sd of 0",
                variance, self.count, mean)
            variance = 0.0
        return math.sqrt(variance)

    def to_dict(self) -> dict[str, Any]:
        """The three accumulators under their stored keys."""
        return {
            "count": self.count,
            "sum": self.sum,
            "sum_of_squares": self.sum_of_squares,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Moments | None:
        """Read the accumulators back; ``None`` when the file has none.

        A file written before the accumulators existed has no ``count``
        key, and nothing rebuilds it on its own (the statistics hash is
        unchanged): its moments stay unknown until the resource's
        statistics are next rebuilt.
        """
        if data.get("count") is None:
            return None
        return Moments(
            count=data["count"],
            sum=data["sum"],
            sum_of_squares=data["sum_of_squares"],
        )
