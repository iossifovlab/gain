"""Fragment count and exact fragment-length statistics for fragment scores.

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

Lengths are kept EXACTLY, as the kind-neutral record in
:mod:`gain.genomic_resources.statistics.exact_lengths` (ADR 0020 as
amended by gain#1544, the way segments left the stored ladder in
gain#1543 and the indel groups in gain#1118): the file stores the
record, the table on the page is read off it, and the chart is drawn
on the ladder derived from it at render time.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any, NamedTuple

import numpy as np

from gain.genomic_resources.genomic_scores import (
    FragmentScore,
    GenomicScore,
    RecordArrays,
    owned_records_mask,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.statistics.base_statistic import (
    RegionFoldedStatistic,
    refuse_unmergeable,
)
from gain.genomic_resources.statistics.exact_lengths import (
    ExactLengths,
    LengthArrayTally,
    LengthStatisticsRow,
    folded_lengths,
    stored_lengths,
    write_length_chart,
)
from gain.genomic_resources.statistics.region_fold import merge_regions
from gain.utils.chromosome_order import natural_chromosome_key

FRAGMENT_STATISTICS_FILE = "statistics/fragments.json"
FRAGMENT_LENGTHS_IMAGE_FILE = "statistics/fragment_lengths.png"

#: How a failed fold of these regions is named in the message.
_MERGE_FAILURE = "fragment statistics"


class RegionFragments:
    """The fragments of one scanned region, counted row by row.

    Consumes row spans and counts each row once, at its own length.
    Unlike :class:`~.coverage.RegionCoverage` this carries no opt-out
    flag: a region is built only for a kind whose rows ARE fragments, so
    every instance publishes a count.
    """

    def __init__(
        self,
        chrom: str,
        start: int | None,
        end: int | None,
        *,
        accumulates: bool = True,
    ) -> None:
        self.chrom = chrom
        self.start = start
        self.end = end
        self._fragments = 0
        # The region's lengths, in whichever form it holds them: the
        # array tally a scanned region accumulates into, built here; the
        # stored record a region restored through :meth:`frozen` -- the
        # one caller that clears ``accumulates`` -- holds as read; or
        # ``None`` for a restored region whose file stored the count
        # without the record (format version 1), where the count stays
        # exact while the lengths read as unknown.  Held as one value
        # rather than a tally beside a flag, so the forms cannot
        # disagree and accumulating into a frozen region is an
        # assertion rather than silent work.
        self._lengths: LengthArrayTally | ExactLengths | None = \
            LengthArrayTally() if accumulates else None

    @classmethod
    def frozen(
        cls,
        chrom: str,
        fragments: int,
        lengths: ExactLengths | None,
    ) -> RegionFragments:
        """A region restored from serialized counts, with no scan state.

        ``lengths`` of ``None`` marks the length record unknown -- the
        file stored the count alone.  The COUNT is unaffected and still
        reads, so a file like that renders its table and no image.
        """
        region = cls(chrom, None, None, accumulates=False)
        region._fragments = fragments
        region._lengths = lengths
        return region

    def _accumulating(self) -> LengthArrayTally:
        """The tally a scanned region folds its lengths into.

        The one gate behind every feed and the merge: a region restored
        from a file holds counts, not scan state, so a length reaching
        it is a wiring error.
        """
        assert isinstance(self._lengths, LengthArrayTally), \
            "a frozen region does not accumulate"
        return self._lengths

    def add_fragment(self, length: int) -> None:
        """Count one fragment of that many base pairs.

        The row's OWN span, never clipped to the region: a region owns
        the rows beginning inside it and measures them whole, so a
        fragment is counted once at its true length however the contig
        was split.
        """
        self._accumulating().add(length)
        self._fragments += 1

    def add_fragment_batch(self, lengths: np.ndarray) -> None:
        """Count a whole batch of fragment lengths at once.

        The vectorized statement of :meth:`add_fragment`: one fold of
        the whole array into the tally, because a genome-scale fragment
        score has hundreds of thousands of rows, and this is the path
        ADR 0001 deleted the per-row object churn from.  A length below
        1 is refused by the tally itself.
        """
        self._accumulating().add_batch(lengths)
        self._fragments += lengths.size

    @property
    def fragments(self) -> int:
        """How many rows this region counted."""
        return self._fragments

    def fragment_lengths(self) -> ExactLengths | None:
        """The region's fragment lengths as a record, ``None`` if unknown."""
        if isinstance(self._lengths, LengthArrayTally):
            return self._lengths.frozen()
        return self._lengths

    def merge(self, other: RegionFragments) -> None:
        """Fold the adjacent region to the right into this one.

        Refuses a pair that is not adjacent-and-in-order on one
        chromosome -- see ``refuse_unmergeable``, which states that rule
        for this statistic and its two twins alike.

        No stitch is needed: a row is owned whole by exactly one region,
        so the merged count is a plain sum and the merged lengths are the
        tallies' own merge.  Only scanned regions get this far: a
        region restored from a file has no extents, and the adjacency
        rule refuses it.
        """
        refuse_unmergeable(_MERGE_FAILURE, self, other)

        self._fragments += other._fragments
        self._accumulating().merge(other._accumulating())
        self.end = other.end


