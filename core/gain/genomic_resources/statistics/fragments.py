"""Fragment-count statistics for fragment scores.

Vocabulary per ``CONTEXT.md``: a **fragment** is a table row AS STORED.
Overlapping, nested and duplicate rows each count once, at their own
unclipped span — there is no run algebra and no stitching here, because
a row is owned whole by exactly one scanned region.

This statistic used to ride inside
:mod:`gain.genomic_resources.statistics.coverage`, as one more optional
group in the coverage file.  It does not any more (gain#1127): a
fragment score's rows deliberately overlap, so the union of their spans
measures nothing a reader wants, and the kind is no longer
coverage-scanned at all.  While the two shared a carrier, dropping the
union would have dropped the tally with it — which is why the tally
moved out first.

Laid out like its two twins, :mod:`.coverage` and :mod:`.alleles`: the
per-region accumulator, the resource-wide statistic, the fold that
merges a scan's regions into one, the write, and the render payload the
info page reads.  The scan wiring that feeds all three is in
``implementations/genomic_scores_impl/scan.py``.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any, NamedTuple

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
from gain.genomic_resources.statistics.length_histogram import (
    LENGTH_BIN_EDGES,
    LENGTH_HISTOGRAM_BIN_COUNT,
    accumulate_bins,
    has_counts_to_plot,
    length_histogram_bin_index,
    plot_length_histogram,
)
from gain.utils.chromosome_order import natural_chromosome_key

FRAGMENT_STATISTICS_FILE = "statistics/fragments.json"
FRAGMENT_LENGTHS_IMAGE_FILE = "statistics/fragment_lengths.png"

#: How a failed fold of these regions is named in the message.
_MERGE_FAILURE = "fragment statistics"


class RegionFragments:
    """The fragments of one scanned region, counted row by row.

    Consumes row spans and counts each row once, binned by its own
    length.  Unlike :class:`~.coverage.RegionCoverage` this carries no
    opt-out flag: a region is built only for a kind whose rows ARE
    fragments, so every instance publishes a tally.
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
        self._fragments = 0
        self._bins = [0] * LENGTH_HISTOGRAM_BIN_COUNT
        # Set only by :meth:`frozen`, for a stored histogram this code
        # cannot merge with: it was binned on foreign edges, so the
        # counts are still exact but the lengths read as unknown.
        self._lengths_known = True

    @classmethod
    def frozen(
        cls,
        chrom: str,
        fragments: int,
        bins: list[int] | None,
    ) -> RegionFragments:
        """A region restored from serialized counts, with no scan state.

        ``bins`` of ``None`` marks the length histogram unknown -- the
        stored one was binned on edges this code cannot merge with.  The
        COUNT is unaffected and still reads, so a file like that renders
        its table and no image.
        """
        region = cls(chrom, None, None)
        region._fragments = fragments
        if bins is None:
            region._lengths_known = False
        else:
            region._bins = list(bins)
        return region

    def add_fragment(self, length: int) -> None:
        """Count one fragment of that many base pairs.

        The row's OWN span, never clipped to the region: a region owns
        the rows beginning inside it and measures them whole, so a
        fragment is counted once at its true length however the contig
        was split.
        """
        self._fragments += 1
        self._bins[length_histogram_bin_index(length)] += 1

    def add_fragment_batch(self, lengths: np.ndarray) -> None:
        """Count a whole batch of fragment lengths at once.

        The vectorized statement of :meth:`add_fragment`, and it lives
        HERE beside that rule so the binning has one home.  Vectorized
        because a genome-scale fragment score has hundreds of thousands
        of rows, and this is the path ADR 0001 deleted the per-row
        object churn from.

        The bin is found by INTEGER comparison against the ladder's own
        edges, not by ``log2``: the edges are part of the stored format,
        and a float log of a large integer can land on the wrong side of
        a power of two.  ``searchsorted`` clamps into the open-ended
        last bin for free.
        """
        if not lengths.size:
            return
        indices = np.searchsorted(LENGTH_BIN_EDGES, lengths, side="right") - 1
        # A length below 1 sorts before the first edge and lands at -1;
        # reading that back is free, where a separate ``min()`` would be
        # another full pass over the batch.
        if indices.min() < 0:
            raise ValueError(
                f"fragment length must be positive: {lengths.min()}")
        accumulate_bins(
            self._bins,
            np.bincount(
                indices, minlength=LENGTH_HISTOGRAM_BIN_COUNT).tolist())
        self._fragments += int(lengths.size)

    @property
    def fragments(self) -> int:
        """How many rows this region counted."""
        return self._fragments

    def length_histogram(self) -> list[int] | None:
        """The region's fragment-length bins, or ``None`` if unknown."""
        if not self._lengths_known:
            return None
        return list(self._bins)

    def merge(self, other: RegionFragments) -> None:
        """Fold the adjacent region to the right into this one.

        Refuses a pair that is not adjacent-and-in-order on one
        chromosome -- see ``refuse_unmergeable``, which states that rule
        for this statistic and its two twins alike.

        No stitch is needed: a row is owned whole by exactly one region,
        so the merged count and histogram are plain sums.
        """
        refuse_unmergeable(_MERGE_FAILURE, self, other)

        self._fragments += other._fragments
        self._lengths_known = self._lengths_known and other._lengths_known
        accumulate_bins(self._bins, other._bins)
        self.end = other.end


