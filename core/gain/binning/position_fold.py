"""The array fold of a position score into grid bins.

:class:`ArrayBinFold` folds the batches of
:meth:`~gain.genomic_resources.genomic_scores.base.GenomicScore.fetch_region_value_arrays`
into the bins of the global grid with numpy.  It gives the bins that
:meth:`~gain.genomic_resources.genomic_scores.position.PositionScore.get_score_in_bins`
gives, bit for bit, for a ``float`` score under the aggregators of
:data:`ARRAY_FOLD_AGGREGATORS`.

The fold keeps the rules of the per-run fold:

- Where records overlap, the first record answers.  A later record
  covers only the positions that no earlier record claimed, also across
  the boundary between two batches.
- A record with a missing value claims its positions.  It is the same as
  a stretch that no record covers, and the replacement stands in for
  both.  The caller marks the missing values in a mask of their own: a
  NaN value of the score is a value, as it is to the per-run fold.
- A run that crosses a bin boundary splits at the boundary, and each part
  has the weight of its length.
- ``sum`` and ``mean`` add ``value * length`` for each part, in position
  order, from the start of the bin.  The numpy reductions that use
  pairwise summation (``np.add.reduceat``, ``ndarray.sum``) give other
  sums, so the fold adds one part at a time for each bin: across the bins
  in one numpy operation, or along one bin with ``np.add.accumulate``,
  which adds in sequence.  An open bin at the end of a batch continues
  its running sum in the next batch.

``docs/adr/0038-the-position-binner-folds-value-arrays-with-numpy.md``
records why the fold exists and what it was measured against.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

import numpy as np
import numpy.typing as npt

from gain.utils.regions import calc_bin_index

#: The aggregators the array fold serves.  ``median`` and ``product``
#: keep the per-run fold.
ARRAY_FOLD_AGGREGATORS = frozenset({"count", "sum", "mean", "max", "min"})

#: The largest magnitude of an exact sum of int parts that a float64 holds.
_EXACT_INT_LIMIT = 2 ** 53

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]
BoolArray = npt.NDArray[np.bool_]

#: The value a bin's accumulator starts from, and the value that a part
#: without a value adds: the identity of the step, so the part changes
#: nothing.  ``sum`` starts from ``-0.0``, because ``-0.0 + x`` is ``x``
#: for every ``x``, which is what the first addition of the ``sum``
#: aggregator gives.  ``mean`` starts from ``0.0``, as its aggregator
#: starts from ``0``.
_START = {
    "sum": -0.0, "mean": 0.0, "max": -np.inf, "min": np.inf, "count": 0.0,
}
_IDENTITY = {
    "sum": -0.0, "mean": -0.0, "max": -np.inf, "min": np.inf, "count": 0.0,
}


def replacement_is_exact(
    replacement: float | None, bin_size: int,
) -> bool:
    """Whether the array fold adds ``replacement`` as the aggregators do.

    The aggregators keep an ``int`` replacement exact, and the fold adds
    it as a float.  The two agree while every sum of the replacement in
    one bin stays within the integers that a float64 holds exactly.
    """
    if replacement is None or isinstance(replacement, float):
        return True
    return abs(replacement) * bin_size <= _EXACT_INT_LIMIT


# A step folds one part into each of several bins.  ``first`` marks the
# bins that get their first value from the part.  The caller keeps the
# accumulator of a bin whose part is missing.
# All steps share one signature, because ``_STEPS`` dispatches on the
# aggregator.  The steps that do not read ``first`` silence the
# unused-argument check for it.
def _add_step(
    acc: FloatArray, parts: FloatArray,
    first: BoolArray,  # ruff: ignore[unused-function-argument]
) -> FloatArray:
    result: FloatArray = acc + parts
    return result


def _max_step(
    acc: FloatArray, parts: FloatArray,
    first: BoolArray,  # ruff: ignore[unused-function-argument]
) -> FloatArray:
    # ``max(value, current)``: the current maximum stays only when it is
    # strictly greater, so of two equal zeros the later one answers, and a
    # NaN maximum gives way to the next value.  A first value meets the
    # start, ``-inf``, and answers.
    result: FloatArray = np.where(acc > parts, acc, parts)
    return result


def _min_step(
    acc: FloatArray, parts: FloatArray, first: BoolArray,
) -> FloatArray:
    # ``min(current, value)``: the value answers only when it is strictly
    # smaller, so of two equal zeros the earlier one answers, and a NaN
    # minimum stays.  A first value answers, also when it is NaN.
    result: FloatArray = np.where(first | (parts < acc), parts, acc)
    return result


# An along fold folds the parts of one bin into its accumulator, from
# ``start``.  ``seen`` says whether the bin had a value before the parts.
# All along folds share the ``_AlongFold`` signature, because ``_ALONG``
# dispatches on the aggregator.  The folds that do not read ``missing``
# or ``seen`` silence the unused-argument check for them.
def _add_along(
    start: float, parts: FloatArray,
    missing: BoolArray,  # ruff: ignore[unused-function-argument]
    *, seen: bool,  # ruff: ignore[unused-function-argument]
) -> float:
    # A missing part holds ``-0.0``, which changes no sum.
    run = np.empty(len(parts) + 1, dtype=np.float64)
    run[0] = start
    run[1:] = parts
    return float(np.add.accumulate(run)[-1])


def _max_along(
    start: float, parts: FloatArray, missing: BoolArray,
    *, seen: bool,  # ruff: ignore[unused-function-argument]
) -> float:
    if np.isnan(start) or np.isnan(parts[~missing]).any():
        current = start
        for value in parts[~missing]:
            current = current if current > value else float(value)
        return current
    if missing.all():
        return start
    top = float(parts.max())
    if start > top:
        return start
    if not top:
        # The two zeros are equal and differ in sign only: the last zero
        # answers.  ``logical_not`` is true for exactly the zeros.
        return float(parts[np.flatnonzero(np.logical_not(parts))[-1]])
    return top


def _min_along(
    start: float, parts: FloatArray, missing: BoolArray, *, seen: bool,
) -> float:
    if np.isnan(start) or np.isnan(parts[~missing]).any():
        current = start
        for value in parts[~missing]:
            if not seen or value < current:
                current = float(value)
            seen = True
        return current
    bottom = float(parts.min())
    if not bottom < start:
        return start
    if not bottom:
        # The two zeros are equal and differ in sign only: the first zero
        # answers.
        return float(parts[np.flatnonzero(np.logical_not(parts))[0]])
    return bottom


_STEPS: dict[
    str, Callable[[FloatArray, FloatArray, BoolArray], FloatArray]] = {
    "sum": _add_step, "mean": _add_step, "max": _max_step, "min": _min_step,
}


class _AlongFold(Protocol):
    def __call__(
        self, start: float, parts: FloatArray, missing: BoolArray,
        *, seen: bool,
    ) -> float: ...


_ALONG: dict[str, _AlongFold] = {
    "sum": _add_along, "mean": _add_along, "max": _max_along,
    "min": _min_along,
}


class ArrayBinFold:
    """Fold value-array batches of one region into its grid bins.

    Give :meth:`add_batch` the batches of the region in their read order,
    then call :meth:`finish` once.  ``start`` and ``end`` are the 1-based
    closed bounds of the region.
    """

    def __init__(
        self, aggregator: str, replacement: float | None,
        start: int, end: int, bin_size: int,
    ) -> None:
        if aggregator not in ARRAY_FOLD_AGGREGATORS:
            raise ValueError(
                f"the array fold does not serve the aggregator "
                f"{aggregator!r}; it serves "
                f"{', '.join(sorted(ARRAY_FOLD_AGGREGATORS))}")
        self.aggregator = aggregator
        self.replacement = replacement
        self.start = start
        self.end = end
        self.bin_size = bin_size
        self._first_bin = calc_bin_index(bin_size, start)
        n_bins = calc_bin_index(bin_size, end) - self._first_bin + 1
        self._acc: FloatArray = np.full(
            n_bins, _START[aggregator], dtype=np.float64)
        self._used: IntArray = np.zeros(n_bins, dtype=np.int64)
        # The first position that no record claimed yet.
        self._cursor = start

    def add_batch(
        self, pos_begin: IntArray, pos_end: IntArray, values: FloatArray,
        missing: BoolArray,
    ) -> None:
        """Fold one batch of records, given in their read order.

        ``missing`` marks the records without a value.  A NaN in
        ``values`` that ``missing`` does not mark is a value.
        """
        left = np.maximum(pos_begin, self.start)
        right = np.minimum(pos_end, self.end)
        overlaps = left <= right
        left, right = left[overlaps], right[overlaps]
        values, missing = values[overlaps], missing[overlaps]
        if len(left) == 0:
            return
        # A record covers the positions from the first one that no
        # earlier record claimed: one past the largest earlier end.
        cursor = np.empty_like(left)
        cursor[0] = self._cursor
        cursor[1:] = np.maximum(
            np.maximum.accumulate(right)[:-1] + 1, self._cursor)
        claims = right >= cursor
        self._cursor = max(self._cursor, int(right.max()) + 1)
        left, right = left[claims], right[claims]
        values, cursor = values[claims], cursor[claims]
        missing = missing[claims]
        left = np.maximum(left, cursor)

        # Each record is a piece, after the uncovered piece before it.
        n_pieces = 2 * len(left)
        piece_begin = np.empty(n_pieces, dtype=np.int64)
        piece_end = np.empty(n_pieces, dtype=np.int64)
        piece_value = np.empty(n_pieces, dtype=np.float64)
        piece_begin[0::2], piece_begin[1::2] = cursor, left
        piece_end[0::2], piece_end[1::2] = left - 1, right
        piece_missing = np.empty(n_pieces, dtype=np.bool_)
        piece_value[0::2], piece_value[1::2] = np.nan, values
        piece_missing[0::2], piece_missing[1::2] = True, missing
        nonempty = piece_end >= piece_begin
        self._fold_pieces(
            piece_begin[nonempty], piece_end[nonempty],
            piece_value[nonempty], piece_missing[nonempty])

    def finish(self) -> FloatArray:
        """The value of each bin, NaN for a bin without a value."""
        if self._cursor <= self.end:
            self._fold_pieces(
                np.array([self._cursor], dtype=np.int64),
                np.array([self.end], dtype=np.int64),
                np.array([np.nan]), np.array([True]))
            self._cursor = self.end + 1
        has_value = self._used > 0
        if self.aggregator == "count":
            result = self._used.astype(np.float64)
        elif self.aggregator == "mean":
            result = np.divide(
                self._acc, self._used, out=np.zeros_like(self._acc),
                where=has_value)
        else:
            result = self._acc.copy()
        result[~has_value] = np.nan
        return result

    def _fold_pieces(
        self, begin: IntArray, end: IntArray, values: FloatArray,
        piece_missing: BoolArray,
    ) -> None:
        """Split pieces at the bin boundaries and fold the parts in order."""
        if len(begin) == 0:
            return
        bin_size = self.bin_size
        first = (begin - 1) // bin_size
        n_parts = (end - 1) // bin_size - first + 1
        piece = np.repeat(np.arange(len(begin)), n_parts)
        offset = np.arange(len(piece)) - np.repeat(
            np.cumsum(n_parts) - n_parts, n_parts)
        part_bin = first[piece] + offset
        part_begin = np.maximum(begin[piece], part_bin * bin_size + 1)
        part_end = np.minimum(end[piece], (part_bin + 1) * bin_size)
        length = part_end - part_begin + 1
        value = values[piece]
        missing = piece_missing[piece]
        if self.replacement is not None:
            value[missing] = self.replacement
            missing[:] = False

        local_bin = part_bin - self._first_bin
        group_start = np.flatnonzero(
            np.concatenate(([True], local_bin[1:] != local_bin[:-1])))
        group_bin = local_bin[group_start]
        seen = self._used > 0
        self._used[group_bin] += np.add.reduceat(
            np.where(missing, 0, length), group_start)
        if self.aggregator == "count":
            return

        # The per-run fold adds Python floats, which overflow to ``inf``
        # and give NaN for ``inf - inf`` without a warning.  The array
        # fold gives the same values, and silences numpy's warnings.
        with np.errstate(over="ignore", invalid="ignore"):
            if self.aggregator in {"sum", "mean"}:
                operand = value * length
            else:
                operand = value
            operand[missing] = _IDENTITY[self.aggregator]
            self._fold_in_order(
                operand, missing, seen, group_start, group_bin)

    def _fold_in_order(
        self, operand: FloatArray, missing: BoolArray, seen: BoolArray,
        group_start: IntArray, group_bin: IntArray,
    ) -> None:
        """Fold each bin's parts into its accumulator, one at a time.

        The parts of a bin are contiguous in ``operand``, from
        ``group_start``.  The fold takes the cheaper of two orders of
        work: one numpy step for each part rank, across all bins, or one
        numpy call along each bin.
        """
        counts = np.diff(np.append(group_start, len(operand)))
        if int(counts.max()) <= len(group_start):
            step = _STEPS[self.aggregator]
            rank = np.arange(len(operand)) - np.repeat(group_start, counts)
            order = np.argsort(rank, kind="stable")
            local_bin = np.repeat(group_bin, counts)
            bounds = np.cumsum(np.bincount(rank))
            low = 0
            for high in bounds:
                chosen = order[low:high]
                bins = local_bin[chosen]
                absent = missing[chosen]
                acc = self._acc[bins]
                self._acc[bins] = np.where(
                    absent, acc,
                    step(acc, operand[chosen], ~seen[bins] & ~absent))
                seen[bins] |= ~absent
                low = high
            return
        along = _ALONG[self.aggregator]
        for start, count, bin_index in zip(
                group_start, counts, group_bin, strict=True):
            self._acc[bin_index] = along(
                float(self._acc[bin_index]),
                operand[start:start + count],
                missing[start:start + count],
                seen=bool(seen[bin_index]))
