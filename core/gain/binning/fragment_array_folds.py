"""Array bin folds: the ``fragment_start``, ``fragment_length`` and
``coverage_profile`` folds.

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

:func:`fold_fragment_lengths` bins the fragments that overlap a region,
from the batches of :meth:`FragmentScore.get_fragment_arrays_overlapping_region
<gain.genomic_resources.genomic_scores.FragmentScore.get_fragment_arrays_overlapping_region>`.
It clips each fragment to the region and splits it into one piece for
each bin that it reaches, weighted by the overlap in base pairs.  The
block equals the one that :class:`~.fragment_folds.BinFragmentLengthAggregator`
gives, with the same two exceptions.  The pieces of a window, the pieces
of the ``uncovered_value`` fill included, are in feed order, so each bin
gets its values in the order of the streaming fold.

:func:`fold_coverage_profiles` bins the coverage profile of the same
fragments, for ``int`` values.  It turns each fragment into a ``+v``
event at its start and a ``-v`` event after its end, and sums the events
of each track with an ``int64`` ``cumsum``.  The levels make runs as
:class:`~.fragment_folds.CoverageProfile` makes them, and the length fold
folds the runs.  The block equals the one that
:class:`~.fragment_folds.BinFragmentCoverageAggregator` gives, with the
exceptions of the length fold.
"""
from __future__ import annotations

import sys
from collections.abc import Callable, Generator, Sequence
from dataclasses import dataclass, field
from typing import Any, NamedTuple

import numpy as np
import numpy.typing as npt
import pandas as pd

from gain.genomic_resources.aggregators import _times_power
from gain.genomic_resources.genomic_scores.aggregation import (
    EMPTY_BIN_VALUES,
)
from gain.genomic_resources.genomic_scores.records import RecordArrays
from gain.utils.regions import calc_bin_begin, calc_bin_index

#: The aggregators that the array fold answers.
ARRAY_AGGREGATORS = (
    "count", "sum", "mean", "max", "min", "median", "product")
