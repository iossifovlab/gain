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
feeds either is in ``implementations/genomic_scores_impl.py``.
"""
from __future__ import annotations

import json
import math
from collections.abc import Iterable
from typing import IO, Any, NamedTuple

import numpy as np

from gain.genomic_resources.cli_errors import report_resource_failure
from gain.genomic_resources.genomic_scores import (
    RecordArrays,
    owned_records_mask,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.statistics.base_statistic import (
    Statistic,
    refuse_unmergeable,
    regions_in_genomic_order,
)

COVERAGE_STATISTICS_FILE = "statistics/coverage.json"

#: How a failed fold of these regions is named in the message.
_MERGE_FAILURE = "coverage"
COVERAGE_SEGMENT_LENGTHS_IMAGE_FILE = \
    "statistics/coverage_segment_lengths.png"

# Length histograms (segments here; fragments and indels reuse the same
# contract, ADR 0020) use fixed log2 bins: bin ``i`` holds lengths in
# ``[2**i, 2**(i + 1))``, and the last bin is open-ended.  The edges are
# part of the stored format -- histograms binned on different edges
# cannot be merged -- so this constant must not change once resources
# carry statistics built from it.
LENGTH_HISTOGRAM_BIN_COUNT = 32


def length_histogram_bin_index(length: int) -> int:
    """The fixed log2 bin a length of that many base pairs falls in."""
    if length < 1:
        raise ValueError(f"length must be positive: {length}")
    return min(length.bit_length() - 1, LENGTH_HISTOGRAM_BIN_COUNT - 1)


def _bin_edge_label(edge: int) -> str:
    for unit, factor in (("G", 2 ** 30), ("M", 2 ** 20), ("K", 2 ** 10)):
        if edge >= factor:
            return f"{edge // factor}{unit}"
    return str(edge)


def plot_segment_length_histogram(
    outfile: IO,
    histogram: list[int],
) -> None:
    """Render a segment-length histogram on the fixed log2 bins as PNG.

    Styled to sit beside the per-score value histograms on the resource
    info page: same figure size and label font as
    :mod:`gain.genomic_resources.histogram` renders.
    """
    # pylint: disable=import-outside-toplevel
    import matplotlib
    matplotlib.use("agg")
    import matplotlib.pyplot as plt

    from gain.genomic_resources.histogram import (
        HISTOGRAM_LABELS_FONT_SIZE,
    )

    figure, axes = plt.subplots(figsize=(15, 10))
    axes.bar(
        range(len(histogram)), histogram, width=0.9, align="edge")
    # Ticks at every fourth bin's lower edge; the last bin is
    # open-ended, so its lower edge is labeled as a floor.
    last = len(histogram) - 1
    ticks = [*range(0, last, 4), last]
    labels = [_bin_edge_label(2 ** tick) for tick in ticks[:-1]]
    labels.append(f"≥{_bin_edge_label(2 ** last)}")
    axes.set_xticks(ticks)
    axes.set_xticklabels(labels, fontsize=HISTOGRAM_LABELS_FONT_SIZE)
    axes.tick_params(axis="y", labelsize=HISTOGRAM_LABELS_FONT_SIZE)
    axes.set_xlabel(
        "segment length (bp)", fontsize=HISTOGRAM_LABELS_FONT_SIZE)
    axes.set_ylabel("segments", fontsize=HISTOGRAM_LABELS_FONT_SIZE)
    # Counts span orders of magnitude on genome-scale scores; symlog
    # keeps the small bars visible while zero stays on the axis.
    axes.set_yscale("symlog")
    figure.tight_layout()
    figure.savefig(outfile, format="png")
    plt.close(figure)


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
        # Rows that can neither overlap nor touch (a position score,
        # whose validators refuse both) have an exact run algebra, so
        # their segment summary is published; and they are pairwise
        # disjoint, so the scan may hand this their FULL spans and the
        # union stays additive across regions.  Rows that can overlap
        # (fragments) publish no segment summary, and must be handed
        # spans clipped to the region -- see
        # ``GenomicScoreImplementation._accumulate_coverage``.
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

        ``segments`` of ``None`` marks the segment data unknown -- the
        file predates it or carries foreign bins.  A frozen region never
        accumulates a span, so ``rows_are_disjoint`` has no clipping
        consequence here; it carries only the other one, gating the
        summary this region can answer with.
        """
        region = cls(
            chrom, None, None, rows_are_disjoint=segments is not None)
        region.covered = covered
        region._frozen_segments = segments
        return region

    @property
    def rows_are_disjoint(self) -> bool:
        """Whether the scanned kind's rows can neither overlap nor touch.

        Read by the scan to decide whether to clip the spans it hands
        :meth:`add_interval` -- see the constructor for the one fact and
        its two consequences.
        """
        return self._rows_are_disjoint

    def segment_summary(self) -> tuple[int, list[int]] | None:
        """Segment count and length histogram, or ``None`` if unknown.

        Unknown means the region does not track segments: rows without
        an exact run algebra, or a region deserialized from a
        statistics file that predates segment-length histograms.  The
        count and histogram accessors themselves are only meaningful
        through this gate.
        """
        if not self._rows_are_disjoint:
            return None
        return self.segment_count, self.segment_length_histogram()

    def segment_length_histogram(self) -> list[int]:
        """Counts of segment lengths on the fixed log2 bins.

        Finalizes the still-open bookkeeping: the first and the open run
        are folded in on top of the interior counts, so the histogram
        totals exactly ``segment_count``.
        """
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
        """
        if not self._closed_segments:
            self._first_run = run
        elif self._rows_are_disjoint:
            self._add_to(self._interior_bins, run)

    @property
    def segment_count(self) -> int:
        if self._frozen_segments is not None:
            return self._frozen_segments[0]
        return self._closed_segments + (1 if self._run is not None else 0)

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
        if stitch and not other._closed_segments:
            # The other region is one run end to end; the combined run
            # stays open for the next merge.
            self._run = (last_begin, other._run[1], last_values)
            return
        for index, count in enumerate(other._interior_bins):
            self._interior_bins[index] += count
        if stitch:
            self._record_closed((last_begin, first_end, last_values))
            self._closed_segments += other._closed_segments
        else:
            self._record_closed(self._run)
            if other._closed_segments and self._rows_are_disjoint:
                # The other region's first run closed there without
                # being binned -- it could still have stitched.  It did
                # not, so it is interior of the merged region now.
                self._add_to(self._interior_bins, other_first)
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
            self._record_closed(self._run)
            self._closed_segments += 1
        self._run = (begin, end, values)

    def add_interval_batch(
        self,
        left: np.ndarray,
        right: np.ndarray,
        cells: list[np.ndarray],
    ) -> None:
        """Fold a batch of clipped row spans, collapsed into runs.

        The vectorized statement of the rule :meth:`add_interval`
        applies row by row — it lives HERE, beside that rule, so the
        equality algebra has one home: rows collapse into a run while
        they touch or overlap the positions covered so far and every
        column compares equal, nan equal to nan (ADR 0020), and each
        run costs one :meth:`add_interval` rather than one per row.

        The touching test reads the running maximum end, which is exact
        for a position score (whose validators refuse overlap, so the
        previous row IS the running maximum).  For overlapping fragment
        rows the covered count is still exact — :meth:`add_interval`
        unions whatever run shapes arrive — while run identity may
        differ from the row-by-row feed and between chunked and
        unchunked scans where differently-valued fragments interleave;
        fragment segment statistics are not published (value-aware
        segments are a position-score statistic), and a consumer that
        wants them must first give fragments an exact run algebra.

        ``left``/``right`` are the already-clipped spans, ``cells`` one
        kept column per scanned score, all equally long.
        """
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

    def _summaries(self) -> dict[str, tuple[int, list[int]]] | None:
        """Per-chromosome segment summaries, or ``None`` if any chromosome
        lacks them -- a partial global would silently understate."""
        summaries = {}
        for chrom, region in self._regions.items():
            summary = region.segment_summary()
            if summary is None:
                return None
            summaries[chrom] = summary
        return summaries

    def segments_by_chromosome(self) -> dict[str, int]:
        summaries = self._summaries()
        if summaries is None:
            return {}
        return {
            chrom: count for chrom, (count, _) in summaries.items()
        }

    def segments_global(self) -> int | None:
        summaries = self._summaries()
        if summaries is None:
            return None
        return sum(count for count, _ in summaries.values())

    def segment_lengths_by_chromosome(self) -> dict[str, list[int]]:
        """Per-chromosome length histograms -- the read API for the
        per-chromosome data the statistics file stores (rendered
        consumers use the global roll-up; gain#776 reads these)."""
        summaries = self._summaries()
        if summaries is None:
            return {}
        return {
            chrom: histogram
            for chrom, (_, histogram) in summaries.items()
        }

    @staticmethod
    def _binwise_sum(histograms: Iterable[list[int]]) -> list[int]:
        merged = [0] * LENGTH_HISTOGRAM_BIN_COUNT
        for histogram in histograms:
            for index, count in enumerate(histogram):
                merged[index] += count
        return merged

    def segment_lengths_global(self) -> list[int] | None:
        """The bin-wise sum of the per-chromosome length histograms."""
        summaries = self._summaries()
        if summaries is None:
            return None
        return self._binwise_sum(
            histogram for _, histogram in summaries.values())

    def add_value(self, value: Any) -> None:  # noqa: ARG002
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
        for region in other._regions.values():  # noqa: SLF001
            self.fold_region(region)

    def serialize(self) -> str:
        # One walk of the regions serves the per-chromosome entries and
        # the global roll-up; the global segment keys are written only
        # when EVERY chromosome has a summary (a partial global would
        # silently understate).
        chromosomes: dict[str, dict[str, Any]] = {}
        summaries: dict[str, tuple[int, list[int]]] | None = {}
        for chrom, region in self._regions.items():
            entry: dict[str, Any] = {
                "covered_positions": region.covered,
            }
            summary = region.segment_summary()
            if summary is None:
                summaries = None
            else:
                entry["segment_count"] = summary[0]
                entry["segment_length_histogram"] = summary[1]
                if summaries is not None:
                    summaries[chrom] = summary
            chromosomes[chrom] = entry
        global_entry: dict[str, Any] = {
            "covered_positions": self.covered_global(),
        }
        if summaries is not None:
            global_entry["segment_count"] = sum(
                count for count, _ in summaries.values())
            global_entry["segment_length_histogram"] = self._binwise_sum(
                histogram for _, histogram in summaries.values())
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
        # and a file written before segment histograms existed reads
        # with its segment data unknown.
        data = json.loads(content)
        result = CoverageStatistics()
        for chrom, counts in data["chromosomes"].items():
            segments = None
            if "segment_count" in counts:
                histogram = [
                    int(count) for count
                    in counts["segment_length_histogram"]]
                # A histogram of any other length was binned on foreign
                # edges; it cannot merge with this code's fixed bins,
                # so it reads as segments-unknown.
                if len(histogram) == LENGTH_HISTOGRAM_BIN_COUNT:
                    segments = (int(counts["segment_count"]), histogram)
            result.fold_region(RegionCoverage.frozen(
                chrom, int(counts["covered_positions"]), segments))
        return result


class CoverageRow(NamedTuple):
    """One chromosome's rendered coverage: raw count plus optional fraction.

    ``fraction`` is ``None`` when no denominator resolved for this
    chromosome -- the row renders its raw count only.  ``segments`` is
    ``None`` when the stored statistic carries no segment data for the
    resource (an old file, or a kind that publishes none).
    """

    chrom: str
    covered: int
    fraction: float | None
    segments: int | None


class CoverageDisplay(NamedTuple):
    """The Coverage section's render payload, fractions resolved.

    Raw counts come from the stored statistic; fractions are computed at
    render time and never stored.  ``global_fraction`` is ``None`` unless
    every chromosome resolved a length -- a global percent over a partial
    denominator would be misleading.
    """

    rows: list[CoverageRow]
    global_fraction: float | None

    @property
    def global_covered(self) -> int:
        return sum(row.covered for row in self.rows)

    @property
    def has_fractions(self) -> bool:
        return any(row.fraction is not None for row in self.rows)

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
    :func:`~gain.genomic_resources.genomic_scores.owned_records_mask`,
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
    """Write the coverage statistics and their histogram image.

    Does nothing for a kind that has no coverage, and skips the image
    for one whose segment data is unknown.
    """
    if statistics is None:
        return
    with resource.open_raw_file(
            COVERAGE_STATISTICS_FILE, mode="wt") as outfile:
        outfile.write(statistics.serialize())
    lengths = statistics.segment_lengths_global()
    if lengths is None:
        return
    with resource.open_raw_file(
            COVERAGE_SEGMENT_LENGTHS_IMAGE_FILE, mode="wb") as outfile:
        plot_segment_length_histogram(outfile, lengths)
