"""Streaming bin folds: fragments in, the bins of one track out.

A fold reduces the fragments of one track over one region, one grid bin
at a time.  It is fed :class:`FragmentValue` fragments in start order and
answers :class:`BinValue` bins, each as soon as no later fragment can
reach it; :meth:`BinFragmentAggregator.flush` answers the rest, so every
bin of the region is answered exactly once, in order, whether or not any
fragment arrived.

The bins are those of the global grid anchored at position 1: the first
is the one holding the region's start, the last the one holding its end,
and each is a whole grid bin, ``calc_bin_begin`` to ``calc_bin_end``,
not clipped to the region.  So the bins of adjacent regions split on a
bin edge follow on from each other.

What an empty bin holds is :data:`EMPTY_BIN_VALUES`, applied when the
bin is answered: a bin whose aggregator received no non-null value
answers ``0`` for ``count`` and ``sum`` and ``NaN`` for the others.

:class:`BinFragmentStartAggregator` puts a fragment in the bin holding
its start; :class:`BinFragmentLengthAggregator` in every bin it
overlaps, weighted by the overlap in base pairs.  The length fold reads
a fragment as a run ``(start, end, value)`` only, so any runs in start
order can be binned with it.  :class:`BinFragmentCoverageAggregator`
bins the fragments' coverage profile, :class:`CoverageProfile`: it
feeds the profile's runs to a length fold.
"""
from __future__ import annotations

import heapq
import math
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Iterable, Iterator
from typing import NamedTuple

from gain.genomic_resources.aggregators import Aggregator
from gain.genomic_resources.genomic_scores.aggregation import (
    EMPTY_BIN_VALUES,
)
from gain.genomic_resources.score_def import ScoreValue
from gain.utils.regions import calc_bin_begin, calc_bin_end, calc_bin_index


class FragmentValue(NamedTuple):
    """One fragment, ``[start, end]`` closed, and the value it adds."""

    start: int
    end: int
    value: ScoreValue


class BinValue(NamedTuple):
    """One grid bin, ``[start, end]`` closed, and its folded value."""

    start: int
    end: int
    value: float


class BinFragmentAggregator(ABC):
    """A streaming fold of one track's fragments into grid bins."""

    @abstractmethod
    def feed(self, fragment: FragmentValue) -> list[BinValue]:
        """Fold ``fragment``; answer the bins it shows are complete."""

    @abstractmethod
    def flush(self) -> list[BinValue]:
        """Answer every bin not answered yet, to the region's last."""


class BinFragmentStartAggregator(BinFragmentAggregator):
    """Each bin reduces the values of the fragments that START in it.

    A fragment belongs to the bin holding its start and to no other,
    however far it runs; its end is not read.  A fragment starting
    outside ``[start, end]``, or before the one fed ahead of it, is
    refused with a :class:`ValueError` when it is fed.  An exact int
    result past the float range (``sum`` or ``product`` of an int score)
    saturates to an infinity of its sign.
    """

    def __init__(
        self, *, start: int, end: int, bin_size: int, aggregator: str,
    ) -> None:
        self.start = start
        self.end = end
        self.previous = start
        self.bin_size = bin_size
        self.empty_value = EMPTY_BIN_VALUES[aggregator]
        self.current_bin = calc_bin_index(bin_size, start)
        self.last_bin = calc_bin_index(bin_size, end)
        self.aggregator = Aggregator.build(aggregator)

    def feed(self, fragment: FragmentValue) -> list[BinValue]:
        if not self.start <= fragment.start <= self.end:
            raise ValueError(
                f"a fragment at {fragment.start} is outside the binned "
                f"region [{self.start}, {self.end}]")
        if fragment.start < self.previous:
            raise ValueError(
                f"a fragment at {fragment.start} arrived after one at "
                f"{self.previous}; a bin fold needs its fragments sorted "
                f"by start")
        self.previous = fragment.start
        fragment_bin = calc_bin_index(self.bin_size, fragment.start)
        bins = self._emit_until(fragment_bin)
        self.aggregator.add(fragment.value)
        return bins

    def flush(self) -> list[BinValue]:
        return self._emit_until(self.last_bin + 1)

    def _emit_until(self, bin_index: int) -> list[BinValue]:
        """Answer the bins before ``bin_index``, from the current one."""
        bins = []
        while self.current_bin < bin_index:
            bins.append(BinValue(
                calc_bin_begin(self.bin_size, self.current_bin),
                calc_bin_end(self.bin_size, self.current_bin),
                self._final()))
            self.aggregator.clear()
            self.current_bin += 1
        return bins

    def _final(self) -> float:
        return _final_value(self.aggregator, self.empty_value)