#: The aggregators a length-weighted mode accepts: ``count`` would count
#: base pairs, and ``product`` raise a value to the power of a length.
#: The two length-weighted array folds, ``fragment_length`` and
#: ``coverage_profile``, answer each of them.
LENGTH_AGGREGATORS = ("max", "mean", "median", "min", "sum")

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
    #: The pending fragments: their starts, ends, tracks and values.
    begins: _IntArray = field(
        default_factory=lambda: np.empty(0, dtype=np.int64), init=False)
    ends: _IntArray = field(
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
                begins, ends, columns = next(self.batches)
            except StopIteration:
                self.exhausted = True
                return
            self._append(begins, ends, columns)

    def take_before(self, frontier: int | None) -> _Window:
        """Take the pending fragments that start before ``frontier``.

        ``None`` takes all of them.
        """
        cut = len(self.begins) if frontier is None else int(
            np.searchsorted(self.begins, frontier, side="left"))
        taken = _Window(
            self.begins[:cut], self.ends[:cut], self.tracks[:cut],
            self.values[:cut])
        self.begins = self.begins[cut:]
        self.ends = self.ends[cut:]
        self.tracks = self.tracks[cut:]
        self.values = self.values[cut:]
        return taken

    def _append(
        self, begins: np.ndarray, ends: np.ndarray,
        columns: dict[str, np.ndarray],
    ) -> None:
        tracks = self._tracks(columns, len(begins))
        if self.value_score is None:
            values = np.full(len(begins), self.constant, dtype=np.float64)
        else:
            values = np.asarray(columns[self.value_score], dtype=np.float64)
        reached = tracks >= 0
        self.dropped += int(len(tracks) - np.count_nonzero(reached))
        kept = reached & self._kept(values)
        begins, ends = self._extent(
            np.asarray(begins, dtype=np.int64),
            np.asarray(ends, dtype=np.int64))
        self.begins = np.concatenate([self.begins, begins[kept]])
        self.ends = np.concatenate([self.ends, ends[kept]])
        self.tracks = np.concatenate([self.tracks, tracks[kept]])
        self.values = np.concatenate([self.values, values[kept]])

    def _kept(self, values: _FloatArray) -> npt.NDArray[np.bool_]:
        """The fragments of a batch with a track that the fold keeps.

        A NaN value is no value: the fragment adds nothing to its bin.
        The record read differs for a NaN that na_values does not cover.
        """
        return ~np.isnan(values)

    def _extent(
        self, begins: _IntArray, ends: _IntArray,
    ) -> tuple[_IntArray, _IntArray]:
        """The extents that the fold reads: the fragments as they are."""
        return begins, ends

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


@dataclass
class LengthRead(StartRead):
    """The batches of one resource that overlap ``[start, end]``.

    Each fragment is clipped to ``[start, end]``.  A fragment with a NaN
    value stays: it adds no value, but it covers its bases, so no
    ``uncovered_value`` goes under it.
    """

    start: int = 1
    end: int = 1

    def _kept(self, values: _FloatArray) -> npt.NDArray[np.bool_]:
        return np.ones(len(values), dtype=bool)

    def _extent(
        self, begins: _IntArray, ends: _IntArray,
    ) -> tuple[_IntArray, _IntArray]:
        # A base pair belongs to one region: a fragment adds only the
        # part of it inside the region.
        return np.maximum(begins, self.start), np.minimum(ends, self.end)


@dataclass
class CoverageRead(LengthRead):
    """The batches of one resource that overlap ``[start, end]``, as
    :class:`LengthRead`.

    A fragment with a NaN value is no coverage: the fold drops it, so it
    covers no base.
    """

    def _kept(self, values: _FloatArray) -> npt.NDArray[np.bool_]:
        return ~np.isnan(values)


#: The running sum of the absolute values of a region must stay below
#: this bound.  Below it, each value and each level is an exact integer
#: in ``float64`` and in ``int64``.
EXACT_LEVEL_BOUND = 2**53


class InexactLevelsError(ValueError):
    """The values of a region can make a level that is not exact.

    :func:`fold_coverage_profiles` raises it when the running sum of the
    absolute values of the region reaches :data:`EXACT_LEVEL_BOUND`.
    """


class _Window(NamedTuple):
    """The fragments of one window, in feed order."""

    begins: _IntArray
    ends: _IntArray
    tracks: _IntArray
    scores: _FloatArray


def _windows(
    reads: Sequence[StartRead],
) -> Generator[tuple[_Window, int | None], None, None]:
    """Yield each window of ``reads`` and its frontier, the last ``None``.

    No fragment of a later window starts before the frontier of a window.
    """
    while True:
        for read in reads:
            read.refill()
        active = [read.begins[-1] for read in reads if not read.exhausted]
        frontier = int(min(active)) if active else None
        taken = [read.take_before(frontier) for read in reads] or [_Window(
            np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64),
            np.empty(0, dtype=np.int64), np.empty(0, dtype=np.float64))]
        begins = np.concatenate([part.begins for part in taken])
        order = np.argsort(begins, kind="stable")
        yield _Window(
            begins[order],
            np.concatenate([part.ends for part in taken])[order],
            np.concatenate([part.tracks for part in taken])[order],
            np.concatenate([part.scores for part in taken])[order],
        ), frontier
        if frontier is None:
            return


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
    for window, frontier in _windows(reads):
        fold.add(window.begins, window.tracks, window.scores, frontier)
    return fold.result()


def fold_fragment_lengths(
    reads: Sequence[LengthRead], *,
    start: int, end: int, bin_size: int, tracks: int, aggregator: str,
    uncovered_value: float | None = None,
) -> _FloatArray:
    """Fold the fragments of ``reads`` into a ``(bins, tracks)`` block.

    The rows are the grid bins from the one that holds ``start`` to the
    one that holds ``end``.  A fragment adds its value to each bin that
    it reaches, weighted by the base pairs that it shares with the bin.
    With an ``uncovered_value``, each base of ``[start, end]`` that no
    fragment of a track covers adds that value to the track with a
    weight of 1 bp.  A bin that gets no value holds the value of
    :data:`EMPTY_BIN_VALUES` for ``aggregator``.  The reads give the
    fragments that overlap ``[start, end]``, each read in start order.
    """
    if aggregator not in LENGTH_AGGREGATORS:
        raise ValueError(
            f"the length array fold has no {aggregator!r} aggregator")
    fold = _LengthFold(
        start=start, end=end, bin_size=bin_size, tracks=tracks,
        aggregator=aggregator, uncovered_value=uncovered_value)
    for window, frontier in _windows(reads):
        fold.add(window, frontier)
    return fold.cells.result()


