"""Array bin folds: fragment batches in, the bins of a region's tracks out.

The array form of the ``fragment_start`` fold of
:mod:`gain.binning.fragment_folds`.  Each bin of a track reduces the
values of the fragments that start in it, and the result equals what a
:class:`~gain.binning.fragment_folds.BinFragmentStartAggregator` per
track answers for the same fragments in the same order.

A batch is a :data:`FragmentBatch`: the start, the track and the value
of each fragment, as three arrays.  A track ``-1`` is no track, and a
value ``nan`` is no value.  The fold skips both rows.

:func:`pooled_batches` merges the batches of several resources into one
stream in start order, the order that :func:`heapq.merge` gives the
records.  :func:`track_codes` maps the barcodes of a batch to tracks.
:class:`FragmentStartArrayFold` folds the merged stream.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd

from gain.genomic_resources.genomic_scores.aggregation import (
    EMPTY_BIN_VALUES,
)

#: The starts, the tracks and the values of a batch of fragments.
FragmentBatch = tuple[
    npt.NDArray[np.int64], npt.NDArray[np.int64], npt.NDArray[np.float64]]


def track_codes(
    labels: npt.NDArray[Any], track_of: Mapping[str, int],
    text_of: Callable[[Any], str | None],
) -> npt.NDArray[np.int64]:
    """The track of each label, ``-1`` for a label with no track.

    ``text_of`` gives the text of a label that ``track_of`` is keyed by.
    A missing label has the text ``""``.  Each distinct label is mapped
    once.
    """
    codes, uniques = pd.factorize(labels, use_na_sentinel=True)
    tracks = np.array(
        [track_of.get(text_of(label) or "", -1) for label in uniques]
        + [track_of.get("", -1)],
        dtype=np.int64)
    return tracks[codes]


def pooled_batches(
    reads: Sequence[Iterable[FragmentBatch]],
) -> Iterator[FragmentBatch]:
    """Merge the batches of ``reads`` into batches in start order.

    Each read yields its fragments in start order.  The merged order
    equals :func:`heapq.merge` by start: fragments with equal starts
    keep the order of their reads, and the order inside each read.

    The frontier is the smallest last start over the reads that are not
    exhausted.  Each merged batch holds the fragments that start before
    the frontier.  When all reads are exhausted, the last batch holds
    the rest.
    """
    sources = [iter(read) for read in reads]
    pending: list[FragmentBatch | None] = [None] * len(sources)
    done = [False] * len(sources)

    def pull(index: int) -> None:
        """Append the next batch of a read to its pending batch."""
        batch = next(sources[index], None)
        if batch is None:
            done[index] = True
            return
        held = pending[index]
        pending[index] = batch if held is None else _joined([held, batch])

    while True:
        for index, batch in enumerate(pending):
            if not done[index] and (batch is None or len(batch[0]) == 0):
                pull(index)
        live = [
            index for index, batch in enumerate(pending)
            if not done[index] and batch is not None]
        if not live:
            break
        frontier = min(_last_start(pending[index]) for index in live)
        taken: list[FragmentBatch] = []
        for index, batch in enumerate(pending):
            if batch is None:
                continue
            cut = int(np.searchsorted(batch[0], frontier, side="left"))
            if cut:
                taken.append(_rows(batch, slice(0, cut)))
                pending[index] = _rows(batch, slice(cut, None))
        if taken:
            yield _sorted(taken)
            continue
        # Every fragment held starts at the frontier or after it: the
        # reads at the frontier read on, so the frontier moves.
        for index in live:
            if _last_start(pending[index]) == frontier:
                pull(index)
    rest = [batch for batch in pending if batch is not None and len(batch[0])]
    if rest:
        yield _sorted(rest)


def _last_start(batch: FragmentBatch | None) -> int:
    assert batch is not None
    return int(batch[0][-1])


def _rows(batch: FragmentBatch, rows: slice) -> FragmentBatch:
    return batch[0][rows], batch[1][rows], batch[2][rows]


def _joined(batches: list[FragmentBatch]) -> FragmentBatch:
    return (
        np.concatenate([batch[0] for batch in batches]),
        np.concatenate([batch[1] for batch in batches]),
        np.concatenate([batch[2] for batch in batches]))


def _sorted(batches: list[FragmentBatch]) -> FragmentBatch:
    """Join ``batches`` in their order and sort them stably by start."""
    joined = _joined(batches)
    order = np.argsort(joined[0], kind="stable")
    return joined[0][order], joined[1][order], joined[2][order]


class FragmentStartArrayFold:
    """Each bin of each track reduces the values that START in it.

    The fold covers the grid bins from the one holding ``start`` to the
    one holding ``end``, for one track per entry of ``aggregators``.
    Feed the batches in start order.  Each update adds in input order,
    so the result equals the streaming fold bit for bit.  The one
    difference: the fold reads an ``int`` score as ``float64``, so a
    ``sum``, ``mean`` or ``product`` past ``2**53`` can round.
    """

    def __init__(
        self, *, start: int, end: int, bin_size: int,
        aggregators: Sequence[str],
    ) -> None:
        self.bin_size = bin_size
        self.first_bin = (start - 1) // bin_size
        self.bins = (end - 1) // bin_size - self.first_bin + 1
        self.aggregators = list(aggregators)
        size = self.bins * len(self.aggregators)
        self.count = np.zeros(size, dtype=np.int64)
        self.sum = np.zeros(size, dtype=np.float64)
        self.max = np.full(size, -np.inf)
        self.min = np.full(size, np.inf)
        self.product = np.ones(size, dtype=np.float64)
        self.median = np.full(size, np.nan)
        #: The ``(key, value, weight)`` rows of the bins whose median is
        #: still open: no fragment of a later batch can reach a closed bin.
        self.open_keys = np.empty(0, dtype=np.int64)
        self.open_values = np.empty(0, dtype=np.float64)
        self.open_weights = np.empty(0, dtype=np.int64)

    def feed(self, batch: FragmentBatch) -> None:
        """Fold the fragments of ``batch``."""
        with np.errstate(over="ignore", invalid="ignore"):
            self._feed(*batch)

    def _feed(
        self, starts: npt.NDArray[np.int64], tracks: npt.NDArray[np.int64],
        values: npt.NDArray[np.float64],
    ) -> None:
        if len(starts) == 0:
            return
        keep = (tracks >= 0) & ~np.isnan(values)
        keys = tracks[keep] * self.bins \
            + (starts[keep] - 1) // self.bin_size - self.first_bin
        values = values[keep]
        size = len(self.count)
        self.count += np.bincount(keys, minlength=size)
        # ``np.add.at`` adds each value to the running sum in input
        # order, as the streaming fold does.  A sum of the batch first
        # and then into the running sum rounds differently.
        np.add.at(self.sum, keys, values)
        np.maximum.at(self.max, keys, values)
        np.minimum.at(self.min, keys, values)
        np.multiply.at(self.product, keys, values)
        if "median" in self.aggregators:
            self.open_keys = np.concatenate([self.open_keys, keys])
            self.open_values = np.concatenate([self.open_values, values])
            self.open_weights = np.concatenate([
                self.open_weights, np.ones(len(keys), dtype=np.int64)])
            # A later batch starts at the last start of this one or
            # after it, so it reaches no bin before that start's bin.
            self._close_medians(
                (int(starts[-1]) - 1) // self.bin_size - self.first_bin)

    def _close_medians(self, before: int) -> None:
        """Reduce the median of each open bin before the bin ``before``."""
        closed = self.open_keys % self.bins < before
        if not closed.any():
            return
        keys = self.open_keys[closed]
        values = self.open_values[closed]
        weights = self.open_weights[closed]
        self.open_keys = self.open_keys[~closed]
        self.open_values = self.open_values[~closed]
        self.open_weights = self.open_weights[~closed]
        # ``MedianAggregator`` sorts the values of a bin stably and
        # selects by rank in the order that the weights expand.
        order = np.lexsort((values, keys))
        keys, values = keys[order], values[order]
        reach = np.cumsum(weights[order])
        bins, firsts = np.unique(keys, return_index=True)
        before_bin = np.concatenate([[0], reach])[firsts]
        totals = np.concatenate([before_bin[1:], [reach[-1]]]) - before_bin
        low = values[np.searchsorted(
            reach, before_bin + (totals - 1) // 2, side="right")]
        high = values[np.searchsorted(
            reach, before_bin + totals // 2, side="right")]
        self.median[bins] = np.where(totals % 2 == 1, low, (low + high) / 2)

    def result(self) -> npt.NDArray[np.float64]:
        """The block of the region: one row per bin, one column per track."""
        with np.errstate(over="ignore", invalid="ignore"):
            self._close_medians(self.bins)
        empty = self.count == 0
        finals = {
            "count": self.count.astype(np.float64),
            "sum": self.sum,
            "mean": np.divide(
                self.sum, self.count, out=np.zeros_like(self.sum),
                where=~empty),
            "max": self.max,
            "min": self.min,
            # A product that holds a 0 is 0, where ``inf * 0`` is nan.
            "product": np.where(np.isnan(self.product), 0.0, self.product),
            "median": self.median,
        }
        block = np.empty((self.bins, len(self.aggregators)))
        for track, aggregator in enumerate(self.aggregators):
            rows = slice(track * self.bins, (track + 1) * self.bins)
            block[:, track] = np.where(
                empty[rows], EMPTY_BIN_VALUES[aggregator],
                finals[aggregator][rows])
        return block