class BinFragmentLengthAggregator(BinFragmentAggregator):
    """Each bin reduces the values of the fragments that OVERLAP it.

    A fragment adds its value to every bin it reaches, weighted by the
    base pairs it shares with the bin.  Fed fragments must lie inside
    ``[start, end]`` -- a caller clips them to the region -- and arrive
    in start order; any other is refused with a :class:`ValueError`.

    With an ``uncovered_value``, each base of ``[start, end]`` that no
    fed fragment covers adds that value with a weight of 1 bp, so no bin
    is empty; the part of an edge bin outside the region is not filled.
    With ``None``, an uncovered base adds nothing.

    The fragments come in start order, but an early long fragment can
    end after a later short one: each bin keeps its own aggregator until
    a fragment starts past it, or until :meth:`flush`, so a bin is
    answered only when nothing fed later can reach it.
    """

    def __init__(
        self, *, start: int, end: int, bin_size: int, aggregator: str,
        uncovered_value: float | None = None,
    ) -> None:
        self.start = start
        self.end = end
        self.previous = start
        self.bin_size = bin_size
        self.aggregator_name = aggregator
        self.empty_value = EMPTY_BIN_VALUES[aggregator]
        self.uncovered_value = uncovered_value
        self.current_bin = calc_bin_index(bin_size, start)
        self.last_bin = calc_bin_index(bin_size, end)
        #: The aggregators of the bins from ``current_bin`` on.
        self.pending: deque[Aggregator] = deque()
        #: The last base a fed fragment covers, ``start - 1`` before any.
        self.covered_end = start - 1

    def feed(self, fragment: FragmentValue) -> list[BinValue]:
        if not self.start <= fragment.start <= fragment.end <= self.end:
            raise ValueError(
                f"a fragment at [{fragment.start}, {fragment.end}] is not "
                f"inside the binned region [{self.start}, {self.end}]")
        if fragment.start < self.previous:
            raise ValueError(
                f"a fragment at {fragment.start} arrived after one at "
                f"{self.previous}; a bin fold needs its fragments sorted "
                f"by start")
        self.previous = fragment.start
        self._fill_uncovered(fragment.start - 1)
        self._add_range(fragment.start, fragment.end, fragment.value)
        self.covered_end = max(self.covered_end, fragment.end)
        return self._emit_until(calc_bin_index(self.bin_size, fragment.start))

    def flush(self) -> list[BinValue]:
        self._fill_uncovered(self.end)
        return self._emit_until(self.last_bin + 1)

    def _fill_uncovered(self, upto: int) -> None:
        """Add the uncovered value over the bases after the covered ones."""
        if self.uncovered_value is not None:
            self._add_range(
                self.covered_end + 1, upto, self.uncovered_value)
        self.covered_end = max(self.covered_end, upto)

    def _add_range(self, start: int, end: int, value: ScoreValue) -> None:
        """Add ``value`` over ``[start, end]``, weighted per bin by overlap."""
        while start <= end:
            bin_index = calc_bin_index(self.bin_size, start)
            piece_end = min(end, calc_bin_end(self.bin_size, bin_index))
            self._aggregator(bin_index).add(value, piece_end - start + 1)
            start = piece_end + 1

    def _aggregator(self, bin_index: int) -> Aggregator:
        offset = bin_index - self.current_bin
        while len(self.pending) <= offset:
            self.pending.append(Aggregator.build(self.aggregator_name))
        return self.pending[offset]

    def _emit_until(self, bin_index: int) -> list[BinValue]:
        """Answer the bins before ``bin_index``, from the current one."""
        bins = []
        while self.current_bin < bin_index:
            value = _final_value(self.pending.popleft(), self.empty_value) \
                if self.pending else self.empty_value
            bins.append(BinValue(
                calc_bin_begin(self.bin_size, self.current_bin),
                calc_bin_end(self.bin_size, self.current_bin),
                value))
            self.current_bin += 1
        return bins