def fold_coverage_profiles(
    reads: Sequence[CoverageRead], *,
    start: int, end: int, bin_size: int, tracks: int, aggregator: str,
    uncovered_value: float | None = None,
) -> _FloatArray:
    """Fold the coverage profile of ``reads`` into a ``(bins, tracks)`` block.

    The values of the fragments must be integers.  The level of a base
    in a track is the sum of the values of the fragments of the track
    that cover it.  The runs of equal levels other than 0 are folded as
    :func:`fold_fragment_lengths` folds fragments.  A base at level 0 is
    uncovered, and gets the ``uncovered_value``.

    Raises :class:`InexactLevelsError` when the running sum of the
    absolute values reaches :data:`EXACT_LEVEL_BOUND`, and
    :class:`ValueError` when a value is not an integer.
    """
    if aggregator not in LENGTH_AGGREGATORS:
        raise ValueError(
            f"the length array fold has no {aggregator!r} aggregator")
    profile = _CoverageRuns(start=start, end=end, tracks=tracks)
    fold = _LengthFold(
        start=start, end=end, bin_size=bin_size, tracks=tracks,
        aggregator=aggregator, uncovered_value=uncovered_value)
    for window, frontier in _windows(reads):
        fold.add(*profile.add(window, frontier))
    return fold.cells.result()


