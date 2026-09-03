"""Covered-position statistics for tabular genomic scores.

Vocabulary per ``CONTEXT.md`` and ADR 0020: a **covered position** is a
position spanned by at least one table row — value-blind, union semantics.
A **segment** is a maximal run of touching-or-overlapping rows carrying
equal values (the whole scanned score tuple, NA equal to NA, floats exact).

The whole of this statistic lives here: the per-region accumulator and
the resource-wide statistic, the fold that merges a scan's regions into
one, the write, and the render payload the info page reads.  Its allele
twin is laid out the same way in
:mod:`gain.genomic_resources.statistics.alleles`; the scan wiring that
feeds either is in ``implementations/genomic_scores_impl/scan.py``.
"""
from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterable
from typing import Any, NamedTuple

import numpy as np

from gain import logging
from gain.genomic_resources.cli_errors import report_resource_failure
from gain.genomic_resources.genomic_scores import (
    GenomicScore,
    RecordArrays,
    owned_records_mask,
)
from gain.genomic_resources.reference_genome import ReferenceGenome
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.statistics.base_statistic import (
    Statistic,
    refuse_unmergeable,
    regions_in_genomic_order,
)
from gain.genomic_resources.statistics.length_histogram import (
    LENGTH_HISTOGRAM_BIN_COUNT,
    accumulate_bins,
    has_counts_to_plot,
    length_histogram_bin_index,
    plot_length_histogram,
)
from gain.genomic_resources.statistics.percentages import percentage_of
from gain.utils.chromosome_order import natural_chromosome_key

logger = logging.getLogger(__name__)

COVERAGE_STATISTICS_FILE = "statistics/coverage.json"