class CoverageProfile:
    """The coverage profile of fragments fed in start order, as runs.

    The profile's value at a position is the sum of the values of the
    fed fragments that cover it.  It is answered as runs ``(start, end,
    value)``, in order: each a maximal stretch of one non-zero value, so
    two adjacent runs never share a value, and a position the profile
    leaves at 0 -- no fragment, or values cancelling to 0 -- is in no
    run.  A fragment with a ``None`` value adds nothing.  The sum keeps
    the type of the values: ``int`` values sum to an ``int``, and any
    ``float`` among them makes it a correctly rounded ``float`` sum.

    :meth:`feed` answers the runs a fragment shows are complete, and
    :meth:`flush` the rest.  A fragment starting before the one fed
    ahead of it is refused with a :class:`ValueError`.  Nothing is kept
    per base pair, so the cost is per fragment, however long a fragment
    or a gap between fragments is.
    """

    def __init__(self) -> None:
        #: ``(end, order, value)`` of each fragment covering ``cursor``.
        self.active: list[tuple[int, int, int | float]] = []
        self.fed = 0
        self.previous: int | None = None
        #: The first position whose profile no run has answered yet.
        self.cursor: int | None = None
        self.floating = False
        self.level: int | float = 0
        #: The last run, held back until the next shows it does not
        #: continue it with the same value.
        self.held: FragmentValue | None = None

    def feed(self, fragment: FragmentValue) -> list[FragmentValue]:
        """Add ``fragment``; answer the runs ending before its start."""
        if self.previous is not None and fragment.start < self.previous:
            raise ValueError(
                f"a fragment at {fragment.start} arrived after one at "
                f"{self.previous}; a coverage profile needs its fragments "
                f"sorted by start")
        self.previous = fragment.start
        runs = self._advance(fragment.start)
        value = fragment.value
        if value is None or fragment.end < fragment.start:
            return runs
        if not isinstance(value, int | float):
            raise TypeError(
                f"a fragment's value {value!r} is not a number")
        if self.cursor is None:
            self.cursor = fragment.start
        self.floating = self.floating or isinstance(value, float)
        self.fed += 1
        heapq.heappush(self.active, (fragment.end, self.fed, value))
        self._relevel(value)
        return runs

    def flush(self) -> list[FragmentValue]:
        """Answer every run not answered yet."""
        runs = self._advance(None)
        if self.held is not None:
            runs.append(self.held)
            self.held = None
        return runs

    def _advance(self, upto: int | None) -> list[FragmentValue]:
        """Answer the profile before ``upto``, or all of it for ``None``."""
        runs: list[FragmentValue] = []
        while self.active and (upto is None or self.active[0][0] < upto):
            end = self.active[0][0]
            self._run(end, runs)
            while self.active and self.active[0][0] == end:
                self._relevel(-heapq.heappop(self.active)[2])
        if upto is not None:
            self._run(upto - 1, runs)
        return runs

    def _run(self, end: int, runs: list[FragmentValue]) -> None:
        """Close the run of the current level at ``end``."""
        if self.cursor is None or end < self.cursor:
            return
        start, self.cursor = self.cursor, end + 1
        if self.level == 0:
            return
        held = self.held
        if held is not None and held.end + 1 == start \
                and held.value == self.level:
            self.held = FragmentValue(held.start, end, self.level)
            return
        if held is not None:
            runs.append(held)
        self.held = FragmentValue(start, end, self.level)

    def _relevel(self, change: int | float) -> None:
        """The level after ``change``: int sums run on exactly, a float
        one is summed afresh, so no rounding error accumulates."""
        if self.floating:
            self.level = math.fsum(value for _, _, value in self.active)
        else:
            self.level += change


class BinFragmentCoverageAggregator(BinFragmentAggregator):
    """Each bin reduces the coverage profile of the fragments over it.

    The fed fragments make a :class:`CoverageProfile`, and its runs are
    folded by a :class:`BinFragmentLengthAggregator`: each bin reduces
    the profile's values weighted by the base pairs they hold in it.  A
    position the profile leaves at 0 is uncovered, so with an
    ``uncovered_value`` it adds that value with a weight of 1 bp, and
    with ``None`` nothing.  Fed fragments must lie inside ``[start,
    end]`` -- a caller clips them to the region -- and arrive in start
    order; any other is refused with a :class:`ValueError`.
    """

    def __init__(
        self, *, start: int, end: int, bin_size: int, aggregator: str,
        uncovered_value: float | None = None,
    ) -> None:
        self.start = start
        self.end = end
        self.profile = CoverageProfile()
        self.runs = BinFragmentLengthAggregator(
            start=start, end=end, bin_size=bin_size, aggregator=aggregator,
            uncovered_value=uncovered_value)

    def feed(self, fragment: FragmentValue) -> list[BinValue]:
        if not self.start <= fragment.start <= fragment.end <= self.end:
            raise ValueError(
                f"a fragment at [{fragment.start}, {fragment.end}] is not "
                f"inside the binned region [{self.start}, {self.end}]")
        return self._fold(self.profile.feed(fragment))

    def flush(self) -> list[BinValue]:
        bins = self._fold(self.profile.flush())
        bins.extend(self.runs.flush())
        return bins

    def _fold(self, runs: list[FragmentValue]) -> list[BinValue]:
        bins: list[BinValue] = []
        for run in runs:
            bins.extend(self.runs.feed(run))
        return bins


def coverage_profile(
    fragments: Iterable[FragmentValue],
) -> Iterator[FragmentValue]:
    """The runs of the coverage profile of ``fragments``, in start order.

    See :class:`CoverageProfile`.
    """
    profile = CoverageProfile()
    for fragment in fragments:
        yield from profile.feed(fragment)
    yield from profile.flush()


def _final_value(aggregator: Aggregator, empty_value: float) -> float:
    """A bin's value: the aggregator's, or ``empty_value`` for none."""
    final = aggregator.get_final()
    if final is None:
        return empty_value
    try:
        return float(final)
    except OverflowError:
        # An exact int (``sum``/``product`` of an int score) past the
        # float range saturates, as a float result already does.
        return math.inf if final > 0 else -math.inf