class _CoverageRuns:
    """Turns the fragments of each window into the runs of the profile.

    ``pending`` holds the events at or after the frontier: their
    positions, tracks and changes of level.  ``level`` is the level of
    each track before the frontier.  ``held`` is the last run of each
    track, which can go on into the next window; a level of 0 means no
    run.
    """

    def __init__(self, *, start: int, end: int, tracks: int) -> None:
        self.end = end
        self.origin = start
        self.magnitude = 0.0
        self.pending = (
            np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64),
            np.empty(0, dtype=np.int64))
        self.level = np.zeros(tracks, dtype=np.int64)
        self.held_starts = np.zeros(tracks, dtype=np.int64)
        self.held_ends = np.zeros(tracks, dtype=np.int64)
        self.held_levels = np.zeros(tracks, dtype=np.int64)

    def add(
        self, window: _Window, frontier: int | None,
    ) -> tuple[_Window, int | None]:
        """The runs that end before ``frontier``, and their frontier.

        No later run starts before the returned frontier.
        """
        self.magnitude += float(np.abs(window.scores).sum())
        if self.magnitude >= EXACT_LEVEL_BOUND:
            raise InexactLevelsError(
                f"the values of the region reach {EXACT_LEVEL_BOUND} "
                f"in absolute sum")
        positions, tracks, changes = self._settle(window, frontier)
        starts, ends, levels, tracks = self._segments(
            positions, tracks, changes, frontier)
        starts, ends, levels, tracks = self._runs(
            starts, ends, levels, tracks)
        self.held_levels[:] = 0
        if frontier is not None:
            held = ends == frontier - 1
            self.held_starts[tracks[held]] = starts[held]
            self.held_ends[tracks[held]] = ends[held]
            self.held_levels[tracks[held]] = levels[held]
            starts, ends, levels, tracks = (
                starts[~held], ends[~held], levels[~held], tracks[~held])
            self.origin = frontier
        order = np.argsort(starts, kind="stable")
        runs = _Window(
            starts[order], ends[order], tracks[order],
            levels[order].astype(np.float64))
        if frontier is None:
            return runs, None
        open_starts = self.held_starts[self.held_levels != 0]
        return runs, int(min([frontier, *open_starts.tolist()]))

    def _settle(
        self, window: _Window, frontier: int | None,
    ) -> tuple[_IntArray, _IntArray, _IntArray]:
        """The events before ``frontier``, sorted by track and position.

        A track with a level other than 0 gets an event with no change at
        the origin, so its level before the window starts a segment.
        """
        values = window.scores.astype(np.int64)
        if not np.array_equal(values, window.scores):
            raise ValueError(
                "the coverage array fold needs integer values")
        carried = np.flatnonzero(self.level)
        positions = np.concatenate([
            self.pending[0], window.begins, window.ends + 1,
            np.full(len(carried), self.origin)])
        tracks = np.concatenate([
            self.pending[1], window.tracks, window.tracks, carried])
        changes = np.concatenate([
            self.pending[2], values, -values,
            np.zeros(len(carried), dtype=np.int64)])
        settled = np.ones(len(positions), dtype=bool) if frontier is None \
            else positions < frontier
        self.pending = (
            positions[~settled], tracks[~settled], changes[~settled])
        order = np.lexsort((positions[settled], tracks[settled]))
        return (
            positions[settled][order], tracks[settled][order],
            changes[settled][order])

    def _segments(
        self, positions: _IntArray, tracks: _IntArray, changes: _IntArray,
        frontier: int | None,
    ) -> tuple[_IntArray, _IntArray, _IntArray, _IntArray]:
        """The segments of one level from each event position on.

        The last segment of a track ends before ``frontier``, or at
        ``end`` for the last window.  The held runs join the segments.
        """
        if len(positions) == 0:
            first = np.empty(0, dtype=np.int64)
        else:
            first = np.flatnonzero(np.concatenate([
                [True],
                (tracks[1:] != tracks[:-1])
                | (positions[1:] != positions[:-1])]))
        starts, owners = positions[first], tracks[first]
        net = np.add.reduceat(changes, first) if len(first) \
            else np.empty(0, dtype=np.int64)
        total = np.cumsum(net)
        new_track = np.concatenate([[True], owners[1:] != owners[:-1]]) \
            if len(owners) else np.empty(0, dtype=bool)
        heads = np.flatnonzero(new_track)
        sizes = np.diff(np.append(heads, len(owners)))
        levels = total - np.repeat((total - net)[heads], sizes) \
            + self.level[owners]
        last = np.append(heads[1:] - 1, len(owners) - 1) if len(heads) \
            else np.empty(0, dtype=np.int64)
        self.level[owners[last]] = levels[last]
        ends = np.append(starts[1:] - 1, 0)
        ends[last] = self.end if frontier is None else frontier - 1
        held = np.flatnonzero(self.held_levels)
        return (
            np.concatenate([starts, self.held_starts[held]]),
            np.concatenate([ends, self.held_ends[held]]),
            np.concatenate([levels, self.held_levels[held]]),
            np.concatenate([owners, held]))

    @staticmethod
    def _runs(
        starts: _IntArray, ends: _IntArray, levels: _IntArray,
        tracks: _IntArray,
    ) -> tuple[_IntArray, _IntArray, _IntArray, _IntArray]:
        """Join the touching segments of one level; drop the level 0.

        As ``CoverageProfile``, two touching runs never share a level.
        """
        covered = levels != 0
        starts, ends, levels, tracks = (
            starts[covered], ends[covered], levels[covered], tracks[covered])
        order = np.lexsort((starts, tracks))
        starts, ends, levels, tracks = (
            starts[order], ends[order], levels[order], tracks[order])
        if len(starts) == 0:
            return starts, ends, levels, tracks
        heads = np.flatnonzero(np.concatenate([
            [True],
            (tracks[1:] != tracks[:-1]) | (starts[1:] != ends[:-1] + 1)
            | (levels[1:] != levels[:-1])]))
        tails = np.append(heads[1:] - 1, len(starts) - 1)
        return starts[heads], ends[tails], levels[heads], tracks[heads]


