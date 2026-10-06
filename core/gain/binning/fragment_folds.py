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
"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod
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
        self.aggregator_name = aggregator
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
            self.aggregator = Aggregator.build(self.aggregator_name)
            self.current_bin += 1
        return bins

    def _final(self) -> float:
        final = self.aggregator.get_final()
        if final is None:
            return self.empty_value
        try:
            return float(final)
        except OverflowError:
            # An exact int (``sum``/``product`` of an int score) past the
            # float range saturates, as a float result already does.
            return math.inf if final > 0 else -math.inf