class FragmentStatistics(RegionFoldedStatistic[RegionFragments]):
    """A resource's fragment counts and lengths, per chromosome and global.

    Folds :class:`RegionFragments` the way the base class does, and
    serializes to :data:`FRAGMENT_STATISTICS_FILE` as raw counts beside
    each chromosome's exact length record.
    """

    def __init__(self) -> None:
        super().__init__(
            "fragments", "Fragment counts and lengths per chromosome")

    def fragments_by_chromosome(self) -> dict[str, int]:
        return {
            chrom: region.fragments
            for chrom, region in self._regions.items()
        }

    def fragments_global(self) -> int:
        return sum(
            region.fragments for region in self._regions.values())

    def fragment_lengths_by_chromosome(self) -> dict[str, ExactLengths]:
        """Per-chromosome fragment-length records, as stored.

        A chromosome whose lengths are unknown is left OUT rather than
        given an empty record, which would read as "measured, and empty".
        """
        return {
            chrom: lengths
            for chrom, region in self._regions.items()
            if (lengths := region.fragment_lengths()) is not None
        }

    def fragment_lengths_global(self) -> ExactLengths | None:
        """The fold of every chromosome's lengths -- :func:`folded_lengths`,
        so the all-or-nothing rule is the coverage twin's."""
        return folded_lengths(
            region.fragment_lengths() for region in self._regions.values())

    def serialize(self) -> str:
        # Each region's lengths are read ONCE, and both the
        # per-chromosome entries and the global roll-up come off that.
        # The global record is written only when EVERY chromosome has
        # one, for the reason ``fragment_lengths_global`` gives.
        records = {
            chrom: region.fragment_lengths()
            for chrom, region in self._regions.items()
        }
        chromosomes: dict[str, dict[str, Any]] = {}
        for chrom, region in self._regions.items():
            entry: dict[str, Any] = {"fragment_count": region.fragments}
            lengths = records[chrom]
            if lengths is not None:
                entry["fragment_lengths"] = lengths.stored()
            chromosomes[chrom] = entry
        global_entry: dict[str, Any] = {
            "fragment_count": self.fragments_global(),
        }
        global_lengths = folded_lengths(records.values())
        if global_lengths is not None:
            global_entry["fragment_lengths"] = global_lengths.stored()
        return json.dumps({
            "format_version": 2,
            "chromosomes": chromosomes,
            "global": global_entry,
        }, indent=2)

    @staticmethod
    def deserialize(content: str) -> FragmentStatistics:
        # Only the per-chromosome entries round-trip; the global entry is
        # a roll-up recomputed from them.  Named keys are read one by
        # one and the entry dict is never iterated, so unknown keys --
        # a version 1 file's ``fragment_length_histogram`` among them --
        # are ignored rather than rejected.
        data = json.loads(content)
        result = FragmentStatistics()
        for chrom, counts in data["chromosomes"].items():
            result.fold_region(RegionFragments.frozen(
                chrom,
                int(counts["fragment_count"]),
                stored_lengths(counts, "fragment_lengths")))
        return result


class FragmentRow(NamedTuple):
    """One chromosome's fragment count, as the info page renders it."""

    chrom: str
    fragments: int


class FragmentDisplay(NamedTuple):
    """The Fragments section's render payload.

    Counts and the global length record -- nothing to resolve: a
    fragment is a table row, and rows have no natural total to be a
    fraction of.  The global count is the sum of the rows, exactly as
    the stored statistic's global entry is the merge of its
    per-chromosome ones.
    """

    rows: list[FragmentRow]
    fragment_lengths: ExactLengths | None
    """The global fragment-length record, or ``None`` if unknown.

    Unknown is a THIRD answer, distinct from a record that is known and
    empty: the counts and the lengths are read independently, so a file
    that stored the counts alone leaves the lengths unknown while the
    counts stay exact.  The section renders all three apart -- "not
    computed", the table and chart, "no fragments" -- because collapsing
    the first into the last would deny fragments the table beside it is
    counting.
    """

    @property
    def global_fragments(self) -> int:
        return sum(row.fragments for row in self.rows)

    @property
    def fragment_row(self) -> LengthStatisticsRow | None:
        """The one row of the "Fragment lengths" table, if known."""
        if self.fragment_lengths is None:
            return None
        return LengthStatisticsRow.of("fragments", self.fragment_lengths)


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


def region_fragments_for(
    score: GenomicScore,
    chrom: str,
    start: int | None,
    end: int | None,
) -> RegionFragments | None:
    """A region accumulator for a fragment score, ``None`` for other kinds.

    Gated on the built score's class rather than on the resource type
    string, for the reason
    :func:`~gain.genomic_resources.statistics.alleles.region_alleles_for`
    gives: the builder has already resolved both spellings of the kind
    to ``FragmentScore``, and asking the string again would restate its
    dispatch.  The class is the property this statistic depends on --
    that the rows ARE fragments, each counted once at its own span.
    """
    if not isinstance(score, FragmentScore):
        return None
    return RegionFragments(chrom, start, end)


def accumulate_fragments(
    arrays: RecordArrays,
    fragments: RegionFragments,
    region: tuple[str, int | None, int | None],
) -> None:
    """Fold one batch of column arrays into the region's fragment tally.

    Fragments partition RECORDS: the rows this region OWNS, each
    measured at its own unclipped span.  So this rides
    :func:`~gain.genomic_resources.genomic_scores.records.owned_records_mask`,
    the record partition every statistic reads -- a row is owned whole
    by exactly one region however the contig was split.
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
    return merge_regions(
        resource_id, regions, FragmentStatistics, _MERGE_FAILURE)


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
    write_length_chart(
        resource, FRAGMENT_LENGTHS_IMAGE_FILE,
        statistics.fragment_lengths_global(), "fragment")