class FragmentStatistics(Statistic):
    """A resource's fragment counts, per chromosome and global.

    Accumulates one :class:`RegionFragments` per scanned region through
    :meth:`fold_region` -- same-chromosome regions merge (adjacency
    asserted there), distinct chromosomes accumulate side by side -- and
    serializes to :data:`FRAGMENT_STATISTICS_FILE` as raw counts.
    """

    def __init__(self) -> None:
        super().__init__(
            "fragments", "Fragment counts and lengths per chromosome")
        self._regions: dict[str, RegionFragments] = {}

    def fold_region(self, region: RegionFragments) -> None:
        """Fold one region's counts in, keyed by its chromosome."""
        held = self._regions.get(region.chrom)
        if held is None:
            self._regions[region.chrom] = region
        else:
            held.merge(region)

    def fragments_by_chromosome(self) -> dict[str, int]:
        return {
            chrom: region.fragments
            for chrom, region in self._regions.items()
        }

    def fragments_global(self) -> int:
        return sum(
            region.fragments for region in self._regions.values())

    def fragment_lengths_by_chromosome(self) -> dict[str, list[int]]:
        """Per-chromosome fragment-length histograms, as stored.

        A chromosome whose histogram is unknown is left OUT rather than
        given an all-zero one, which would read as "measured, and empty".
        """
        return {
            chrom: histogram
            for chrom, region in self._regions.items()
            if (histogram := region.length_histogram()) is not None
        }

    def fragment_lengths_global(self) -> list[int] | None:
        """The bin-wise sum of every chromosome's histogram.

        ``None`` when any chromosome's is unknown: a partial roll-up
        would silently understate, the same all-or-nothing rule the
        coverage twin applies to its own optional groups.
        """
        histograms = [
            region.length_histogram()
            for region in self._regions.values()
        ]
        if not histograms or any(
                histogram is None for histogram in histograms):
            return None
        total = [0] * LENGTH_HISTOGRAM_BIN_COUNT
        for histogram in histograms:
            assert histogram is not None
            accumulate_bins(total, histogram)
        return total

    def add_value(self, value: Any) -> None:  # ruff: ignore[unused-method-argument]
        raise TypeError(
            "FragmentStatistics accumulates regions, not values; "
            "use fold_region")

    def merge(self, other: Statistic) -> None:
        if not isinstance(other, FragmentStatistics):
            raise TypeError("unexpected type of statistics to merge with")
        for region in other._regions.values():  # ruff: ignore[private-member-access]
            self.fold_region(region)

    def serialize(self) -> str:
        # One walk of the regions serves the per-chromosome entries and
        # the global roll-up.  The global histogram is written only when
        # EVERY chromosome has one, for the reason
        # ``fragment_lengths_global`` gives.
        chromosomes: dict[str, dict[str, Any]] = {}
        for chrom, region in self._regions.items():
            entry: dict[str, Any] = {"fragment_count": region.fragments}
            histogram = region.length_histogram()
            if histogram is not None:
                entry["fragment_length_histogram"] = histogram
            chromosomes[chrom] = entry
        global_entry: dict[str, Any] = {
            "fragment_count": self.fragments_global(),
        }
        lengths = self.fragment_lengths_global()
        if lengths is not None:
            global_entry["fragment_length_histogram"] = lengths
        return json.dumps({
            "format_version": 1,
            "chromosomes": chromosomes,
            "global": global_entry,
        }, indent=2)

    @staticmethod
    def deserialize(content: str) -> FragmentStatistics:
        # Only the per-chromosome counts round-trip; the global entry is
        # a roll-up recomputed from them.  Named keys are read one by
        # one and the entry dict is never iterated, so unknown keys are
        # ignored rather than rejected.
        data = json.loads(content)
        result = FragmentStatistics()
        for chrom, counts in data["chromosomes"].items():
            result.fold_region(RegionFragments.frozen(
                chrom,
                int(counts["fragment_count"]),
                _read_stored_histogram(counts)))
        return result