class _LengthFold:
    """Splits the fragments of each window into weighted bin pieces.

    ``covered`` holds the last covered base of each track, ``start - 1``
    before any fragment.  ``fill`` is the ``uncovered_value``, NaN for
    none.
    """

    def __init__(
        self, *, start: int, end: int, bin_size: int, tracks: int,
        aggregator: str, uncovered_value: float | None,
    ) -> None:
        self.start = start
        self.end = end
        self.bin_size = bin_size
        self.tracks = tracks
        self.fill = np.nan if uncovered_value is None else uncovered_value
        self.first_bin = calc_bin_index(bin_size, start)
        self.cells = _StartFold(
            first_bin=self.first_bin,
            bins=calc_bin_index(bin_size, end) - self.first_bin + 1,
            bin_size=bin_size, tracks=tracks, aggregator=aggregator)
        self.covered = np.full(tracks, start - 1, dtype=np.int64)

    def add(self, window: _Window, frontier: int | None) -> None:
        """Add one window; no later fragment starts before ``frontier``.

        Each fragment adds the fill of the gap before it, then its own
        pieces.  The window then fills each track up to the bin before
        the one of ``frontier``, or to ``end`` for the last window.  The
        fill stops at a bin boundary: a fill split inside a bin adds
        ``v*w1 + v*w2`` and not ``v*(w1+w2)``.
        """
        size = len(window.begins)
        gaps = self._covered_before(window) + 1
        starts = np.empty(2 * size, dtype=np.int64)
        ends = np.empty(2 * size, dtype=np.int64)
        values = np.empty(2 * size, dtype=np.float64)
        starts[0::2], starts[1::2] = gaps, window.begins
        ends[0::2], ends[1::2] = window.begins - 1, window.ends
        values[0::2] = self.fill
        values[1::2] = window.scores
        tracks = np.repeat(window.tracks, 2)
        np.maximum.at(self.covered, window.tracks, window.ends)

        upto = self.end if frontier is None else calc_bin_begin(
            self.bin_size, calc_bin_index(self.bin_size, frontier)) - 1
        filled = np.flatnonzero(self.covered < upto)
        starts = np.concatenate([starts, self.covered[filled] + 1])
        ends = np.concatenate([ends, np.full(len(filled), upto)])
        values = np.concatenate([values, np.full(len(filled), self.fill)])
        tracks = np.concatenate([tracks, filled])
        self.covered[filled] = upto

        # A fragment with no value covers its bases and adds nothing.
        kept = (starts <= ends) & ~np.isnan(values)
        cells, weights, piece_values = self._pieces(
            starts[kept], ends[kept], tracks[kept], values[kept])
        self.cells.add_cells(cells, piece_values, weights, frontier)

    def _covered_before(self, window: _Window) -> _IntArray:
        """The last base that a track covers before each fragment.

        A running maximum of the ends over the fragments of each track.
        The ends of a track are shifted past those of the tracks before
        it, so one running maximum serves all tracks.
        """
        if len(window.tracks) == 0:
            return window.ends
        order = np.argsort(window.tracks, kind="stable")
        tracks = window.tracks[order]
        span = self.end - self.start + 2
        base = tracks * span - (self.start - 1)
        shifted = base + window.ends[order]
        previous = np.concatenate([[-1], shifted[:-1]])
        before: _IntArray = np.maximum(
            np.maximum.accumulate(previous),
            base + self.covered[tracks]) - base
        covered = np.empty_like(before)
        covered[order] = before
        return covered

    def _pieces(
        self, starts: _IntArray, ends: _IntArray, tracks: _IntArray,
        values: _FloatArray,
    ) -> tuple[_IntArray, _IntArray, _FloatArray]:
        """Split each range into one piece for each bin that it reaches.

        The pieces keep the order of the ranges, and the order of the
        bins inside a range.
        """
        # calc_bin_index and calc_bin_begin, over arrays.
        first = (starts - 1) // self.bin_size
        counts = (ends - 1) // self.bin_size - first + 1
        owner = np.repeat(np.arange(len(starts)), counts)
        bins = first[owner] + np.arange(len(owner)) \
            - np.repeat(np.cumsum(counts) - counts, counts)
        piece_starts = np.maximum(starts[owner], bins * self.bin_size + 1)
        piece_ends = np.minimum(ends[owner], (bins + 1) * self.bin_size)
        cells = (bins - self.first_bin) * self.tracks + tracks[owner]
        return cells, piece_ends - piece_starts + 1, values[owner]


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
        #: ``median``: the cells, values and weights of the open bins.
        self.open_cells: _IntArray = np.empty(0, dtype=np.int64)
        self.open_values: _FloatArray = np.empty(0, dtype=np.float64)
        self.open_weights: _IntArray = np.empty(0, dtype=np.int64)

    def add(
        self, begins: _IntArray, tracks: _IntArray, values: _FloatArray,
        frontier: int | None,
    ) -> None:
        """Add one window; no later fragment starts before ``frontier``."""
        cells = ((begins - 1) // self.bin_size - self.first_bin) \
            * self.tracks + tracks
        self.add_cells(cells, values, None, frontier)

    def add_cells(
        self, cells: _IntArray, values: _FloatArray,
        weights: _IntArray | None, frontier: int | None,
    ) -> None:
        """Add ``values`` to ``cells`` in input order, with ``weights``.

        ``None`` weighs each value 1.  ``counts`` sums the weights.  A
        ``count`` or a ``product`` takes no weights.  No later value goes
        to a bin before the one of ``frontier``.
        """
        np.add.at(self.counts, cells, 1 if weights is None else weights)
        with np.errstate(over="ignore", invalid="ignore"):
            if self.aggregator in ("sum", "mean"):
                np.add.at(
                    self.state, cells,
                    values if weights is None else values * weights)
            elif self.aggregator == "max":
                np.maximum.at(self.state, cells, values)
                self._keep_zero(cells, values, last=True)
            elif self.aggregator == "min":
                np.minimum.at(self.state, cells, values)
                self._keep_zero(cells, values, last=False)
            elif self.aggregator == "product":
                self._multiply(cells, values)
            elif self.aggregator == "median":
                self._median(
                    cells, values,
                    np.ones(len(cells), dtype=np.int64) if weights is None
                    else weights,
                    frontier)

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
        self, cells: _IntArray, values: _FloatArray, weights: _IntArray,
        frontier: int | None,
    ) -> None:
        """Keep the window open, then reduce the bins before ``frontier``.

        A bin's median is selected by weighted rank, as
        ``MedianAggregator`` does: the middle value for an odd total
        weight, the mean of the two middle values for an even one.  A
        stable sort keeps equal values in input order.
        """
        self.open_cells = np.concatenate([self.open_cells, cells])
        self.open_values = np.concatenate([self.open_values, values])
        self.open_weights = np.concatenate([self.open_weights, weights])
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
        done_weights = self.open_weights[closed]
        self.open_cells = self.open_cells[~closed]
        self.open_values = self.open_values[~closed]
        self.open_weights = self.open_weights[~closed]
        order = np.lexsort((done_values, done_cells))
        done_cells, done_values, done_weights = (
            done_cells[order], done_values[order], done_weights[order])
        cumulative = np.cumsum(done_weights)
        cell, first = np.unique(done_cells, return_index=True)
        before = cumulative[first] - done_weights[first]
        total = self.counts[cell]
        low = np.searchsorted(
            cumulative, before + (total - 1) // 2, side="right")
        high = np.searchsorted(cumulative, before + total // 2, side="right")
        self.state[cell] = np.where(
            total % 2 == 1, done_values[high],
            (done_values[low] + done_values[high]) / 2)


#: The state of a cell before its first value.  A ``sum`` starts at -0.0,
#: the one float that adds to any value without a change; ``mean`` starts
#: at 0, as ``MeanAggregator`` does.
_INITIAL: dict[str, float] = {
    "count": 0.0, "sum": -0.0, "mean": 0.0, "max": -np.inf,
    "min": np.inf, "median": np.nan, "product": 1.0,
}
