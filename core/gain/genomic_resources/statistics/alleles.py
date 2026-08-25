"""Allele-content statistics for allele-score resources.

Vocabulary per ``CONTEXT.md`` and ADR 0020.  Where the coverage statistic
answers *where* a score holds data, this one answers *what* its rows are:
how many **alleles** a resource carries, over how many **covered
positions**, and how those alleles distribute over the five **allele
classes**.

An allele row collapses to the point it sits at, so its coverage is a
DISTINCT-POSITION count rather than the span union
:mod:`gain.genomic_resources.statistics.coverage` computes -- which is
why allele scores are deliberately absent from that statistic's kinds and
counted here instead.

Raw counts only.  Anything needing a denominator is computed at render
time, as the coverage statistic's fractions are.
"""
from __future__ import annotations

import json
from collections.abc import Generator, Iterable, Iterator
from typing import Any, NamedTuple

import numpy as np

from gain.genomic_resources.allele_classification import (
    AlleleClass,
    classify_allele,
)
from gain.genomic_resources.cli_errors import report_resource_failure
from gain.genomic_resources.genomic_position_table.record import (
    ALT,
    POS_BEGIN,
    REF,
    Record,
)
from gain.genomic_resources.genomic_scores import (
    AlleleRecordArrays,
    AlleleScore,
    GenomicScore,
    RecordArrays,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.statistics.base_statistic import Statistic

ALLELE_STATISTICS_FILE = "statistics/alleles.json"

#: The five classes in the order ADR 0020 states them.  The serialized
#: class map is written in this order, so two builds of one resource
#: produce byte-identical JSON however the rows arrived.
CLASS_ORDER: tuple[AlleleClass, ...] = (
    AlleleClass.SUBSTITUTION,
    AlleleClass.INSERTION,
    AlleleClass.DELETION,
    AlleleClass.COMPLEX,
    AlleleClass.OTHER,
)


class AlleleCounts(NamedTuple):
    """One chromosome's -- or a whole resource's -- allele counts.

    ``class_counts`` is keyed by the class names in :data:`CLASS_ORDER`
    and sums to ``allele_count``: every row classifies, ``other``
    absorbing what does not parse as alleles.
    """

    allele_count: int
    covered_positions: int
    class_counts: dict[str, int]


class RegionAlleles:
    """The allele content of one scanned region, accumulated row by row.

    Counts each ROW as an allele -- duplicate ``(chrom, pos, ref, alt)``
    rows are legitimate per-transcript data and each is one allele --
    and each POSITION once however many rows sit on it.

    A region owns the rows whose point falls inside it, ``start <= pos
    <= end``.  That is what makes the statistic chunk-invariant: the
    regions of a contig partition it, so a position carries rows in
    exactly one of them and no merge can double-count it.  A row's
    optional ``pos_end`` takes no part -- an allele's value stands for
    its ref/alt pair, not for the bases such a column may reach over.

    Distinct positions are counted against the LAST position seen
    rather than a set of every position: the scan's door refuses a
    record beginning before the one before it, so the positions arrive
    non-decreasing and a change of position is a new one.  A set would
    be exact for the same rows at the cost of holding a whole
    chromosome's positions in memory.
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
        self.allele_count = 0
        self.covered_positions = 0
        self._class_counts: dict[AlleleClass, int] = dict.fromkeys(
            CLASS_ORDER, 0)
        self._last_pos: int | None = None

    @classmethod
    def frozen(
        cls,
        chrom: str,
        counts: AlleleCounts,
    ) -> RegionAlleles:
        """A region restored from serialized counts, with no scan state."""
        region = cls(chrom, None, None)
        region.allele_count = counts.allele_count
        region.covered_positions = counts.covered_positions
        for allele_class in CLASS_ORDER:
            region._class_counts[allele_class] = \
                counts.class_counts.get(allele_class.value, 0)
        return region

    def counts(self) -> AlleleCounts:
        """This region's counts, class map keyed by class name."""
        return AlleleCounts(
            self.allele_count,
            self.covered_positions,
            {
                allele_class.value: self._class_counts[allele_class]
                for allele_class in CLASS_ORDER
            },
        )

    def _owns(self, pos: int) -> bool:
        return (self.start is None or pos >= self.start) \
            and (self.end is None or pos <= self.end)

    def _fold(self, pos: int, ref: str | None, alt: str | None) -> None:
        """Count one owned row.  The whole counting rule, stated once."""
        self.allele_count += 1
        if pos != self._last_pos:
            self.covered_positions += 1
            self._last_pos = pos
        self._class_counts[classify_allele(ref, alt).allele_class] += 1

    def add_allele(
        self, pos: int, ref: str | None, alt: str | None,
    ) -> None:
        """Fold one row, read as the point at ``pos``."""
        if self._owns(pos):
            self._fold(pos, ref, alt)

    def add_record(self, record: Record) -> None:
        """Fold one raw record.

        The nucleotides come off the RAW record: the region-values
        transform an allele score applies collapses a row to the point
        it sits at and drops them (``region_values_from_records``).
        """
        self.add_allele(record[POS_BEGIN], record[REF], record[ALT])

    def add_allele_batch(
        self,
        pos_begin: np.ndarray,
        reference: np.ndarray,
        alternative: np.ndarray,
    ) -> None:
        """Fold a batch of column arrays, region ownership vectorized.

        The same rule :meth:`add_allele` applies row by row -- only the
        ownership test is vectorized.  The classification itself is not:
        a class is a property of one ref/alt PAIR, and there is no array
        statement of it that would not be a second spelling of
        :func:`classify_allele`.

        The nucleotide arrays are RAW, as
        :meth:`AlleleScore.fetch_region_allele_arrays` yields them, so
        this path hands the classifier exactly the strings the
        per-record path does.
        """
        keep = np.ones(pos_begin.shape[0], dtype=bool)
        if self.start is not None:
            keep &= pos_begin >= self.start
        if self.end is not None:
            keep &= pos_begin <= self.end
        if not keep.any():
            return
        if keep.all():
            positions, refs, alts = pos_begin, reference, alternative
        else:
            positions = pos_begin[keep]
            refs = reference[keep]
            alts = alternative[keep]
        for pos, ref, alt in zip(
                positions.tolist(), refs.tolist(), alts.tolist(),
                strict=True):
            self._fold(pos, ref, alt)

    def merge(self, other: RegionAlleles) -> None:
        """Fold the adjacent region to the right into this one.

        Refuses a pair that is not adjacent-and-in-order on one
        chromosome, for the reason
        :meth:`~gain.genomic_resources.statistics.coverage.RegionCoverage.merge`
        gives: region statistics are only ever produced over a contig's
        non-overlapping windows, and it is the adjacency that makes
        the distinct-position counts simply add.
        """
        if self.chrom != other.chrom:
            raise ValueError(
                f"allele statistics merge across chromosome boundaries: "
                f"{self.chrom} and {other.chrom}")
        if self.end is None or other.start is None \
                or self.end + 1 != other.start:
            raise ValueError(
                f"allele statistics regions are not adjacent-and-in-order: "
                f"{self.chrom}:{self.start}-{self.end} then "
                f"{other.chrom}:{other.start}-{other.end}")
        self.allele_count += other.allele_count
        self.covered_positions += other.covered_positions
        for allele_class in CLASS_ORDER:
            self._class_counts[allele_class] += \
                other._class_counts[allele_class]
        if other._last_pos is not None:
            self._last_pos = other._last_pos
        self.end = other.end


class AlleleStatistics(Statistic):
    """A resource's allele content, per chromosome and global.

    Accumulates one :class:`RegionAlleles` per scanned region through
    :meth:`fold_region` -- same-chromosome regions merge (adjacency
    asserted there), distinct chromosomes accumulate side by side -- and
    serializes to the resource's :data:`ALLELE_STATISTICS_FILE` as raw
    counts.
    """

    FORMAT_VERSION = 1

    def __init__(self) -> None:
        super().__init__(
            "alleles",
            "Allele counts, covered positions and class totals")
        self._regions: dict[str, RegionAlleles] = {}

    def fold_region(self, region: RegionAlleles) -> None:
        """Fold one region's counts in, keyed by its chromosome."""
        held = self._regions.get(region.chrom)
        if held is None:
            self._regions[region.chrom] = region
        else:
            held.merge(region)

    def by_chromosome(self) -> dict[str, AlleleCounts]:
        return {
            chrom: region.counts()
            for chrom, region in self._regions.items()
        }

    def global_counts(self) -> AlleleCounts:
        """The roll-up over every chromosome."""
        class_counts = {
            allele_class.value: 0 for allele_class in CLASS_ORDER}
        allele_count = 0
        covered = 0
        for region in self._regions.values():
            counts = region.counts()
            allele_count += counts.allele_count
            covered += counts.covered_positions
            for name, count in counts.class_counts.items():
                class_counts[name] += count
        return AlleleCounts(allele_count, covered, class_counts)

    def add_value(self, value: Any) -> None:  # noqa: ARG002
        raise TypeError(
            "AlleleStatistics accumulates regions, not values; "
            "use fold_region")

    def merge(self, other: Statistic) -> None:
        if not isinstance(other, AlleleStatistics):
            raise TypeError("unexpected type of statistics to merge with")
        for region in other._regions.values():  # noqa: SLF001
            self.fold_region(region)

    def serialize(self) -> str:
        return json.dumps({
            "format_version": self.FORMAT_VERSION,
            "chromosomes": {
                chrom: counts._asdict()
                for chrom, counts in self.by_chromosome().items()
            },
            "global": self.global_counts()._asdict(),
        }, indent=2)

    @staticmethod
    def deserialize(content: str) -> AlleleStatistics:
        # Only the per-chromosome counts round-trip; the global entry is
        # a roll-up recomputed from them, and the last-position
        # bookkeeping is scan state and is never written.  Unknown keys
        # are ignored rather than rejected, so a file carrying extra
        # fields still reads.
        data = json.loads(content)
        result = AlleleStatistics()
        for chrom, counts in data["chromosomes"].items():
            result.fold_region(RegionAlleles.frozen(chrom, AlleleCounts(
                int(counts["allele_count"]),
                int(counts["covered_positions"]),
                {
                    name: int(count)
                    for name, count in counts["class_counts"].items()
                },
            )))
        return result


def region_alleles_for(
    score: GenomicScore,
    chrom: str,
    start: int | None,
    end: int | None,
) -> RegionAlleles | None:
    """A region accumulator for an allele score, ``None`` for other kinds.

    Gated on the BUILT SCORE CLASS rather than the resource type string:
    ``allele_score`` and the deprecated ``np_score`` both build an
    :class:`~gain.genomic_resources.genomic_scores.AlleleScore`, and
    ``equivalent_resource_types`` does not alias them -- it aliases only
    the two fragment-score spellings.  A gate written on the type
    strings would therefore skip ``np_score`` with no error and no
    failing test, which is precisely the deprecated spelling this
    statistic must keep serving (gain#777).
    """
    if not isinstance(score, AlleleScore):
        return None
    return RegionAlleles(chrom, start, end)


def serves_allele_arrays(score: GenomicScore, score_ids: list[str]) -> bool:
    """Whether the bulk read can hand this score's rows their nucleotides.

    Asked BEFORE a path is chosen rather than caught after: a region
    whose backend will not serve the ref/alt arrays must fall back to
    the per-record scan, which reads the nucleotides off the record,
    rather than produce a statistic with no class data.
    """
    return isinstance(score, AlleleScore) \
        and score.supports_region_allele_arrays(score_ids)


def records_folded_into(
    records: Iterator[Record],
    alleles: RegionAlleles,
) -> Generator[Record, None, None]:
    """Yield a record stream through, folding each row into ``alleles``.

    A transducer over the very stream the per-record scan is already
    pulling: this statistic rides that one read rather than re-reading
    the region, exactly as the coverage statistic rides it.
    """
    for record in records:
        alleles.add_record(record)
        yield record


def validated_allele_batches(
    score: AlleleScore,
    chrom: str,
    start: int,
    end: int,
    score_ids: list[str],
    batch_size: int,
) -> Generator[AlleleRecordArrays, None, None]:
    """The bulk read's batches, nucleotides kept, validated as usual.

    ``validate_record_arrays`` -- the scan's array door (ADR 0008) --
    unpacks the three names of a shared ``RecordArrays`` and raises on
    the five an :class:`AlleleRecordArrays` carries, so the batches go
    through it as ``batch[:3]`` and the widened batch is handed back
    here.  The door is a transducer: it yields exactly what it was
    given, in order, one for one, which is what lets the slice and the
    whole batch be paired without buffering the region.
    """
    held: list[AlleleRecordArrays] = []

    def shared_view() -> Generator[RecordArrays, None, None]:
        for batch in score.fetch_region_allele_arrays(
                chrom, start, end, score_ids, batch_size=batch_size):
            held.append(batch)
            yield batch[:3]

    for _validated in score.validate_record_arrays(shared_view(), chrom):
        yield held.pop()


def merge_region_alleles(
    resource_id: str,
    regions: Iterable[RegionAlleles | None],
) -> AlleleStatistics | None:
    """Fold the regions' counts, or ``None`` for a kind that has none.

    The fold sorts the regions into genomic order rather than trusting
    the task-argument order they arrived in, is keyed by chromosome, and
    within one chromosome :meth:`RegionAlleles.merge` still refuses a
    pair that is not adjacent-and-in-order -- a gap or an overlap fails
    the build rather than mis-counting it.
    """
    ordered = sorted(
        (region for region in regions if region is not None),
        key=lambda region: (
            region.chrom,
            region.start if region.start is not None else 0))
    if not ordered:
        return None
    statistics = AlleleStatistics()
    try:
        for region in ordered:
            statistics.fold_region(region)
    except ValueError as err:
        report_resource_failure(
            err, "could not merge the allele statistics of", resource_id)
        raise
    return statistics


def save_allele_statistics(
    resource: GenomicResource,
    statistics: AlleleStatistics | None,
) -> None:
    """Write the statistics into the resource, or do nothing without any."""
    if statistics is None:
        return
    with resource.proto.open_raw_file(
            resource, ALLELE_STATISTICS_FILE, mode="wt") as outfile:
        outfile.write(statistics.serialize())
