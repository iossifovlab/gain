"""The count, sum and sum of squares a number histogram folds beside its
bars, and the mean and standard deviation read off them (gain#1589)."""
from __future__ import annotations

import math
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


#: Every finite double is an integer multiple of ``2**-1074``, the
#: smallest subnormal; :class:`_ExactSum` counts in those units.
_UNIT_EXPONENT: Final = 1074

#: The mantissas of a batch are split into a high and a low half of this
#: many bits, so that ``np.bincount`` adds each half exactly in float64.
_HALF_BITS: Final = 26

#: The most terms one ``np.bincount`` call adds.  A half is below
#: ``2**27`` in magnitude, so a sum of this many halves stays below
#: ``2**53`` and every float64 addition in it is exact.
_EXACT_CHUNK: Final = 1 << 25


class _ExactSum:
    """The exact sum of a stream of doubles, rounded once on read.

    The finite terms are held as one Python integer in units of
    ``2**-1074``, so the order of the additions cannot change the total.
    Infinities and nans are added as plain doubles beside it, so that they
    propagate as they would through a plain sum.
    """

    __slots__ = ("_nonfinite", "_units")

    def __init__(self, value: float = 0.0) -> None:
        self._units = 0
        self._nonfinite = 0.0
        self.add(value)

    def add(self, value: float) -> None:
        """Add one double exactly."""
        value = float(value)
        if not math.isfinite(value):
            self._nonfinite += value
            return
        numerator, denominator = value.as_integer_ratio()
        # ``denominator`` is a power of two no larger than ``2**1074``, so
        # a shift scales to units exactly, without a big-int division.
        self._units += numerator << (
            _UNIT_EXPONENT + 1 - denominator.bit_length())

    def add_array(self, terms: np.ndarray) -> None:
        """Add a float64 array exactly, vectorized.

        Each finite term is an integer mantissa below ``2**53`` times a
        power of two.  The mantissas are added per exponent with
        ``np.bincount``, in two halves that it adds without rounding, and
        the per-exponent totals are shifted into place as integers.
        """
        terms = np.asarray(terms, dtype=np.float64)
        finite = np.isfinite(terms)
        if not finite.all():
            self._nonfinite += float(terms[~finite].sum())
            terms = terms[finite]
        for start in range(0, len(terms), _EXACT_CHUNK):
            self._add_finite(terms[start:start + _EXACT_CHUNK])

    def _add_finite(self, terms: np.ndarray) -> None:
        if terms.size == 0:
            return
        # The IEEE 754 fields: a finite double with exponent field ``e``
        # and fraction ``f`` is ``(2**52 + f) * 2**(e - 1075)`` when
        # ``e > 0`` and ``f * 2**-1074`` when ``e == 0`` (a subnormal).
        # In units of ``2**-1074`` that is the mantissa shifted left by
        # ``e - 1`` or by 0, never right.
        bits = terms.view(np.int64)
        fields = (bits >> 52) & 0x7FF
        normal = fields > 0
        mantissas = (bits & ((1 << 52) - 1)) | (normal.astype(np.int64) << 52)
        np.negative(mantissas, out=mantissas, where=bits < 0)
        high = mantissas >> _HALF_BITS
        low = mantissas - (high << _HALF_BITS)
        high_sums = np.bincount(fields, weights=high).tolist()
        low_sums = np.bincount(fields, weights=low).tolist()
        for field, (high_sum, low_sum) in enumerate(
                zip(high_sums, low_sums, strict=True)):
            if not high_sum and not low_sum:
                continue
            total = (int(high_sum) << _HALF_BITS) + int(low_sum)
            self._units += total << max(field - 1, 0)

    def merge(self, other: _ExactSum) -> None:
        """Add ``other``'s exact sum to this one."""
        self._units += other._units
        self._nonfinite += other._nonfinite

    def value(self) -> float:
        """The sum, correctly rounded to a double."""
        try:
            # Integer true division rounds correctly.
            exact = self._units / (1 << _UNIT_EXPONENT)
        except OverflowError:
            # ``float(self._units)`` would overflow too: use the sign.
            exact = math.inf if self._units > 0 else -math.inf
        # ``exact`` is never ``-0.0``, so adding a zero leaves it as is.
        return self._nonfinite + exact


