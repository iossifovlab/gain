"""The count, sum and sum of squares a number histogram folds beside its
bars, and the mean and standard deviation read off them (gain#1589)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Final

import numpy as np

from gain import logging

logger = logging.getLogger(__name__)

#: The keys the accumulators are stored under, beside a histogram's
#: ``min_value`` / ``max_value``.  Owned here with :meth:`Moments.stored`
#: and :meth:`Moments.from_stored`, so the file format of the moments has
#: one home.
MOMENT_KEYS: Final = ("count", "sum", "sum_of_squares")

#: The rendered moments, one ``(label, value)`` pair per line of a
#: summary page's cell: ``n``, ``mean``, ``sd`` in that order.
MomentsSummary = tuple[tuple[str, str], ...]


@dataclass(slots=True)
class Moments:
    """Running count, sum and sum of squares of a weighted value stream.

    Weighted exactly as a histogram's bars are -- by the ``count`` handed
    to ``add_value`` -- so ``count`` is the bars' total plus the
    out-of-range counts, and an out-of-range value still enters ``sum``
    and ``sum_of_squares`` at its real value.  Only the three
    accumulators are stored; :attr:`mean` and :attr:`std` are derived on
    read.

    Plain doubles, no compensated summation: over a whole chromosome of
    a real score the cancellation in ``sum_of_squares / count - mean**2``
    costs about one digit of the sd.

    A stream nobody has folded into is a ``Moments`` at zero; a histogram
    whose stored file predates these keys has NO moments, which its
    owner spells ``None`` rather than a zero here.
    """

    count: int = 0
    sum: float = 0.0
    sum_of_squares: float = 0.0

    def add(self, value: float, count: int) -> None:
        """Fold one value, ``count`` times.

        The per-value twin of :meth:`add_batch`: the products are
        ``value * count`` and ``value * value * count``, in that order, so
        each term rounds exactly as the batch arm rounds it.
        """
        self.count += count
        self.sum += value * count
        self.sum_of_squares += value * value * count

    def add_batch(self, values: np.ndarray, weights: np.ndarray) -> None:
        """Fold ``(value, weight)`` pairs, vectorized.

        ``values`` are float64 and already free of nan.  The per-term
        products are the ones :meth:`add` folds -- ``value * weight``,
        then ``value * value * weight`` -- so the two arms round each
        term identically and differ only by the order of the additions:
        pairwise here (numpy's ``sum``), sequentially there.  They agree
        to rounding, not to the bit.

        One temporary, reused in place: a fresh 800 KB array per product
        of a 100k batch costs more than the reductions themselves.
        """
        products = values * weights
        self.sum += float(products.sum())
        np.multiply(values, values, out=products)
        np.multiply(products, weights, out=products)
        self.sum_of_squares += float(products.sum())
        self.count += int(weights.sum())

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
        mean = self.mean
        if mean is None:
            return None
        return self._std(mean)

    def _std(self, mean: float) -> float:
        variance = self.sum_of_squares / self.count - mean * mean
        if variance < 0:
            logger.warning(
                "negative variance %s from cancellation in "
                "sum_of_squares/count - mean**2 (count=%s, mean=%s); "
                "reporting an sd of 0",
                variance, self.count, mean)
            variance = 0.0
        return math.sqrt(variance)

    def summary(self) -> MomentsSummary | None:
        """``n``, ``mean`` and ``sd`` for a summary page, as ``(label,
        value)`` pairs; ``None`` when nothing folded.

        ``n`` with thousands separators, the other two at the three
        significant digits a histogram's ``values_domain`` uses.  The
        page lays the pairs out one per line, so every info page formats
        them here and nowhere else.
        """
        mean = self.mean
        if mean is None:
            return None
        return (
            ("n", f"{self.count:,}"),
            ("mean", f"{mean:0.3g}"),
            ("sd", f"{self._std(mean):0.3g}"),
        )

    @staticmethod
    def stored(moments: Moments | None) -> dict[str, Any]:
        """The accumulators under :data:`MOMENT_KEYS`, for a histogram file.

        As Python numbers whatever was folded (a numpy weight would
        otherwise reach ``json.dumps`` as an int64), and every key
        ``None`` for unknown moments -- which :meth:`from_stored` reads
        back as exactly that.
        """
        if moments is None:
            return dict.fromkeys(MOMENT_KEYS)
        return {
            "count": int(moments.count),
            "sum": float(moments.sum),
            "sum_of_squares": float(moments.sum_of_squares),
        }

    @staticmethod
    def from_stored(data: dict[str, Any]) -> Moments | None:
        """Read the accumulators out of a histogram file's mapping.

        ``None`` when the file has none: one written before the
        accumulators existed has no ``count`` key, and nothing rebuilds
        it on its own (the statistics hash is unchanged), so its moments
        stay unknown until the resource's statistics are next rebuilt.
        """
        if data.get("count") is None:
            return None
        return Moments(
            count=data["count"],
            sum=data["sum"],
            sum_of_squares=data["sum_of_squares"],
        )
