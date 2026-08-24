"""Covered-position statistics for tabular genomic scores.

Vocabulary per ``CONTEXT.md`` and ADR 0020: a **covered position** is a
position spanned by at least one table row — value-blind, union semantics.
A **segment** is a maximal run of touching-or-overlapping rows carrying
equal values (the whole scanned score tuple, NA equal to NA, floats exact).
"""
from __future__ import annotations

import json
import math
from collections.abc import Iterable
from typing import Any

from gain.genomic_resources.statistics.base_statistic import Statistic

COVERAGE_STATISTICS_FILE = "statistics/coverage.json"


def normalize_values(values: Iterable[Any]) -> tuple:
    """A row's score values as the tuple segment equality compares.

    Every spelling of "no value" -- ``None`` on the per-record path, nan
    in a bulk float column -- becomes ``None``, so NA equals NA whichever
    path produced the row (ADR 0020).  Everything else is compared as
    stored: floats exactly, no tolerance.
    """
    return tuple(
        None if value is None
        or (isinstance(value, float) and math.isnan(value))
        else value
        for value in values)


class RegionCoverage:
    """Coverage of one scanned region, accumulated row by row.

    Consumes clipped ``[begin, end]`` spans in non-decreasing ``begin``
    order — the order the scan validators guarantee — and counts each
    position once however many rows span it (a running-maximum union, so
    nested and overlapping fragment rows are handled).
    """

    def __init__(
        self,
        chrom: str,
        start: int | None,
        end: int | None,
    ) -> None:
        self.chrom = chrom
        self.start = start
        self.end = end
        self.covered = 0
        # The rightmost covered position so far; union means only the part
        # of a row past this mark adds new covered positions.
        self._covered_through: int | None = None
        self._closed_segments = 0
        # The open run at the scan's right edge: (begin, end, values).
        self._run: tuple[int, int, tuple] | None = None
        # The first run, frozen when it closes.  While no run has closed,
        # the first run IS the open run -- the region is one run end to
        # end exactly when ``_closed_segments == 0`` -- which is what lets
        # a segment spanning three or more chunks stay one segment: the
        # middle chunks' head and tail are the same run, never two.
        self._first_run: tuple[int, int, tuple] | None = None

    @property
    def segment_count(self) -> int:
        return self._closed_segments + (1 if self._run is not None else 0)

    def _first(self) -> tuple[int, int, tuple] | None:
        """The leftmost run -- frozen if closed, the open run otherwise."""
        if self._closed_segments:
            return self._first_run
        return self._run

    def merge(self, other: RegionCoverage) -> None:
        """Fold the adjacent region to the right into this one.

        Refuses a pair that is not adjacent-and-in-order on one
        chromosome: region statistics are only ever produced over a
        contig's non-overlapping windows, so anything else reaching here
        is a wiring error, and refusing it loudly is the difference
        between a failed build and a silently wrong coverage table.
        """
        if self.chrom != other.chrom:
            raise ValueError(
                f"coverage merge across chromosome boundaries: "
                f"{self.chrom} and {other.chrom}")
        if self.end is None or other.start is None \
                or self.end + 1 != other.start:
            raise ValueError(
                f"coverage regions are not adjacent-and-in-order: "
                f"{self.chrom}:{self.start}-{self.end} then "
                f"{other.chrom}:{other.start}-{other.end}")

        self.covered += other.covered
        if other._run is None:
            self.end = other.end
            return
        if self._run is None:
            self._closed_segments = other._closed_segments
            self._first_run = other._first_run
            self._run = other._run
        else:
            self._merge_runs(other)
        self._covered_through = other._covered_through
        self.end = other.end

    def _merge_runs(self, other: RegionCoverage) -> None:
        """Combine the run bookkeeping of two non-empty regions.

        The one stitch decision: this region's open run and the other's
        first run are one segment exactly when both abut the shared
        boundary and carry equal values.
        """
        assert self._run is not None
        assert other._run is not None
        other_first = other._first()
        assert other_first is not None
        last_begin, last_end, last_values = self._run
        first_begin, first_end, first_values = other_first

        stitch = (
            last_end == self.end
            and first_begin == other.start
            and last_values == first_values
        )
        if stitch and not other._closed_segments:
            # The other region is one run end to end; the combined run
            # stays open for the next merge.
            self._run = (last_begin, other._run[1], last_values)
            return
        if stitch:
            combined = (last_begin, first_end, last_values)
            if not self._closed_segments:
                self._first_run = combined
            self._closed_segments += other._closed_segments
        else:
            if not self._closed_segments:
                self._first_run = self._run
            self._closed_segments += \
                1 + other._closed_segments
        self._run = other._run

    def add_interval(
        self,
        begin: int,
        end: int,
        values: tuple,
    ) -> None:
        """Fold one clipped row span into the coverage."""
        if self._covered_through is None or begin > self._covered_through:
            self.covered += end - begin + 1
            self._covered_through = end
        elif end > self._covered_through:
            self.covered += end - self._covered_through
            self._covered_through = end

        if self._run is not None:
            run_begin, run_end, run_values = self._run
            if values == run_values and begin <= run_end + 1:
                self._run = (run_begin, max(run_end, end), run_values)
                return
            if not self._closed_segments:
                self._first_run = self._run
            self._closed_segments += 1
        self._run = (begin, end, values)


class CoverageStatistics(Statistic):
    """A resource's covered positions, per chromosome and global.

    Accumulates one :class:`RegionCoverage` per scanned region through
    :meth:`fold_region` — same-chromosome regions merge (adjacency
    asserted there), distinct chromosomes accumulate side by side — and
    serializes to the resource's ``statistics/coverage.json`` as raw
    counts.  Fractions are deliberately not computed here: they need
    chromosome lengths, which belong to a reference genome resolved at
    render time.
    """

    def __init__(self) -> None:
        super().__init__(
            "coverage", "Covered positions per chromosome and global")
        self._regions: dict[str, RegionCoverage] = {}

    def fold_region(self, region: RegionCoverage) -> None:
        """Fold one region's coverage in, keyed by its chromosome."""
        held = self._regions.get(region.chrom)
        if held is None:
            self._regions[region.chrom] = region
        else:
            held.merge(region)

    def covered_by_chromosome(self) -> dict[str, int]:
        return {
            chrom: region.covered
            for chrom, region in self._regions.items()
        }

    def covered_global(self) -> int:
        return sum(region.covered for region in self._regions.values())

    def add_value(self, value: Any) -> None:  # noqa: ARG002
        raise TypeError(
            "CoverageStatistics accumulates regions, not values; "
            "use fold_region")

    def merge(self, other: Statistic) -> None:
        if not isinstance(other, CoverageStatistics):
            raise TypeError("unexpected type of statistics to merge with")
        for region in other._regions.values():  # noqa: SLF001
            self.fold_region(region)

    def serialize(self) -> str:
        return json.dumps({
            "format_version": 1,
            "chromosomes": {
                chrom: {"covered_positions": covered}
                for chrom, covered in self.covered_by_chromosome().items()
            },
            "global": {"covered_positions": self.covered_global()},
        }, indent=2)

    @staticmethod
    def deserialize(content: str) -> CoverageStatistics:
        # Only the counts round-trip; the open-run bookkeeping is scan
        # state and is never written.  Unknown keys are ignored rather
        # than rejected, so a file carrying extra fields still reads.
        data = json.loads(content)
        result = CoverageStatistics()
        for chrom, counts in data["chromosomes"].items():
            region = RegionCoverage(chrom, None, None)
            region.covered = int(counts["covered_positions"])
            result.fold_region(region)
        return result
