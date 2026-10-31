"""Array bin folds: the ``fragment_start`` fold over column arrays.

:func:`fold_fragment_starts` bins the fragments of one region from the
batches of :meth:`FragmentScore.get_fragment_arrays_starting_in_region
<gain.genomic_resources.genomic_scores.FragmentScore.get_fragment_arrays_starting_in_region>`,
one read per resource, into a ``(bins, tracks)`` block.  The block equals,
bit for bit, the one that :class:`~.fragment_folds.BinFragmentStartAggregator`
gives for the ``heapq.merge`` by start of the record reads, with two
exceptions:

* The record read keeps an ``int`` score and an ``int`` constant value
  exact, so ``sum``, ``mean``, ``product`` and the ``median`` of an even
  count past 2**53 can differ.
* The array fold takes every NaN value as no value.  The record read
  takes only a value in the ``na_values`` of the score as no value, so a
  NaN that ``na_values`` does not cover counts as a value there.

The reads are pooled by a frontier: the smallest last start over the
pending batches of the reads that are not exhausted.  Each window takes
the fragments that start before the frontier from each read, joins them
in read order and sorts them by start with a stable sort, which is the
order of ``heapq.merge``.  The fold then adds a window to dense state
arrays with ``ufunc.at``, which applies the values in input order.
"""
from __future__ import annotations

import sys
from collections.abc import Callable, Generator, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd

from gain.genomic_resources.aggregators import _times_power
from gain.genomic_resources.genomic_scores.aggregation import (
    EMPTY_BIN_VALUES,
)
from gain.genomic_resources.genomic_scores.records import RecordArrays
from gain.utils.regions import calc_bin_index

#: The aggregators that the array fold answers.
ARRAY_AGGREGATORS = (
    "count", "sum", "mean", "max", "min", "median", "product")

_FloatArray = npt.NDArray[np.float64]
_IntArray = npt.NDArray[np.int64]


@dataclass
class StartRead:
    """The batches of one resource and how a fragment finds its track.

    ``track_of`` maps a value of the ``group_score`` column to a track
    index, or to ``None`` for a fragment that reaches no track.  Without
    ``track_of`` every fragment goes to track 0.  ``value_score`` names
    the column of the values, and ``None`` gives each fragment the
    ``constant`` value.  :attr:`dropped` counts the fragments that reach
    no track.
    """

    batches: Generator[RecordArrays, None, None]
    value_score: str | None = None
    constant: float = 1.0
    group_score: str | None = None
    track_of: Callable[[Any], int | None] | None = None
    dropped: int = field(default=0, init=False)
    exhausted: bool = field(default=False, init=False)
    #: The pending fragments: their starts, tracks and values.
    begins: _IntArray = field(
        default_factory=lambda: np.empty(0, dtype=np.int64), init=False)
    tracks: _IntArray = field(
        default_factory=lambda: np.empty(0, dtype=np.int64), init=False)
    values: _FloatArray = field(
        default_factory=lambda: np.empty(0, dtype=np.float64), init=False)
    _memo: dict[Any, int] = field(default_factory=dict, init=False)

    def refill(self) -> None:
        """Pull batches while the pending fragments all share one start."""
        while not self.exhausted and (
                len(self.begins) == 0 or self.begins[0] == self.begins[-1]):
            try:
                begins, _ends, columns = next(self.batches)
            except StopIteration:
                self.exhausted = True
                return
            self._append(begins, columns)

    def take_before(
        self, frontier: int | None,
    ) -> tuple[_IntArray, _IntArray, _FloatArray]:
        """Take the pending fragments that start before ``frontier``.

        ``None`` takes all of them.
        """
        cut = len(self.begins) if frontier is None else int(
            np.searchsorted(self.begins, frontier, side="left"))
        taken = (self.begins[:cut], self.tracks[:cut], self.values[:cut])
        self.begins = self.begins[cut:]
        self.tracks = self.tracks[cut:]
        self.values = self.values[cut:]
        return taken

    def _append(
        self, begins: np.ndarray, columns: dict[str, np.ndarray],
    ) -> None:
        tracks = self._tracks(columns, len(begins))
        if self.value_score is None:
            values = np.full(len(begins), self.constant, dtype=np.float64)
        else:
            values = np.asarray(columns[self.value_score], dtype=np.float64)
        reached = tracks >= 0
        self.dropped += int(len(tracks) - np.count_nonzero(reached))
        # A NaN value is no value: the fragment adds nothing to its bin.
        # The record read differs for a NaN that na_values does not cover.
        kept = reached & ~np.isnan(values)
        self.begins = np.concatenate(
            [self.begins, np.asarray(begins, dtype=np.int64)[kept]])
        self.tracks = np.concatenate([self.tracks, tracks[kept]])
        self.values = np.concatenate([self.values, values[kept]])

    def _tracks(
        self, columns: dict[str, np.ndarray], size: int,
    ) -> _IntArray:
        """The track of each fragment of a batch, ``-1`` for none."""
        if self.track_of is None:
            return np.zeros(size, dtype=np.int64)
        assert self.group_score is not None
        codes, uniques = pd.factorize(columns[self.group_score])
        lookup = np.fromiter(
            (self._track(unique) for unique in uniques),
            dtype=np.int64, count=len(uniques))
        # A missing value has the code -1, which picks the appended -1.
        return np.append(lookup, -1)[codes]

    def _track(self, unique: Any) -> int:
        track = self._memo.get(unique)
        if track is None:
            assert self.track_of is not None
            found = self.track_of(unique)
            track = self._memo[unique] = -1 if found is None else found
        return track


