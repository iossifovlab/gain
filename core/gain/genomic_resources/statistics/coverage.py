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
from collections.abc import Callable, Iterable, Mapping
from typing import Any, NamedTuple

import numpy as np

from gain import logging
from gain.genomic_resources.genomic_scores import (
    GenomicScore,
    PositionScore,
    RecordArrays,
    owned_records_mask,
)
from gain.genomic_resources.genomic_scores.chrom_lengths import ChromLength
from gain.genomic_resources.reference_genome import ReferenceGenome
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.statistics.base_statistic import (
    RegionFoldedStatistic,
    refuse_unmergeable,
)
from gain.genomic_resources.statistics.exact_lengths import (
    NO_LENGTHS,
    ExactLengths,
    LengthArrayTally,
    LengthStatisticsRow,
    length_ladder,
    merged_lengths,
)
from gain.genomic_resources.statistics.length_histogram import (
    plot_length_histogram,
)
from gain.genomic_resources.statistics.percentages import percentage_of
from gain.genomic_resources.statistics.region_fold import merge_regions
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

    Consumes ``[begin, end]`` spans in non-decreasing ``begin`` order --
    the order the scan validators guarantee -- and counts each position
    once.  The rows it is fed are pairwise disjoint: since gain#1127
    the only coverage-scanned kinds are position scores, whose
    validators refuse a row beginning at or before its predecessor's
    end (adjacent rows are legal and common, and the segment algebra
    depends on that).  So the scan hands over each row at its FULL
    extent, unclipped -- disjoint spans cannot double-count a position,
    the union stays additive across parallel regions, and the segment
    runs are measured at their true length rather than the region's
    (gain#1175 retired the clip that overlapping rows once needed).
    """

    def __init__(
        self,
        chrom: str,
        start: int | None,
        end: int | None,
        *,
        publishes_segments: bool = True,
    ) -> None:
        self.chrom = chrom
        self.start = start
        self.end = end
        # Whether this region has segment numbers to answer with.  A
        # scanned region always does -- disjoint rows have an exact run
        # algebra.  Only :meth:`frozen` sets this False, for a region
        # restored from a statistics file that carried no segment data,
        # and such a region never accumulates a span:
        # :meth:`add_interval` refuses one.
        self._publishes_segments = publishes_segments
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
        # except the first -- tallied exactly.  The first and the open
        # run are excluded because either may still stitch across a
        # merge boundary; their lengths are only final at read time.
        self._interior = LengthArrayTally()
        # A deserialized region's segment data, frozen as read; it
        # carries no scan state.  The count is always known here; the
        # lengths are ``None`` for a file that carried none.
        self._frozen_segments: tuple[int, ExactLengths | None] | None = None

    @classmethod
    def frozen(
        cls,
        chrom: str,
        covered: int,
        segments: tuple[int, ExactLengths | None] | None,
    ) -> RegionCoverage:
        """A region restored from serialized counts, with no scan state.

        ``segments`` of ``None`` marks the segments wholly unknown -- the
        file predates them -- and is the one way a region comes to
        publish no segments.  A count with ``None`` for its lengths is
        the file that stored the count and the ladder but no exact
        record (format version 1): the count reads, the lengths do not.
        """
        region = cls(
            chrom, None, None,
            publishes_segments=segments is not None)
        region.covered = covered
        region._frozen_segments = segments
        return region

    @property
    def publishes_segments(self) -> bool:
        """Whether this region has segment numbers to answer with.

        The one predicate behind both :meth:`segment_summary`'s
        ``None`` and the accessors' refusal, so the two gates cannot
        drift apart.  False only for a region :meth:`frozen` from a
        statistics file that carried no segment data.
        """
        return self._publishes_segments

    def segment_summary(self) -> tuple[int, ExactLengths | None] | None:
        """Segment count and exact lengths, or ``None`` if unknown.

        Unknown means the region was deserialized from a statistics
        file that predates segments altogether.  This is the ASKING
        form of the gate the count and lengths accessors refuse through
        -- ``None`` here, an exception there, because a caller that
        asks may not know and one that reaches straight for a number
        has asserted it does.  The lengths inside are ``None`` on their
        own when the file carried the count but no exact record.
        """
        if not self._publishes_segments:
            return None
        return self.segment_count, self.segment_lengths()

    def segment_lengths(self) -> ExactLengths | None:
        """The exact record of the region's segment lengths.

        Finalizes the still-open bookkeeping: the first and the open run
        are folded in on top of the interior tally, so the record's
        ``total`` is exactly ``segment_count``.  ``None`` for a region
        read from a file that stored the count without the record.
        Refuses a region that publishes no segments at all -- see
        :meth:`_refuse_without_segments`.
        """
        self._refuse_without_segments("answer segment lengths")
        if self._frozen_segments is not None:
            return self._frozen_segments[1]
        lengths = LengthArrayTally()
        lengths.merge(self._interior)
        if self._closed_segments:
            first = self._first_run
            assert first is not None
            self._add_to(lengths, first)
        if self._run is not None:
            self._add_to(lengths, self._run)
        return lengths.frozen()

    @staticmethod
    def _add_to(
        lengths: LengthArrayTally,
        run: tuple[int, int, tuple],
    ) -> None:
        begin, end, _ = run
        lengths.add(end - begin + 1)

    def _record_closed(self, run: tuple[int, int, tuple]) -> None:
        """A run closed: freeze the first, tally the interior ones.

        The caller still advances ``_closed_segments`` itself -- a
        stitched merge records the combined run here but counts it
        through the other region's tally.
        """
        if not self._closed_segments:
            self._first_run = run
        else:
            self._add_to(self._interior, run)

    @property
    def segment_count(self) -> int:
        """How many segments the region holds.

        Refuses a region that publishes none -- see
        :meth:`_refuse_without_segments`.
        """
        self._refuse_without_segments("answer a segment count")
        if self._frozen_segments is not None:
            return self._frozen_segments[0]
        return self._closed_segments + (1 if self._run is not None else 0)

    def _refuse_without_segments(self, doing: str) -> None:
        """Refuse ``doing`` on a region that publishes no segments.

        The one gate behind both segment accessors and both span feeds,
        so they cannot drift apart.  A region deserialized from a file
        that carried no segment data holds zero segments of zero
        length, and that number is a lie: zero reads as
        scanned-and-empty rather than never-scanned, and only
        :meth:`segment_summary`'s ``None`` tells those apart (gain#1043
        was filed for a count that escaped this way).  Nor may such a
        region accumulate: it holds counts, not scan state, and a span
        reaching it is a wiring error (gain#1175).
        """
        if not self._publishes_segments:
            raise ValueError(
                f"region {self.chrom} publishes no segment statistics: "
                "it was read from a statistics file carrying none, "
                f"and cannot {doing}")

    def _first(self) -> tuple[int, int, tuple] | None:
        """The leftmost run -- frozen if closed, the open run otherwise."""
        if self._closed_segments:
            return self._first_run
        return self._run

    def merge(self, other: RegionCoverage) -> None:
        """Fold the adjacent region to the right into this one.

        Refuses a pair that is not adjacent-and-in-order on one
        chromosome -- see ``refuse_unmergeable``, which states that rule
        for this statistic and its two twins alike.
        """
        refuse_unmergeable(_MERGE_FAILURE, self, other)

        self.covered += other.covered
        self._publishes_segments = \
            self._publishes_segments and other._publishes_segments
        if other._run is None:
            self.end = other.end
            return
        if self._run is None:
            self._closed_segments = other._closed_segments
            self._first_run = other._first_run
            self._run = other._run
            self._interior = LengthArrayTally()
            self._interior.merge(other._interior)
        else:
            self._merge_runs(other)
        self._covered_through = other._covered_through
        self.end = other.end

    def _merge_runs(self, other: RegionCoverage) -> None:
        """Combine the run bookkeeping of two non-empty regions.

        The one stitch decision: this region's open run and the other's
        first run are one segment exactly when they touch or overlap and
        carry equal values -- the very test :meth:`add_interval` applies
        row by row, stated once more across a merge boundary.

        It is deliberately NOT "both runs abut the shared boundary".
        That was the same test in a world where every span arrived
        clipped to its region, which made abutting the boundary the only
        way two runs could touch.  A region is handed FULL spans (see
        the class docstring), so its runs reach past its own extent,
        and abutting would refuse to stitch a segment that plainly
        continues.
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
        self._interior.merge(other._interior)
        if stitch:
            self._record_closed(
                (last_begin, max(last_end, first_end), last_values))
            self._closed_segments += other._closed_segments
        else:
            self._record_closed(self._run)
            if other._closed_segments:
                # The other region's first run closed there without
                # being tallied -- it could still have stitched.  It did
                # not, so it is interior of the merged region now.
                self._add_to(self._interior, other_first)
            self._closed_segments += \
                1 + other._closed_segments
        self._run = other._run

    def add_interval(
        self,
        begin: int,
        end: int,
        values: tuple,
    ) -> None:
        """Fold one row span into the coverage and its run bookkeeping.

        The union first -- a running maximum over the right edge, so a
        row is counted once whatever it overlaps -- then the runs: the
        row joins the open run while it touches or overlaps it and
        carries equal values, and closes it otherwise.  Refuses a
        region that publishes no segments -- see
        :meth:`_refuse_without_segments`.
        """
        self._refuse_without_segments("accumulate a span")
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
        previous row IS the running maximum).

        ``left``/``right`` are the rows' own full extents (see the
        class docstring) and ``cells`` is one kept column per scanned
        score, all equally long.
        """
        self._refuse_without_segments("accumulate a span")
        count = left.shape[0]
        if not count:
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
        run_begins = left[starts]
        run_ends = np.maximum.reduceat(right, starts)
        runs = len(starts)
        # Only the batch's two end runs need their values: the first,
        # to decide whether it stitches onto the region's open run, and
        # the last, which becomes the open run for the next batch.
        # Gathered vectorized, then handed over as plain Python objects:
        # per-run numpy scalar indexing would put the object churn ADR
        # 0001 deleted back on the hot path.
        edges = starts[[0, -1]] if runs > 1 else starts[[0]]
        columns = []
        for column in cells:
            gathered = column[edges]
            if gathered.dtype == object:
                columns.append(gathered.tolist())
            else:
                columns.append([
                    None if is_nan else value
                    for value, is_nan in zip(
                        gathered.tolist(),
                        np.isnan(gathered).tolist(), strict=True)
                ])
        edge_values = list(zip(*columns, strict=True)) if columns \
            else [()] * len(edges)
        self.add_interval(
            int(run_begins[0]), int(run_ends[0]), edge_values[0])
        if runs > 1:
            self._close_through(run_begins[1:], run_ends[1:], edge_values[-1])

    def _close_through(
        self,
        begins: np.ndarray,
        ends: np.ndarray,
        last_values: tuple,
    ) -> None:
        """Fold a batch's remaining runs, the open one closing first.

        The runs follow one :meth:`add_interval` has just made the open
        run, and none of them touches its predecessor with equal values
        -- that is what made them separate runs -- so every one of them
        except the last closes INSIDE the batch, at exactly its own
        length.  Those lengths fold into the tally as one array
        (decision 4 of gain#1541): the cost of a batch is bounded by
        the clamp, not by the run count, where one tally call per run
        would put microseconds back on a path walked once per segment.
        The last run becomes the open run, values and all, for the
        next batch to stitch onto or not.

        The open run closes first, through :meth:`_record_closed`,
        because it may be the region's FIRST -- the one run whose
        length stays undecided until the region's left neighbour is
        known.  The union is folded here too, under the same running
        maximum :meth:`add_interval` keeps row by row.
        """
        assert self._run is not None
        self._record_closed(self._run)
        self._closed_segments += 1
        interior = len(begins) - 1
        if interior:
            self._interior.add_batch(ends[:-1] - begins[:-1] + 1)
            self._closed_segments += interior
        through = self._covered_through
        assert through is not None
        # What each run adds to the union is the part of it past the
        # rightmost position covered before it -- the running maximum
        # over the runs ahead of it in this batch, or the region's mark.
        reach = np.maximum.accumulate(ends)
        prior = np.concatenate(([through], np.maximum(reach[:-1], through)))
        gained = np.maximum(ends - np.maximum(begins - 1, prior), 0)
        self.covered += int(gained.sum())
        self._covered_through = max(through, int(reach[-1]))
        self._run = (int(begins[-1]), int(ends[-1]), last_values)


class CoverageStatistics(RegionFoldedStatistic[RegionCoverage]):
    """A resource's covered positions, per chromosome and global.

    Folds :class:`RegionCoverage` the way the base class does, and
    serializes to the resource's ``statistics/coverage.json`` as raw
    counts.  Fractions are deliberately not computed here: they need
    chromosome lengths, which belong to a reference genome resolved at
    render time.
    """

    def __init__(self) -> None:
        super().__init__(
            "coverage", "Covered positions per chromosome and global")

    def covered_by_chromosome(self) -> dict[str, int]:
        return {
            chrom: region.covered
            for chrom, region in self._regions.items()
        }

    def covered_global(self) -> int:
        return sum(region.covered for region in self._regions.values())

    def _segment_summaries(
        self,
    ) -> dict[str, tuple[int, ExactLengths | None]] | None:
        """Per-chromosome segment summaries, or ``None`` if any
        chromosome lacks them -- a partial global would silently
        understate.

        All-or-nothing, and that is the whole rule the four accessors
        below share.  It was once parameterised over a table of optional
        GROUPS, because fragments were a second one; they became a
        statistic of their own in gain#1127 and segments are the only
        group left, so the table and its ``summary_of`` callable went
        with them.
        """
        summaries = {}
        for chrom, region in self._regions.items():
            summary = region.segment_summary()
            if summary is None:
                return None
            summaries[chrom] = summary
        return summaries

    def segments_by_chromosome(self) -> dict[str, int]:
        summaries = self._segment_summaries()
        if summaries is None:
            return {}
        return {chrom: count for chrom, (count, _) in summaries.items()}

    def segments_global(self) -> int | None:
        summaries = self._segment_summaries()
        if summaries is None:
            return None
        return sum(count for count, _ in summaries.values())

    def _segment_lengths(self) -> dict[str, ExactLengths] | None:
        """Per-chromosome exact records, or ``None`` if any chromosome
        lacks them -- the second all-or-nothing gate, over the lengths
        alone.  A format version 1 file passes the first gate (every
        chromosome has its count) and fails this one."""
        summaries = self._segment_summaries()
        if summaries is None:
            return None
        records = {}
        for chrom, (_, lengths) in summaries.items():
            if lengths is None:
                return None
            records[chrom] = lengths
        return records

    def segment_lengths_by_chromosome(self) -> dict[str, ExactLengths]:
        """Per-chromosome exact length records -- the read API for the
        per-chromosome data the statistics file stores (rendered
        consumers use the global roll-up; gain#776 reads these)."""
        return self._segment_lengths() or {}

    def segment_lengths_global(self) -> ExactLengths | None:
        """The fold of the per-chromosome records, unknown if any is."""
        records = self._segment_lengths()
        if records is None:
            return None
        result: ExactLengths | None = NO_LENGTHS
        for lengths in records.values():
            result = merged_lengths(result, lengths)
        return result

    def serialize(self) -> str:
        # One walk of the regions serves the per-chromosome entries and
        # the global roll-up.  The segment keys are written per
        # chromosome wherever that chromosome has them, and globally only
        # when EVERY chromosome does -- a partial global would silently
        # understate.  Format version 2 (gain#1543) stores the exact
        # length record where version 1 stored the log2 ladder.
        chromosomes: dict[str, dict[str, Any]] = {}
        for chrom, region in self._regions.items():
            entry: dict[str, Any] = {
                "covered_positions": region.covered,
            }
            summary = region.segment_summary()
            if summary is not None:
                count, lengths = summary
                entry["segment_count"] = count
                if lengths is not None:
                    entry["segment_lengths"] = lengths.stored()
            chromosomes[chrom] = entry
        global_entry: dict[str, Any] = {
            "covered_positions": self.covered_global(),
        }
        global_segments = self.segments_global()
        if global_segments is not None:
            global_entry["segment_count"] = global_segments
        global_lengths = self.segment_lengths_global()
        if global_lengths is not None:
            global_entry["segment_lengths"] = global_lengths.stored()
        return json.dumps({
            "format_version": 2,
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
                _read_stored_summary(counts)))
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

    segment_lengths: ExactLengths | None
    """The global segment-length record, or ``None`` if unknown.

    The section's table is read off this record and its image is drawn
    from the ladder derived from it, so the page decides whether to
    show either from the same record the build wrote or declined to
    draw -- a proxy such as the segment total could disagree with what
    was actually written.
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

    @property
    def segment_row(self) -> LengthStatisticsRow | None:
        """The Segment lengths table's one row, ``None`` when unknown.

        Global only, as the indel rows are: per chromosome this would
        be four more columns on the Coverage table above, to answer a
        question nobody asks.
        """
        if self.segment_lengths is None:
            return None
        return LengthStatisticsRow.of("segments", self.segment_lengths)


def resolve_chrom_lengths(
    resource: GenomicResource,
    ref_genome: ReferenceGenome | None,
    score_lengths: Callable[[], Mapping[str, ChromLength]],
    chroms: Iterable[str],
) -> dict[str, int]:
    """Resolve chromosome lengths for the render-time denominator.

    The ladder: the ``reference_genome`` the caller resolved from the
    resource's label; else the score's own lengths, kept where their
    source is exact (a bigWig header's contig sizes); else raw counts
    (an empty mapping).

    What comes back is the **whole universe** the fraction is measured
    against, not only the contigs the score touched (gain#1041): every
    contig of the resolved genome, or every contig the score rung has
    an exact length for.  ``chroms`` -- the covered contigs -- is still
    passed in so that a covered contig the resolved source does NOT
    list is visible to the caller by its absence, which is what
    degrades the fraction.

    The two rungs are not interchangeable: only the genome rung answers
    "what part of the reference genome has values", while the score rung
    answers "what part of what this file declares".  ``docs/adr/0020``
    carries the worked example and is the record to amend if this
    changes; the user-facing half is in ``docs/source/grr.rst``.

    Both rungs are the CALLER's to supply: the genome needs a
    repository, which only exists during a page build, and the score's
    records come from the implementation's ladder
    (:meth:`GenomicScoreImplementation.get_chrom_lengths`), which
    opens the score to ask -- so they are asked for, through
    ``score_lengths``, only once the genome rung has nothing.  Which of
    a record's sources may serve as a denominator is
    ``ChromLengthSource.is_exact``'s call, not this function's
    (gain#1414): a tabix probe's upper bound and an in-memory table's
    extent are dropped here, and a contig with no length at all
    (proven empty, or one the probe could not bracket) with them.
    """
    if ref_genome is not None:
        return dict(ref_genome.get_all_chrom_lengths())
    lengths = {
        chrom: resolved.length
        for chrom, resolved in score_lengths().items()
        if resolved.length is not None
        and resolved.source is not None
        and resolved.source.is_exact
    }
    if not lengths:
        logger.info(
            "no coverage denominator resolvable for %s; "
            "rendering raw counts only", resource.resource_id)
        return {}
    for chrom in chroms:
        if chrom not in lengths:
            # Every contig this rung lists came from the score's own
            # contig list, so a COVERED contig missing from it is either
            # one the score does not list any more (a chrom_mapping
            # changed under the stored statistic) or one whose length is
            # not exact -- either way worth saying out loud, unlike the
            # implausible-length drop in ``_plausible_lengths``, which
            # follows coveredness quietly.
            logger.warning(
                "covered contig %s has no exact length in %s; "
                "rendering raw counts for it",
                chrom, resource.resource_id)
    return lengths


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


def region_coverage_for(
    score: GenomicScore,
    chrom: str,
    start: int | None,
    end: int | None,
) -> RegionCoverage | None:
    """A region accumulator for a position score, ``None`` for other kinds.

    Gated on the built score's class rather than on the resource type
    string, for the reason
    :func:`~gain.genomic_resources.statistics.alleles.region_alleles_for`
    gives.

    A position score is the one kind whose rows are pairwise disjoint --
    the rule it is registered under in :mod:`~.record_validation` refuses a
    row beginning at or before its predecessor's end -- and that is the
    property this statistic depends
    on: only then does the union of the spans answer "is there data at
    this position at all?" exactly, so that the count is a genuine
    measure of what the resource covers and the fraction a genuine
    completeness figure.  What disjointness buys the accumulator --
    full-span rows, an additive union, published segments -- is
    :class:`RegionCoverage`'s own docstring.

    An allele score's rows are points, so there is no span to union.  A
    fragment score's rows overlap by design;
    :mod:`~gain.genomic_resources.statistics.fragments` and ADR 0020 say
    why that keeps the kind out of both coverage and segments.
    """
    if not isinstance(score, PositionScore):
        return None
    return RegionCoverage(chrom, start, end)


def accumulate_coverage(
    arrays: RecordArrays,
    coverage: RegionCoverage,
    region: tuple[str, int | None, int | None],
) -> None:
    """Fold one batch of column arrays into the region's coverage.

    Rides
    :func:`~gain.genomic_resources.genomic_scores.records.owned_records_mask`,
    the record partition every other statistic reads: a region owns
    the rows whose ``pos_begin`` falls inside it and measures them
    whole.  That is exact for coverage because the rows are pairwise
    disjoint (see :class:`RegionCoverage`): they cannot double-count a
    position, so the union is additive across parallel regions at full
    span, and the segment runs the same feed builds are measured at
    their true length rather than the region's.  A record beginning
    past the region's end is not owned and covers nothing -- the
    gain#636 verdict, reached here by the partition rather than by a
    clip.

    The spans reach :meth:`RegionCoverage.add_interval_batch`, which
    owns the run-collapse algebra; nothing here knows what "equal
    values" means.  The batches the backends return rarely carry a row
    outside the queried region, so the all-kept batch skips the mask
    copies entirely.
    """
    _chrom, start, end = region
    pos_begin, pos_end, value_cells = arrays
    keep = owned_records_mask(pos_begin, start, end)
    if not keep.any():
        return
    if keep.all():
        left, right = pos_begin, pos_end
        cells = list(value_cells.values())
    else:
        left, right = pos_begin[keep], pos_end[keep]
        cells = [column[keep] for column in value_cells.values()]
    coverage.add_interval_batch(left, right, cells)


def merge_region_coverage(
    resource_id: str,
    regions: Iterable[RegionCoverage | None],
) -> CoverageStatistics | None:
    """Fold the regions' coverage, or ``None`` for an uncovered kind."""
    return merge_regions(
        resource_id, regions, CoverageStatistics, _MERGE_FAILURE)


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
    if lengths is None or not lengths.has_counts_to_plot:
        return
    with resource.open_raw_file(
            COVERAGE_SEGMENT_LENGTHS_IMAGE_FILE, mode="wb") as outfile:
        plot_length_histogram(outfile, length_ladder(lengths), "segment")


def _read_stored_summary(
    entry: dict[str, Any],
) -> tuple[int, ExactLengths | None] | None:
    """The segment count and exact lengths out of a chromosome entry.

    The count reads at any format version; the lengths only where the
    exact record is stored (version 2).  A version 1 file's ladder is
    deliberately NOT read -- one reader, not a compatibility branch,
    the rule ADR 0020's gain#1118 amendment states for the indel
    groups: a ladder can publish no exact sum, min or max, so every
    figure in the table would be a guess at bin resolution.
    """
    if "segment_count" not in entry:
        return None
    stored = entry.get("segment_lengths")
    lengths = None if stored is None else ExactLengths.from_stored(stored)
    return (int(entry["segment_count"]), lengths)