class FragmentRow(NamedTuple):
    """One chromosome's fragment count, as the info page renders it."""

    chrom: str
    fragments: int


class FragmentDisplay(NamedTuple):
    """The Fragments section's render payload.

    Counts only -- nothing to resolve: a fragment is a table row, and
    rows have no natural total to be a fraction of.  The global count is
    the sum of the rows, exactly as the stored statistic's global entry
    is the merge of its per-chromosome ones.
    """

    rows: list[FragmentRow]
    fragment_lengths: list[int] | None
    """The global fragment-length histogram, or ``None`` if unknown.

    Gates the section's image exactly as the coverage twin's segment
    histogram gates its own.
    """

    @property
    def global_fragments(self) -> int:
        return sum(row.fragments for row in self.rows)


def build_fragment_display(
    statistics: FragmentStatistics,
) -> FragmentDisplay:
    """The Fragments payload for a resource that has the statistic.

    Always a payload: the file existing IS the statistic, where the
    group riding inside the coverage file used to have to answer
    "present but carrying no fragments" as well (gain#1127).  Whether
    the section renders at all is decided by the file's presence, in
    the implementation's ``get_fragment_display``.
    """
    counts = statistics.fragments_by_chromosome()
    return FragmentDisplay(
        [
            FragmentRow(chrom, counts[chrom])
            for chrom in sorted(counts, key=natural_chromosome_key)
        ],
        statistics.fragment_lengths_global())


def accumulate_fragments(
    arrays: RecordArrays,
    fragments: RegionFragments,
    region: tuple[str, int | None, int | None],
) -> None:
    """Fold one batch of column arrays into the region's fragment tally.

    Fragments partition RECORDS, not positions: the rows this region
    OWNS, each measured at its own unclipped span.  So this rides
    :func:`~gain.genomic_resources.genomic_scores.records.owned_records_mask`,
    the record partition every statistic but coverage reads, and needs
    none of the clipping its coverage twin has to do -- a row is owned
    whole by exactly one region however the contig was split.
    """
    _chrom, start, end = region
    pos_begin, pos_end, _value_cells = arrays
    owned = owned_records_mask(pos_begin, start, end)
    fragments.add_fragment_batch(
        pos_end - pos_begin + 1 if owned.all()
        else pos_end[owned] - pos_begin[owned] + 1)


def merge_region_fragments(
    resource_id: str,
    regions: Iterable[RegionFragments | None],
) -> FragmentStatistics | None:
    """Fold the regions' counts, or ``None`` for a kind with no fragments."""
    ordered = regions_in_genomic_order(regions)
    if not ordered:
        return None
    statistics = FragmentStatistics()
    try:
        for region in ordered:
            statistics.fold_region(region)
    except ValueError as err:
        report_resource_failure(
            err, "could not merge the fragment statistics of", resource_id)
        raise
    return statistics


def save_and_plot_fragments(
    resource: GenomicResource,
    statistics: FragmentStatistics | None,
) -> None:
    """Write the fragment statistics and their histogram image.

    Does nothing for a kind that has no fragments, and skips the image
    when there is nothing to draw.  The same rule as
    ``save_and_plot_coverage`` and ``save_allele_statistics``.
    """
    if statistics is None:
        return
    with resource.open_raw_file(
            FRAGMENT_STATISTICS_FILE, mode="wt") as outfile:
        outfile.write(statistics.serialize())
    lengths = statistics.fragment_lengths_global()
    if not has_counts_to_plot(lengths):
        return
    with resource.open_raw_file(
            FRAGMENT_LENGTHS_IMAGE_FILE, mode="wb") as outfile:
        plot_length_histogram(outfile, lengths, "fragment")


def _read_stored_histogram(entry: dict[str, Any]) -> list[int] | None:
    """One chromosome's length histogram, or ``None`` if unusable."""
    if "fragment_length_histogram" not in entry:
        return None
    histogram = [int(count) for count in entry["fragment_length_histogram"]]
    # A histogram of any other length was binned on foreign edges; it
    # cannot merge with this code's fixed bins, so it reads as unknown.
    if len(histogram) != LENGTH_HISTOGRAM_BIN_COUNT:
        return None
    return histogram