def fold_fragment_starts(
    reads: Sequence[StartRead], *,
    start: int, end: int, bin_size: int, tracks: int, aggregator: str,
) -> _FloatArray:
    """Fold the fragments of ``reads`` into a ``(bins, tracks)`` block.

    The rows are the grid bins from the one that holds ``start`` to the
    one that holds ``end``.  A bin that no fragment with a value reaches
    holds the value of :data:`EMPTY_BIN_VALUES` for ``aggregator``.  The
    reads give the fragments that start in ``[start, end]``, each read in
    start order.
    """
    first_bin = calc_bin_index(bin_size, start)
    fold = _StartFold(
        first_bin=first_bin,
        bins=calc_bin_index(bin_size, end) - first_bin + 1,
        bin_size=bin_size, tracks=tracks, aggregator=aggregator)
    while True:
        for read in reads:
            read.refill()
        active = [read.begins[-1] for read in reads if not read.exhausted]
        frontier = int(min(active)) if active else None
        taken = [read.take_before(frontier) for read in reads] or [(
            np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64),
            np.empty(0, dtype=np.float64))]
        begins = np.concatenate([part[0] for part in taken])
        order = np.argsort(begins, kind="stable")
        fold.add(
            begins[order],
            np.concatenate([part[1] for part in taken])[order],
            np.concatenate([part[2] for part in taken])[order],
            frontier)
        if frontier is None:
            return fold.result()