#: How a failed fold of these regions is named in the message.
_MERGE_FAILURE = "coverage"
COVERAGE_SEGMENT_LENGTHS_IMAGE_FILE = \
    "statistics/coverage_segment_lengths.png"


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

    Consumes ``[begin, end]`` spans in non-decreasing ``begin`` order —
    the order the scan validators guarantee — and counts each position
    once however many rows span it (a running-maximum union, so nested
    and overlapping fragment rows are handled).  Whether those spans
    arrive clipped to the region is the scan's decision, taken from
    :attr:`rows_are_disjoint`.
    """

    def __init__(
        self,
        chrom: str,
        start: int | None,
        end: int | None,
        *,
        rows_are_disjoint: bool = True,
    ) -> None:
        self.chrom = chrom
        self.start = start
        self.end = end
        # ONE fact about the kind being scanned, with two consequences.
        # Rows no two of which share a position (a position score, whose
        # validators refuse a row beginning at or before its
        # predecessor's end) have an exact run algebra, so their segment
        # summary is published; and they cannot double-count a position,
        # so the scan may hand this their FULL spans and the union stays
        # additive across regions.  Rows that can overlap (fragments)
        # publish no segment summary -- not wanted, ADR 0020 as amended
        # by gain#926, so they build no runs either -- and must be
        # handed spans clipped to the region -- see
        # ``accumulate_coverage`` below.
        #
        # Disjoint, NOT "non-touching": adjacent rows are legal, and the
        # stitch in ``_merge_runs`` depends on them being so.
        self._rows_are_disjoint = rows_are_disjoint
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
        # Lengths of the INTERIOR closed segments -- every closed run
        # except the first -- on the fixed log2 bins.  The first and the
        # open run are excluded because either may still stitch across a
        # merge boundary; their lengths are only final at read time.
        self._interior_bins = [0] * LENGTH_HISTOGRAM_BIN_COUNT
        # A deserialized region's segment data, frozen as read; it
        # carries no scan state.
        self._frozen_segments: tuple[int, list[int]] | None = None

    @classmethod
    def frozen(
        cls,
        chrom: str,
        covered: int,
        segments: tuple[int, list[int]] | None,
    ) -> RegionCoverage:
        """A region restored from serialized counts, with no scan state.

        ``segments`` of ``None`` marks that data unknown -- the file
        predates it, carries foreign bins, or the kind publishes none.
        A frozen region never accumulates a span, so
        ``rows_are_disjoint`` has no clipping consequence here; it
        carries only the other one, gating the summary this region can
        answer with.
        """
        region = cls(
            chrom, None, None,
            rows_are_disjoint=segments is not None)
        region.covered = covered
        region._frozen_segments = segments
        return region

    @property
    def rows_are_disjoint(self) -> bool:
        """Whether no two of the scanned kind's rows share a position.

        Read by the scan to decide whether to clip the spans it hands
        :meth:`add_interval` -- see the constructor for the one fact and
        its two consequences.
        """
        return self._rows_are_disjoint

    def segment_summary(self) -> tuple[int, list[int]] | None:
        """Segment count and length histogram, or ``None`` if unknown.

        Unknown means the region does not track segments: rows that
        overlap, for which segments are not wanted (ADR 0020, amended
        by gain#926), or a region deserialized from a statistics file
        that predates segment-length histograms.  This is the ASKING
        form of the gate the count and histogram accessors refuse
        through -- ``None`` here, an exception there, because a caller
        that asks may not know and one that reaches straight for a
        number has asserted it does.
        """
        if not self._publishes_segments:
            return None
        return self.segment_count, self.segment_length_histogram()

    def segment_length_histogram(self) -> list[int]:
        """Counts of segment lengths on the fixed log2 bins.

        Finalizes the still-open bookkeeping: the first and the open run
        are folded in on top of the interior counts, so the histogram
        totals exactly ``segment_count``.  Refuses a region that
        publishes none -- see :meth:`_refuse_without_segments`.
        """
        self._refuse_without_segments()
        if self._frozen_segments is not None:
            return list(self._frozen_segments[1])
        histogram = list(self._interior_bins)
        if self._closed_segments:
            first = self._first_run
            assert first is not None
            self._add_to(histogram, first)
        if self._run is not None:
            self._add_to(histogram, self._run)
        return histogram

    @staticmethod
    def _add_to(
        histogram: list[int],
        run: tuple[int, int, tuple],
    ) -> None:
        begin, end, _ = run
        histogram[length_histogram_bin_index(end - begin + 1)] += 1

    def _record_closed(self, run: tuple[int, int, tuple]) -> None:
        """A run closed: freeze the first, bin the interior ones.

        The caller still advances ``_closed_segments`` itself -- a
        stitched merge records the combined run here but counts it
        through the other region's tally.

        Reached only for a disjoint kind -- :meth:`add_interval` opens
        no run for one whose rows overlap -- so every run arriving here
        belongs to a segmentation that will be published.
        """
        if not self._closed_segments:
            self._first_run = run
        else:
            self._add_to(self._interior_bins, run)

    @property
    def segment_count(self) -> int:
        """How many segments the region holds.

        Refuses a region that publishes none -- see
        :meth:`_refuse_without_segments`.
        """
        self._refuse_without_segments()
        if self._frozen_segments is not None:
            return self._frozen_segments[0]
        return self._closed_segments + (1 if self._run is not None else 0)

    @property
    def _publishes_segments(self) -> bool:
        """Whether this region has segment numbers to answer with.

        The one predicate behind both the summary's ``None`` and the
        accessors' refusal, so the two gates cannot drift apart.  It
        reads ``_rows_are_disjoint``, which carries this second meaning
        alongside its clipping one -- a scanned region publishes
        segments exactly when its rows have an exact run algebra, and
        :meth:`frozen` reuses the flag to mark a deserialized region
        whose file carried no segment data.  Deliberately reads the
        FIELD rather than the public :attr:`rows_are_disjoint`, which
        is the clipping view: the two are siblings over one flag, not
        one built on the other, and if the flag is ever split this gate
        must follow the publishing meaning, not the clipping one.
        """
        return self._rows_are_disjoint

    def _refuse_without_segments(self) -> None:
        """Guard both segment accessors on a region that has none.

        Both kinds of region that land here hold the same wrong
        answer, zero segments of zero length: a SCANNED region of
        overlapping rows opens no run at all (gain#926), and a
        DESERIALIZED region whose file carried no segment data never
        had one.  Their numbers agree, and agree on a lie -- zero
        reads as scanned-and-empty rather than as not-wanted or
        never-scanned, and only :meth:`segment_summary`'s ``None``
        tells those apart.

        Before gain#926 the scanned case was worse: the run
        bookkeeping ran but ``_record_closed`` binned no interior run,
        so a count answered here outran its own histogram and broke
        the contract :meth:`segment_length_histogram` states.  That
        inconsistency is what gain#1043 was filed for; gating off the
        bookkeeping replaced it with a uniform zero, which is quieter
        and no more true.  Either way the numbers must not escape, and
        fragments have no exact run algebra that could make them mean
        anything.

        Ask through :meth:`segment_summary` instead, which answers
        ``None`` -- a caller that asks may not know, one that reaches
        straight for a number has asserted it does.
        """
        if not self._publishes_segments:
            raise ValueError(
                f"region {self.chrom} publishes no segment statistics: "
                "its rows overlap, or it was read from a statistics "
                "file carrying none; ask segment_summary()")

    def _first(self) -> tuple[int, int, tuple] | None:
        """The leftmost run -- frozen if closed, the open run otherwise."""
        if self._closed_segments:
            return self._first_run
        return self._run

    def merge(self, other: RegionCoverage) -> None:
        """Fold the adjacent region to the right into this one.

        Refuses a pair that is not adjacent-and-in-order on one
        chromosome -- see ``refuse_unmergeable``, which states that rule
        for this statistic and its allele twin alike.
        """
        refuse_unmergeable(_MERGE_FAILURE, self, other)

        self.covered += other.covered
        self._rows_are_disjoint = \
            self._rows_are_disjoint and other._rows_are_disjoint
        if other._run is None:
            self.end = other.end
            return
        if self._run is None:
            self._closed_segments = other._closed_segments
            self._first_run = other._first_run
            self._run = other._run
            self._interior_bins = list(other._interior_bins)
        else:
            self._merge_runs(other)
        self._covered_through = other._covered_through
        self.end = other.end

    def _merge_runs(self, other: RegionCoverage) -> None:
        """Combine the run bookkeeping of two non-empty regions.

        Both are of a disjoint kind: an overlapping one holds no open
        run, so :meth:`merge` never reaches here with one.

        The one stitch decision: this region's open run and the other's
        first run are one segment exactly when they touch or overlap and
        carry equal values -- the very test :meth:`add_interval` applies
        row by row, stated once more across a merge boundary.

        It is deliberately NOT "both runs abut the shared boundary".
        That was the same test in a world where every span arrived
        clipped to its region, which made abutting the boundary the only
        way two runs could touch.  A region handed FULL spans (an
        unclipped, disjoint kind -- see :attr:`rows_are_disjoint`) has
        runs that reach past its own extent, and abutting would refuse
        to stitch a segment that plainly continues.
        """
        assert self._run is not None
        assert other._run is not None
        other_first = other._first()
        assert other_first is not None
        last_begin, last_end, last_values = self._run
        first_begin, first_end, first_values = other_first

        stitch = (
            first_begin <= last_end + 1
            and last_values == first_values
        )
        # The combined run ends at the wider of the two ends, the same
        # maximum :meth:`add_interval` takes row by row.  Under the old
        # boundary-abutting stitch the other run's end was wider by
        # construction; the touching test that replaced it admits a run
        # nested inside this one, and taking that end would report the
        # segment short.
        if stitch and not other._closed_segments:
            # The other region is one run end to end; the combined run
            # stays open for the next merge.
            self._run = (
                last_begin, max(last_end, other._run[1]), last_values)
            return
        accumulate_bins(self._interior_bins, other._interior_bins)
        if stitch:
            self._record_closed(
                (last_begin, max(last_end, first_end), last_values))
            self._closed_segments += other._closed_segments
        else:
            self._record_closed(self._run)
            if other._closed_segments:
                # The other region's first run closed there without
                # being binned -- it could still have stitched.  It did
                # not, so it is interior of the merged region now.
                self._add_to(self._interior_bins, other_first)
            self._closed_segments += \
                1 + other._closed_segments
        self._run = other._run

    def add_span(self, begin: int, end: int) -> None:
        """Union one row span into the covered count, values ignored.

        The whole of what a kind publishing no segments needs, and the
        first half of :meth:`add_interval` for one that does.  Clipped
        to the region or not, as :attr:`rows_are_disjoint` decides at
        the scan; this only unions what it is handed.
        """
        if self._covered_through is None or begin > self._covered_through:
            self.covered += end - begin + 1
            self._covered_through = end
        elif end > self._covered_through:
            self.covered += end - self._covered_through
            self._covered_through = end

    def add_interval(
        self,
        begin: int,
        end: int,
        values: tuple,
    ) -> None:
        """Fold one row span into the coverage and its run bookkeeping.

        The union first, then the runs -- and the runs ONLY for a kind
        whose rows are disjoint.  A kind whose rows overlap publishes
        no segments (ADR 0020, amended by gain#926: not merely deferred
        -- not wanted), so building runs for it would be work whose
        only product is discarded, on exactly the kind with the largest
        tables.  Gating here rather than at each feed is what makes the
        invariant hold however the region is fed: **a region whose rows
        overlap never opens a run**, which is what leaves the disjoint
        branches of :meth:`_record_closed` and :meth:`_merge_runs`
        unreachable.
        """
        self.add_span(begin, end)
        if not self._rows_are_disjoint:
            return

        if self._run is not None:
            run_begin, run_end, run_values = self._run
            if values == run_values and begin <= run_end + 1:
                self._run = (run_begin, max(run_end, end), run_values)
                return
            self._record_closed(self._run)
            self._closed_segments += 1
        self._run = (begin, end, values)

    def add_interval_batch(
        self,
        left: np.ndarray,
        right: np.ndarray,
        cells: list[np.ndarray],
    ) -> None:
        """Fold a batch of row spans, collapsed into runs.

        The vectorized statement of the rule :meth:`add_interval`
        applies row by row — it lives HERE, beside that rule, so the
        equality algebra has one home: rows collapse into a run while
        they touch or overlap the positions covered so far and every
        column compares equal, nan equal to nan (ADR 0020), and each
        run costs one :meth:`add_interval` rather than one per row.

        The touching test reads the running maximum end, which is exact
        for a position score (whose validators refuse overlap, so the
        previous row IS the running maximum).  A kind whose rows can
        overlap publishes no segments at all (ADR 0020, amended by
        gain#926 — not wanted, not merely deferred), so it takes the
        value-blind collapse below: the per-column equality and the
        per-run value gather are skipped entirely, and only the union
        the covered count needs is done.  That union is exact whatever
        run shapes arrive, which is why it may be taken value-blind.

        ``left``/``right`` are the spans as the scan decided to hand
        them over -- clipped to the region for an overlapping kind, the
        rows' own full extents for a disjoint one -- and ``cells`` is
        one kept column per scanned score, all equally long.  ``cells``
        is read only for a disjoint kind: nothing else compares values.
        """
        count = left.shape[0]
        if not count:
            return
        if not self._rows_are_disjoint:
            self._add_span_batch(left, right)
            return
        boundary = np.ones(count, dtype=bool)
        if count > 1:
            equal = left[1:] <= np.maximum.accumulate(right)[:-1] + 1
            for column in cells:
                head, prev = column[1:], column[:-1]
                if column.dtype == object:
                    same = head == prev
                else:
                    same = (head == prev) \
                        | (np.isnan(head) & np.isnan(prev))
                equal &= same
            boundary[1:] = ~equal
        starts = np.flatnonzero(boundary)
        run_begins = left[starts].tolist()
        run_ends = np.maximum.reduceat(right, starts).tolist()
        # Gather per-run values vectorized, then hand the loop plain
        # Python objects: per-run numpy scalar indexing would put the
        # object churn ADR 0001 deleted back on the hot path for the
        # common one-value-per-row score, where runs are rows.
        columns = []
        for column in cells:
            gathered = column[starts]
            if gathered.dtype == object:
                columns.append(gathered.tolist())
            else:
                columns.append([
                    None if is_nan else value
                    for value, is_nan in zip(
                        gathered.tolist(),
                        np.isnan(gathered).tolist(), strict=True)
                ])
        run_values = list(zip(*columns, strict=True)) if columns \
            else [()] * len(run_begins)
        for begin, end, values in zip(
                run_begins, run_ends, run_values, strict=True):
            self.add_interval(begin, end, values)

    def _add_span_batch(
        self,
        left: np.ndarray,
        right: np.ndarray,
    ) -> None:
        """Union a batch of spans, value-blind -- the overlapping kind.

        The same collapse :meth:`add_interval_batch` does, minus the
        equality: spans join a run while they touch or overlap the
        positions covered so far, whatever they carry.  It reaches the
        same covered count because :meth:`add_span` unions whatever run
        shapes arrive, and it hands the loop the shapes that make the
        loop shortest -- heavily overlapping fragment rows collapse to
        one span per contiguous stretch.
        """
        count = left.shape[0]
        boundary = np.ones(count, dtype=bool)
        if count > 1:
            boundary[1:] = \
                left[1:] > np.maximum.accumulate(right)[:-1] + 1
        starts = np.flatnonzero(boundary)
        for begin, end in zip(
                left[starts].tolist(),
                np.maximum.reduceat(right, starts).tolist(), strict=True):
            self.add_span(begin, end)


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

    def _summaries(
        self, summary_of: _SummaryOf,
    ) -> dict[str, tuple[int, list[int]]] | None:
        """Per-chromosome summaries of one group, or ``None`` if any
        chromosome lacks them -- a partial global would silently
        understate."""
        summaries = {}
        for chrom, region in self._regions.items():
            summary = summary_of(region)
            if summary is None:
                return None
            summaries[chrom] = summary
        return summaries

    def _counts_by_chromosome(self, summary_of: _SummaryOf) -> dict[str, int]:
        summaries = self._summaries(summary_of)
        if summaries is None:
            return {}
        return {chrom: count for chrom, (count, _) in summaries.items()}

    def _count_global(self, summary_of: _SummaryOf) -> int | None:
        summaries = self._summaries(summary_of)
        if summaries is None:
            return None
        return sum(count for count, _ in summaries.values())

    def _lengths_by_chromosome(
        self, summary_of: _SummaryOf,
    ) -> dict[str, list[int]]:
        summaries = self._summaries(summary_of)
        if summaries is None:
            return {}
        return {
            chrom: histogram
            for chrom, (_, histogram) in summaries.items()
        }

    def _lengths_global(self, summary_of: _SummaryOf) -> list[int] | None:
        summaries = self._summaries(summary_of)
        if summaries is None:
            return None
        return self._binwise_sum(
            histogram for _, histogram in summaries.values())

    def segments_by_chromosome(self) -> dict[str, int]:
        return self._counts_by_chromosome(RegionCoverage.segment_summary)

    def segments_global(self) -> int | None:
        return self._count_global(RegionCoverage.segment_summary)

    def segment_lengths_by_chromosome(self) -> dict[str, list[int]]:
        """Per-chromosome length histograms -- the read API for the
        per-chromosome data the statistics file stores (rendered
        consumers use the global roll-up; gain#776 reads these)."""
        return self._lengths_by_chromosome(RegionCoverage.segment_summary)

    def segment_lengths_global(self) -> list[int] | None:
        """The bin-wise sum of the per-chromosome length histograms."""
        return self._lengths_global(RegionCoverage.segment_summary)

    @staticmethod
    def _binwise_sum(histograms: Iterable[list[int]]) -> list[int]:
        merged = [0] * LENGTH_HISTOGRAM_BIN_COUNT
        for histogram in histograms:
            accumulate_bins(merged, histogram)
        return merged

    def add_value(self, value: Any) -> None:  # ruff: ignore[unused-method-argument]
        raise TypeError(
            "CoverageStatistics accumulates regions, not values; "
            "use fold_region")

    def merge(self, other: Statistic) -> None:
        """Fold another statistics object's regions into this one.

        For statistics holding LIVE regions (the scan's own): two
        deserialized statistics carry no extents, so same-chromosome
        regions from two files refuse to merge as non-adjacent.
        """
        if not isinstance(other, CoverageStatistics):
            raise TypeError("unexpected type of statistics to merge with")
        for region in other._regions.values():  # ruff: ignore[private-member-access]
            self.fold_region(region)

    def serialize(self) -> str:
        # One walk of the regions serves the per-chromosome entries and
        # the global roll-up, for every optional group; a group's global
        # keys are written only when EVERY chromosome has that summary
        # (a partial global would silently understate).
        chromosomes: dict[str, dict[str, Any]] = {}
        rollups: dict[str, dict[str, tuple[int, list[int]]] | None] = {
            name: {} for name, _ in _STORED_SUMMARIES}
        for chrom, region in self._regions.items():
            entry: dict[str, Any] = {
                "covered_positions": region.covered,
            }
            for name, summary_of in _STORED_SUMMARIES:
                summary = summary_of(region)
                if summary is None:
                    rollups[name] = None
                    continue
                entry[f"{name}_count"] = summary[0]
                entry[f"{name}_length_histogram"] = summary[1]
                held = rollups[name]
                if held is not None:
                    held[chrom] = summary
            chromosomes[chrom] = entry
        global_entry: dict[str, Any] = {
            "covered_positions": self.covered_global(),
        }
        for name, _ in _STORED_SUMMARIES:
            held = rollups[name]
            if held is None:
                continue
            global_entry[f"{name}_count"] = sum(
                count for count, _ in held.values())
            global_entry[f"{name}_length_histogram"] = self._binwise_sum(
                histogram for _, histogram in held.values())
        return json.dumps({
            "format_version": 1,
            "chromosomes": chromosomes,
            "global": global_entry,
        }, indent=2)

    @staticmethod
    def deserialize(content: str) -> CoverageStatistics:
        # Only the counts round-trip; the open-run bookkeeping is scan
        # state and is never written.  Unknown keys are ignored rather
        # than rejected, so a file carrying extra fields still reads,
        # and a file written before a group existed reads with that
        # group unknown -- which is how the statistics roll out lazily
        # rather than through a migration.
        data = json.loads(content)
        result = CoverageStatistics()
        for chrom, counts in data["chromosomes"].items():
            result.fold_region(RegionCoverage.frozen(
                chrom, int(counts["covered_positions"]),
                _read_stored_summary(counts, "segment")))
        return result


class CoverageRow(NamedTuple):
    """One chromosome's rendered coverage: raw counts, share derived.

    ``length`` is the denominator resolved for this chromosome, or
    ``None`` when none was -- the row then renders its raw count only.
    ``segments`` is ``None`` when the stored statistic carries no
    segment data for the resource (an old file, or a kind that
    publishes none).

    The share is held as the two INTEGERS it is a share of rather than
    as either rendering of it, because the page needs both and they
    must not disagree: the cell sorts on :attr:`fraction` and displays
    :attr:`percent`, and the boundaries :attr:`percent` respects --
    covered none of it, covered all of it -- are decided on the counts
    (gain#1057).
    """

    chrom: str
    covered: int
    length: int | None
    segments: int | None

    @property
    def fraction(self) -> float | None:
        """The share as a number, for the cell's sort key."""
        if not self.length:
            return None
        return self.covered / self.length

    @property
    def percent(self) -> str | None:
        """The share as the page writes it, ``None`` without a length."""
        if not self.length:
            return None
        return percentage_of(self.covered, self.length)


class UncoveredContigs(NamedTuple):
    """The contigs of the reference that carry no values at all.

    One roll-up rather than a row each: a reference genome routinely
    carries hundreds of contigs a score never touches (alts, decoys, an
    unplaced scaffold), and per-contig zero rows would bury the contigs
    that do have values.  The count and the base pairs are what the
    global fraction is measured against but has nothing to show for.

    Membership is **zero covered positions**, not absence from the
    stored statistic.  The two differ by backend and by nothing else --
    a bigWig scan visits every header contig and stores a 0 for the
    empty ones, a tabix scan visits only contigs the index lists -- so
    rolling up by absence would render the same data two ways.
    """

    contigs: int
    length: int

    @property
    def percent(self) -> str:
        """The share of these contigs that is covered: none of it.

        Written through the same rule as every other cell in the column
        rather than as a formatted constant, so the row cannot drift
        from its neighbours (gain#1057).  Membership is zero covered
        positions, so this is the rule's exact zero by construction.

        Unguarded, and returning ``str`` rather than ``str | None``,
        where :attr:`CoverageRow.percent` and
        :attr:`CoverageDisplay.global_percent` both check their
        denominator first: those hold an ``int | None``, because a
        denominator is what may fail to resolve, while a roll-up exists
        only where one DID -- and :func:`_plausible_lengths` has
        already dropped every contig that could contribute a zero to
        :attr:`length`.
        """
        return percentage_of(0, self.length)


class CoverageDisplay(NamedTuple):
    """The Coverage section's render payload, shares resolved.

    Raw counts come from the stored statistic; shares are computed at
    render time and never stored.  ``global_length`` is the denominator
    the section answers *what part of the reference genome has values*
    against: the WHOLE resolved reference, including contigs the score
    never touched (gain#1041).  It is ``None`` unless every covered
    chromosome resolved a length -- a covered contig the reference does
    not list is proof the reference is the wrong one, and a global
    percent over a partial denominator would be misleading.
    """

    rows: list[CoverageRow]
    global_length: int | None
    uncovered: UncoveredContigs | None
    """The untouched part of the reference, or ``None`` when unknowable.

    ``None`` -- rather than a zero roll-up -- whenever
    ``global_length`` is: "these contigs have no values" is a claim
    about the resolved reference being the right one, and it is not made
    under a denominator already known to be wrong.
    """

    segment_lengths: list[int] | None
    """The global segment-length histogram, or ``None`` if unknown.

    The section's image is drawn from these counts, so the page decides
    whether to show it from the same counts the plotter refuses to draw
    -- a proxy such as the segment total could disagree with what was
    actually written.
    """

    @property
    def global_covered(self) -> int:
        return sum(row.covered for row in self.rows)

    @property
    def global_fraction(self) -> float | None:
        """The whole score's share of the reference, as a number."""
        if not self.global_length:
            return None
        return self.global_covered / self.global_length

    @property
    def global_percent(self) -> str | None:
        """The whole score's share, as the page writes it.

        The same rule the rows are written through, over the same two
        integers: a reference all but entirely covered reads
        ``>99.99%`` here exactly as one of its chromosomes does above
        (gain#1057).
        """
        if not self.global_length:
            return None
        return percentage_of(self.global_covered, self.global_length)

    @property
    def has_fractions(self) -> bool:
        """Whether the section renders a ``Covered %`` column at all.

        The summary rows carry percentages too, so this cannot be read
        off ``rows`` alone: a score with no values ANYWHERE has every
        contig rolled up and no rows left, and a resolved global
        fraction of 0.0 would be computed and then dropped for want of
        a column to print it in.
        """
        return (
            self.global_fraction is not None
            or any(row.fraction is not None for row in self.rows)
        )

    @property
    def global_segments(self) -> int | None:
        """The segment total, or ``None`` when any row lacks segments.

        All-or-nothing like the stored statistic: a global over a
        partial set would silently understate.
        """
        counts = [
            row.segments for row in self.rows
            if row.segments is not None
        ]
        if not counts or len(counts) != len(self.rows):
            return None
        return sum(counts)

    @property
    def has_segments(self) -> bool:
        return self.global_segments is not None


def resolve_chrom_lengths(
    resource: GenomicResource,
    score: GenomicScore,
    ref_genome: ReferenceGenome | None,
    chroms: Iterable[str],
) -> dict[str, int]:
    """Resolve chromosome lengths for the render-time denominator.

    The ladder: the ``reference_genome`` the caller resolved from the
    resource's label, falling back to the bigWig header's chromosome
    sizes for a bigWig-backed score, or raw counts (an empty mapping)
    when nothing resolves.

    What comes back is the **whole universe** the fraction is measured
    against, not only the contigs the score touched (gain#1041): every
    contig of the resolved genome, or every contig the table rung
    lists.  ``chroms`` -- the covered contigs -- is still passed in so
    that a covered contig the resolved source does NOT list is visible
    to the caller by its absence, which is what degrades the fraction.

    The two rungs are not interchangeable: only the genome rung answers
    "what part of the reference genome has values", while the table rung
    answers "what part of what this file declares".  ``docs/adr/0020``
    carries the worked example and is the record to amend if this
    changes; the user-facing half is in ``docs/source/grr.rst``.

    The genome rung is resolved by the CALLER: it needs a repository,
    which only exists during a page build, and the cache it goes
    through is shared with the scan's own contig splitting.
    """
    if ref_genome is not None:
        return dict(ref_genome.get_all_chrom_lengths())
    if score.table.chrom_lengths_are_exact:
        return _table_exact_lengths(resource, score, chroms)
    logger.info(
        "no coverage denominator resolvable for %s; "
        "rendering raw counts only", resource.resource_id)
    return {}


def _table_exact_lengths(
    resource: GenomicResource,
    score: GenomicScore,
    chroms: Iterable[str],
) -> dict[str, int]:
    """Contig lengths from a backend that declares them exact.

    Only consulted when the table's ``chrom_lengths_are_exact``
    capability holds (the bigWig header; mapping-aware).  Opens the
    score if it is closed, and closes it again only in that case --
    an already-open score stays open for its owner.

    The universe is the table's WHOLE contig list, which this backend
    serves cleanly -- ``get_chromosomes()`` off an open table, already
    in reference space -- rather than only the contigs the score
    touched (gain#1041, ADR 0020).  The covered contigs are appended so
    that one the table does not list still reaches the unknown-contig
    warning below instead of vanishing silently.

    "The table's" and not "the header's": under a ``chrom_mapping``
    FILE, ``get_chromosomes()`` is the mapping's contigs, so a mapping
    naming a subset of the header shrinks the universe to that subset.
    That is the mapping doing its job -- a contig the resource declines
    to map is not part of what the resource claims to cover -- but it
    does mean this rung's denominator is the resource's declared
    universe, not the file's.
    """
    opened_here = not score.is_open()
    if opened_here:
        score.open()
    try:
        universe = dict.fromkeys(
            [*score.table.get_chromosomes(), *chroms])
        lengths: dict[str, int] = {}
        for chrom in universe:
            try:
                length = score.table.find_chromosome_length(chrom)
            except ValueError:
                # The backend raises ValueError both for a contig it does
                # not list and for a closed table; the open() above rules
                # the latter out, so this is the unknown-contig case.
                #
                # A WARNING unconditionally, unlike the implausible-length
                # drop in ``_plausible_lengths``, which follows
                # coveredness.  Every contig of the universe above came
                # from ``get_chromosomes()`` and must resolve a length, so
                # reaching here means either a covered contig the table
                # does not list or a chrom_mapping naming a contig the
                # file lacks -- both worth saying out loud.
                logger.warning(
                    "contig %s has no exact table length in %s; "
                    "rendering raw counts for it",
                    chrom, resource.resource_id)
                continue
            if isinstance(length, int):
                lengths[chrom] = length
        return lengths
    finally:
        if opened_here:
            score.close()


def build_coverage_display(
    resource_id: str,
    statistics: CoverageStatistics,
    lengths: dict[str, int],
) -> CoverageDisplay:
    """Turn stored counts plus a resolved denominator into the payload.

    What this resolves is each share's DENOMINATOR; the shares
    themselves are derived on the payload, from that denominator and
    the count it bounds (see :class:`CoverageRow`).  Either way nothing
    is stored: the statistic stays raw counts (see
    :class:`CoverageStatistics`).  A denominator that cannot bound what
    it must is withheld, degrading that row to a raw count rather than
    rendering a zero-division or a >100%.

    ``lengths`` is the whole reference the score is measured against,
    not merely the contigs it touched, so the global share answers
    *what part of the reference genome has values* and the untouched
    remainder is reported as one roll-up (gain#1041).
    """
    covered = statistics.covered_by_chromosome()
    lengths = _plausible_lengths(resource_id, covered, lengths)
    resolved = bool(lengths) and covered.keys() <= lengths.keys()
    global_length = sum(lengths.values()) if resolved else None
    untouched = {
        chrom: length for chrom, length in lengths.items()
        if resolved and not covered.get(chrom)
    }
    uncovered = (
        UncoveredContigs(len(untouched), sum(untouched.values()))
        if untouched else None
    )
    segments = statistics.segments_by_chromosome()
    rows = [
        CoverageRow(
            chrom,
            covered[chrom],
            lengths.get(chrom),
            segments.get(chrom),
        )
        # Filtered BEFORE the sort, not in the comprehension after it: the
        # key is a regex substitution, and a whole reference genome's worth
        # of untouched contigs would each pay for one only to be dropped.
        for chrom in sorted(
            (chrom for chrom in covered if chrom not in untouched),
            key=natural_chromosome_key)
    ]
    return CoverageDisplay(
        rows, global_length, uncovered,
        statistics.segment_lengths_global())


def _plausible_lengths(
    resource_id: str,
    covered: dict[str, int],
    lengths: dict[str, int],
) -> dict[str, int]:
    """``lengths`` without the entries proven wrong for their contig.

    A length that cannot bound what it must -- a zero-length ``.fai``
    record, a contig the genome claims is shorter than the positions
    the score holds on it -- is dropped rather than rendered as a
    zero-division or a >100%.  Dropping a COVERED contig also degrades
    the global fraction, since the caller's all-covered-contigs-resolve
    test then fails; dropping an untouched one merely shrinks the
    universe, which is right: it contributes no reference either.

    Which of the two decides the LOG LEVEL, because this runs on every
    page render: a covered contig whose length is wrong changes what
    the page shows and is a warning, as it always was, while an
    untouched one changes nothing visible and would otherwise warn once
    per zero-length ``.fai`` record per render.
    """
    kept: dict[str, int] = {}
    for chrom, length in lengths.items():
        is_covered = chrom in covered
        covered_here = covered[chrom] if is_covered else 0
        if length <= 0 or covered_here > length:
            if is_covered:
                logger.warning(
                    "implausible length %s for contig %s of %s "
                    "(covered positions: %s); rendering raw counts for it",
                    length, chrom, resource_id, covered_here)
            else:
                logger.debug(
                    "implausible length %s for untouched contig %s of %s; "
                    "leaving it out of the coverage denominator",
                    length, chrom, resource_id)
            continue
        kept[chrom] = length
    return kept


def accumulate_coverage(
    arrays: RecordArrays,
    coverage: RegionCoverage,
    region: tuple[str, int | None, int | None],
) -> None:
    """Fold one batch of column arrays into the region's coverage.

    Coverage partitions POSITIONS, not records: a union is only
    additive across parallel regions when the spans are clipped to
    disjoint extents.  Two fragments 8-14 and 12-18 over regions
    [1-10] and [11-20] cover 11 positions between them; measured whole
    they would report 7 + 7, and :meth:`RegionCoverage.merge` holds
    only counts, so nothing downstream can repair it.

    So a kind whose rows can overlap is clipped on both edges -- a
    record beginning past the region's end covers nothing, the gain#636
    verdict.  A kind whose rows cannot (see
    :attr:`RegionCoverage.rows_are_disjoint`) is spared the clip
    entirely and rides
    :func:`~gain.genomic_resources.genomic_scores.records.owned_records_mask`,
    the record partition every other statistic reads: disjoint spans
    cannot double-count, so the union is exact at full span, and the
    segment runs the same feed builds are measured at their true length
    rather than the region's.

    Either way the spans reach :meth:`RegionCoverage.add_interval_batch`,
    which owns the run-collapse algebra; nothing here knows what "equal
    values" means.  The batches the backends return rarely carry a row
    outside the queried region, so the all-kept batch skips the mask
    copies entirely.
    """
    _chrom, start, end = region
    pos_begin, pos_end, value_cells = arrays
    if coverage.rows_are_disjoint:
        keep = owned_records_mask(pos_begin, start, end)
    else:
        keep = np.ones(pos_begin.shape[0], dtype=bool)
        if start is not None:
            keep &= pos_end >= start
        if end is not None:
            keep &= pos_begin <= end
    if not keep.any():
        return
    if keep.all():
        left, right = pos_begin, pos_end
        cells = list(value_cells.values())
    else:
        left, right = pos_begin[keep], pos_end[keep]
        cells = [column[keep] for column in value_cells.values()]
    if not coverage.rows_are_disjoint:
        if start is not None:
            left = np.maximum(left, start)
        if end is not None:
            right = np.minimum(right, end)
    coverage.add_interval_batch(left, right, cells)


def merge_region_coverage(
    resource_id: str,
    regions: Iterable[RegionCoverage | None],
) -> CoverageStatistics | None:
    """Fold the regions' coverage, or ``None`` for an uncovered kind."""
    ordered = regions_in_genomic_order(regions)
    if not ordered:
        return None
    statistics = CoverageStatistics()
    try:
        for region in ordered:
            statistics.fold_region(region)
    except ValueError as err:
        report_resource_failure(
            err, "could not merge the coverage of", resource_id)
        raise
    return statistics


def save_and_plot_coverage(
    resource: GenomicResource,
    statistics: CoverageStatistics | None,
) -> None:
    """Write the coverage statistics and their histogram images.

    Does nothing for a kind that has no coverage, and skips a group's
    image when there is nothing to draw -- whether the group is unknown
    or known and empty.  The same rule as ``save_and_plot_alleles``.
    """
    if statistics is None:
        return
    with resource.open_raw_file(
            COVERAGE_STATISTICS_FILE, mode="wt") as outfile:
        outfile.write(statistics.serialize())
    # A group the resource publishes nothing for writes no image; the
    # info page's section is what says so.
    lengths = statistics.segment_lengths_global()
    if not has_counts_to_plot(lengths):
        return
    with resource.open_raw_file(
            COVERAGE_SEGMENT_LENGTHS_IMAGE_FILE, mode="wb") as outfile:
        plot_length_histogram(outfile, lengths, "segment")


# How a region answers for each optional group the statistics file
# stores, keyed by the JSON key prefix.  One table, read by the
# serializer, the reader and the all-or-nothing roll-up alike, so a
# group cannot be written in a shape nothing reads back.
_SummaryOf = Callable[[RegionCoverage], "tuple[int, list[int]] | None"]

_STORED_SUMMARIES: tuple[tuple[str, _SummaryOf], ...] = (
    ("segment", RegionCoverage.segment_summary),
)


def _read_stored_summary(
    entry: dict[str, Any], name: str,
) -> tuple[int, list[int]] | None:
    """One group's count and histogram out of a chromosome entry."""
    if f"{name}_count" not in entry:
        return None
    histogram = [int(count) for count in entry[f"{name}_length_histogram"]]
    # A histogram of any other length was binned on foreign edges; it
    # cannot merge with this code's fixed bins, so it reads as unknown.
    if len(histogram) != LENGTH_HISTOGRAM_BIN_COUNT:
        return None
    return (int(entry[f"{name}_count"]), histogram)