class Moments:
    """Running count, sum and sum of squares of a weighted value stream.

    Weighted exactly as a histogram's bars are -- by the ``count`` handed
    to ``add_value`` -- so ``count`` is the bars' total plus the
    out-of-range counts, and an out-of-range value still enters ``sum``
    and ``sum_of_squares`` at its real value.  Only the three
    accumulators are stored; :attr:`mean` and :attr:`std` are derived on
    read.

    The two sums are exact: each per-term product rounds once, as a
    double, and the additions of those products do not round at all.
    :attr:`sum` and :attr:`sum_of_squares` are the exact totals rounded
    once, so they depend only on the multiset of folded terms, never on
    the order of :meth:`add`, :meth:`add_batch` and :meth:`merge`, the
    batch boundaries or the numpy build.  Two hosts that fold the same
    values store the same sums, to the bit.

    A stream nobody has folded into is a ``Moments`` at zero; a histogram
    whose stored file predates these keys has NO moments, which its
    owner spells ``None`` rather than a zero here.
    """

    __slots__ = ("_sum", "_sum_of_squares", "count")

    def __init__(  # pylint: disable=redefined-builtin  # see ``sum`` below
        self,
        count: int = 0,
        sum: float = 0.0,  # ruff: ignore[builtin-argument-shadowing] -- the
        # ``sum=`` keyword and attribute of the former dataclass, which
        # ``from_stored`` and the tests pass and read by that name.
        sum_of_squares: float = 0.0,
    ) -> None:
        self.count = count
        self._sum = _ExactSum(sum)
        self._sum_of_squares = _ExactSum(sum_of_squares)

    @property
    def sum(self) -> float:
        """The weighted sum of the values, correctly rounded."""
        return self._sum.value()

    @sum.setter  # ruff: ignore[builtin-attribute-shadowing] -- as above
    def sum(self, value: float) -> None:
        self._sum = _ExactSum(value)

    @property
    def sum_of_squares(self) -> float:
        """The weighted sum of the squared values, correctly rounded."""
        return self._sum_of_squares.value()

    @sum_of_squares.setter
    def sum_of_squares(self, value: float) -> None:
        self._sum_of_squares = _ExactSum(value)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Moments):
            return NotImplemented
        # The rounded sums, as stored, so a loaded histogram equals the
        # one it was saved from; a nan sum (``inf`` plus ``-inf``) equals
        # a nan, so such moments still equal themselves.
        return self.count == other.count and all(
            mine == theirs or (math.isnan(mine) and math.isnan(theirs))
            for mine, theirs in (
                (self.sum, other.sum),
                (self.sum_of_squares, other.sum_of_squares),
            ))

    # Mutable, so unhashable, as the former ``@dataclass`` was.  typeshed
    # types ``__hash__`` as a method, so mypy rejects the ``None``.
    __hash__ = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return (
            f"Moments(count={self.count!r}, sum={self.sum!r}, "
            f"sum_of_squares={self.sum_of_squares!r})")

    def add(self, value: float, count: int) -> None:
        """Fold one value, ``count`` times.

        The per-value twin of :meth:`add_batch`: the products are
        ``value * count`` and ``value * value * count``, in that order, so
        each term rounds exactly as the batch arm rounds it, and the exact
        sums make the two arms agree to the bit.
        """
        # A Python int value would square exactly, where the float64
        # batch arm rounds ``v * v``: fold it as its float64 twin.
        value = float(value)
        self.count += count
        self._sum.add(value * count)
        self._sum_of_squares.add(value * value * count)

    def add_batch(self, values: np.ndarray, weights: np.ndarray) -> None:
        """Fold ``(value, weight)`` pairs, vectorized.

        ``values`` are float64 and already free of nan.  The per-term
        products are the ones :meth:`add` folds -- ``value * weight``,
        then ``value * value * weight`` -- and they are added exactly, so
        the result is the same, to the bit, as an :meth:`add` per pair in
        any order.

        One temporary, reused in place: a fresh 800 KB array per product
        of a 100k batch costs more than the reductions themselves.
        """
        products = values * weights
        self._sum.add_array(products)
        np.multiply(values, values, out=products)
        np.multiply(products, weights, out=products)
        self._sum_of_squares.add_array(products)
        self.count += int(weights.sum())

    def merge(self, other: Moments) -> None:
        """Add ``other``'s accumulators to this one's, exactly."""
        self.count += other.count
        self._sum.merge(other._sum)
        self._sum_of_squares.merge(other._sum_of_squares)

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
        # The stored sums seed the exact state, so a loaded histogram
        # merged with a new one adds to exactly the stored values.
        return Moments(
            count=data["count"],
            sum=data["sum"],
            sum_of_squares=data["sum_of_squares"],
        )