class _StartFold:
    """The dense state of one aggregator over the bins of all tracks.

    The state is flat: the cell of bin row ``r`` and track ``t`` is
    ``r * tracks + t``, so a block is a reshape of it.
    """

    def __init__(
        self, *, first_bin: int, bins: int, bin_size: int, tracks: int,
        aggregator: str,
    ) -> None:
        if aggregator not in ARRAY_AGGREGATORS:
            raise ValueError(
                f"the array fold has no {aggregator!r} aggregator")
        self.first_bin = first_bin
        self.bins = bins
        self.bin_size = bin_size
        self.tracks = tracks
        self.aggregator = aggregator
        size = bins * tracks
        self.counts = np.zeros(size, dtype=np.int64)
        self.state = np.full(size, _INITIAL[aggregator], dtype=np.float64)
        #: ``product``: the cells whose running product underflowed to 0.
        self.frozen = np.zeros(size, dtype=bool)
        #: ``product``, ``max``, ``min``: the zero value of each cell that
        #: the aggregator keeps, NaN for none.
        self.zeros = np.full(size, np.nan, dtype=np.float64)
        #: ``median``: the cells and the values of the open bins.
        self.open_cells: _IntArray = np.empty(0, dtype=np.int64)
        self.open_values: _FloatArray = np.empty(0, dtype=np.float64)

    def add(
        self, begins: _IntArray, tracks: _IntArray, values: _FloatArray,
        frontier: int | None,
    ) -> None:
        """Add one window; no later fragment starts before ``frontier``."""
        cells = ((begins - 1) // self.bin_size - self.first_bin) \
            * self.tracks + tracks
        np.add.at(self.counts, cells, 1)
        with np.errstate(over="ignore", invalid="ignore"):
            if self.aggregator in ("sum", "mean"):
                np.add.at(self.state, cells, values)
            elif self.aggregator == "max":
                np.maximum.at(self.state, cells, values)
                self._keep_zero(cells, values, last=True)
            elif self.aggregator == "min":
                np.minimum.at(self.state, cells, values)
                self._keep_zero(cells, values, last=False)
            elif self.aggregator == "product":
                self._multiply(cells, values)
            elif self.aggregator == "median":
                self._median(cells, values, frontier)

    def result(self) -> _FloatArray:
        """The block: one row per bin, one column per track."""
        filled = self.counts > 0
        if self.aggregator == "count":
            values = self.counts.astype(np.float64)
        elif self.aggregator == "mean":
            values = np.divide(
                self.state, self.counts, out=np.zeros_like(self.state),
                where=filled)
        elif self.aggregator == "product":
            values = np.where(np.isnan(self.zeros), self.state, self.zeros)
        elif self.aggregator in ("max", "min"):
            values = np.where(
                (self.state == 0) & ~np.isnan(self.zeros),
                self.zeros, self.state)
        else:
            values = self.state
        block = np.where(filled, values, EMPTY_BIN_VALUES[self.aggregator])
        return block.reshape(self.bins, self.tracks)

    def _multiply(self, cells: _IntArray, values: _FloatArray) -> None:
        """Multiply in input order, as ``ProductAggregator`` does.

        A zero value makes the cell's result 0, the last such zero.  A
        running product that underflows to 0 stays 0: ``np.multiply.at``
        would turn it into NaN by a later infinity, or flip its sign.
        ``ProductAggregator`` multiplies a subnormal value with two
        roundings, and ``np.multiply.at`` with one.  So the cells that
        reach 0 or NaN, and the cells that get a subnormal value, are
        multiplied again one value at a time from their state before the
        window, with the multiplication of ``ProductAggregator``.
        """
        self._keep_zero(cells, values, last=True)
        zero = values == 0
        live = ~zero & ~self.frozen[cells]
        cells, values = cells[live], values[live]
        if len(cells) == 0:
            return
        touched = np.unique(cells)
        before = self.state[touched]
        np.multiply.at(self.state, cells, values)
        after = self.state[touched]
        subnormal = np.isin(
            touched, cells[np.abs(values) < sys.float_info.min])
        serial = (after == 0) | np.isnan(after) | subnormal
        if not serial.any():
            return
        order = np.argsort(cells, kind="stable")
        sorted_cells = cells[order]
        sorted_values = values[order]
        replay = touched[serial]
        lows = np.searchsorted(sorted_cells, replay, side="left")
        highs = np.searchsorted(sorted_cells, replay, side="right")
        for cell, start, low, high in zip(
                replay.tolist(), before[serial].tolist(),
                lows.tolist(), highs.tolist(), strict=True):
            product = start
            for value in sorted_values[low:high].tolist():
                product = _times_power(product, value, 1)
                if product == 0:
                    break
            self.state[cell] = product
            self.frozen[cell] = product == 0

    def _keep_zero(
        self, cells: _IntArray, values: _FloatArray, *, last: bool,
    ) -> None:
        """Keep the last, or else the first, zero value of each cell.

        ``0.0`` and ``-0.0`` are equal, so the aggregators keep the zero
        that they see last (``product``, ``max``) or first (``min``).
        ``ufunc.at`` does not promise which of two equal values it keeps.
        """
        zero = values == 0
        if not zero.any():
            return
        zero_cells, zero_values = cells[zero], values[zero]
        if last:
            zero_cells, zero_values = zero_cells[::-1], zero_values[::-1]
        found, index = np.unique(zero_cells, return_index=True)
        if not last:
            unset = np.isnan(self.zeros[found])
            found, index = found[unset], index[unset]
        self.zeros[found] = zero_values[index]

    def _median(
        self, cells: _IntArray, values: _FloatArray, frontier: int | None,
    ) -> None:
        """Keep the window open, then reduce the bins before ``frontier``.

        A bin's median is selected by rank, as ``MedianAggregator`` does:
        the middle value for an odd count, the mean of the two middle
        values for an even one.
        """
        self.open_cells = np.concatenate([self.open_cells, cells])
        self.open_values = np.concatenate([self.open_values, values])
        if frontier is None:
            closed = np.ones(len(self.open_cells), dtype=bool)
        else:
            limit = (calc_bin_index(self.bin_size, frontier)
                     - self.first_bin) * self.tracks
            closed = self.open_cells < limit
        if not closed.any():
            return
        done_cells = self.open_cells[closed]
        done_values = self.open_values[closed]
        self.open_cells = self.open_cells[~closed]
        self.open_values = self.open_values[~closed]
        order = np.lexsort((done_values, done_cells))
        done_cells, done_values = done_cells[order], done_values[order]
        cell, first, count = np.unique(
            done_cells, return_index=True, return_counts=True)
        middle = first + count // 2
        odd = count % 2 == 1
        self.state[cell] = np.where(
            odd, done_values[middle],
            (done_values[middle - 1 + odd] + done_values[middle]) / 2)


#: The state of a cell before its first value.  A ``sum`` starts at -0.0,
#: the one float that adds to any value without a change; ``mean`` starts
#: at 0, as ``MeanAggregator`` does.
_INITIAL: dict[str, float] = {
    "count": 0.0, "sum": -0.0, "mean": 0.0, "max": -np.inf,
    "min": np.inf, "median": np.nan, "product": 1.0,
}
