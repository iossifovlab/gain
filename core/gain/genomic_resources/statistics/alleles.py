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

Laid out like its coverage twin: the per-region accumulator, the
resource-wide statistic, the fold that merges a scan's regions into one,
and the write.  The scan wiring that feeds it is in
``implementations/genomic_scores_impl.py``.
"""
from __future__ import annotations

import json
from collections import Counter
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
    clip_span,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.statistics.base_statistic import (
    Statistic,
    refuse_unmergeable,
    regions_in_genomic_order,
)

ALLELE_STATISTICS_FILE = "statistics/alleles.json"

#: The five class names, in the order ADR 0020 states them -- which is
#: the order :class:`AlleleClass` declares them in, so this reads that
#: order rather than restating it.  The serialized class map is written
#: in it, so two builds of one resource produce byte-identical JSON
#: however the rows arrived.
CLASS_NAMES: tuple[str, ...] = tuple(
    allele_class.value for allele_class in AlleleClass)

#: How a failed fold of these regions is named in the message.
_MERGE_FAILURE = "allele statistics"


class AlleleCounts(NamedTuple):
    """One chromosome's -- or a whole resource's -- allele counts.

    ``class_counts`` is keyed by the class names in :data:`CLASS_NAMES`
    and sums to ``allele_count``: every row classifies, ``other``
    absorbing what does not parse as alleles.
    """

    allele_count: int
    covered_positions: int
    class_counts: dict[str, int]


def _total(counts: Iterable[AlleleCounts]) -> AlleleCounts:
    """The roll-up of several chromosomes' counts into one.

    The one statement of what the ``global`` entry IS -- read when the
    statistic is serialized and when one is asked for its global counts,
    which is why that entry is never read back from the file.
    """
    class_counts = dict.fromkeys(CLASS_NAMES, 0)
    allele_count = 0
    covered = 0
    for entry in counts:
        allele_count += entry.allele_count
        covered += entry.covered_positions
        for name, count in entry.class_counts.items():
            class_counts[name] = class_counts.get(name, 0) + count
    return AlleleCounts(allele_count, covered, class_counts)


class RegionAlleles:
    """The allele content of one scanned region, accumulated row by row.

    Counts each ROW as an allele -- duplicate ``(chrom, pos, ref, alt)``
    rows are legitimate per-transcript data and each is one allele --
    and each POSITION once however many rows sit on it.

    A region owns the rows whose point falls inside it, which is what
    makes the statistic chunk-invariant: the regions of a contig
    partition it, so a position carries rows in exactly one of them and
    no merge can double-count it.  A row's optional ``pos_end`` takes no
    part -- an allele's value stands for its ref/alt pair, not for the
    bases such a column may reach over -- so ownership is the shared
    :func:`~gain.genomic_resources.genomic_scores.clip_span` asked about
    the point, and gain#636's edge is answered there rather than again
    here.

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
        self._class_counts: dict[str, int] = dict.fromkeys(CLASS_NAMES, 0)
        self._last_pos: int | None = None

    @classmethod
    def frozen(
        cls,
        chrom: str,
        allele_count: int,
        covered_positions: int,
        class_counts: dict[str, int],
    ) -> RegionAlleles:
        """A region restored from serialized counts, with no scan state."""
        region = cls(chrom, None, None)
        region.allele_count = allele_count
        region.covered_positions = covered_positions
        region._class_counts = {
            name: class_counts.get(name, 0) for name in CLASS_NAMES}
        return region

    def counts(self) -> AlleleCounts:
        """This region's counts, class map keyed by class name."""
        return AlleleCounts(
            self.allele_count,
            self.covered_positions,
            dict(self._class_counts))

    def _owns(self, pos: int) -> bool:
        """Whether this region owns the row sitting at ``pos``."""
        return clip_span(pos, pos, self.start, self.end) is not None

    def _count_position(self, pos: int) -> None:
        if pos != self._last_pos:
            self.covered_positions += 1
            self._last_pos = pos

    def add_allele(
        self, pos: int, ref: str | None, alt: str | None,
    ) -> None:
        """Fold one row, read as the point at ``pos``."""
        if not self._owns(pos):
            return
        self.allele_count += 1
        self._count_position(pos)
        self._class_counts[classify_allele(ref, alt).allele_class.value] += 1

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
        """Fold a batch of column arrays, the counting vectorized.

        The same rule :meth:`add_allele` applies row by row.  Ownership
        and the distinct-position count are vectorized outright; the
        classification cannot be -- a class is a property of one ref/alt
        PAIR, and an array statement of it would be a second spelling of
        :func:`classify_allele` -- so instead each DISTINCT pair in the
        batch is classified once and its multiplicity added.  Same
        function, same answer, called once per pair rather than once per
        row: a real allele score is overwhelmingly substitutions, so a
        100,000-row batch usually holds a handful of distinct pairs, and
        this is ~7x the row-by-row fold over whole-genome data.  (A
        batch of entirely distinct pairs pays a small tally overhead
        instead.)

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

        self.allele_count += int(positions.shape[0])
        # The positions arrive non-decreasing (the door's rule), so the
        # distinct count is the number of CHANGES within the batch, plus
        # the batch's first position unless the last batch ended on it.
        self._count_position(int(positions[0]))
        self.covered_positions += int(
            np.count_nonzero(positions[1:] != positions[:-1]))
        self._last_pos = int(positions[-1])

        for pair, multiplicity in Counter(
                zip(refs.tolist(), alts.tolist(), strict=True)).items():
            self._class_counts[
                classify_allele(*pair).allele_class.value] += multiplicity

    def merge(self, other: RegionAlleles) -> None:
        """Fold the adjacent region to the right into this one.

        It is the adjacency -- asserted by ``refuse_unmergeable`` -- that
        lets the counts simply add: a position belongs to exactly one of
        two adjacent regions, so none is counted twice.
        """
        refuse_unmergeable(_MERGE_FAILURE, self, other)
        self.allele_count += other.allele_count
        self.covered_positions += other.covered_positions
        for name in CLASS_NAMES:
            self._class_counts[name] += \
                other._class_counts[name]
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
        return _total(self.by_chromosome().values())

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
        # One walk of the regions serves the per-chromosome entries and
        # the global roll-up.
        chromosomes = self.by_chromosome()
        return json.dumps({
            "format_version": 1,
            "chromosomes": {
                chrom: counts._asdict()
                for chrom, counts in chromosomes.items()
            },
            "global": _total(chromosomes.values())._asdict(),
        }, indent=2)

    @staticmethod
    def deserialize(content: str) -> AlleleStatistics:
        # Only the per-chromosome counts round-trip; the global entry is
        # a roll-up recomputed from them by ``_total``, and the
        # last-position bookkeeping is scan state and is never written.
        # Unknown keys are ignored rather than rejected, so a file
        # carrying fields a later slice added still reads.
        data = json.loads(content)
        result = AlleleStatistics()
        for chrom, counts in data["chromosomes"].items():
            result.fold_region(RegionAlleles.frozen(
                chrom,
                int(counts["allele_count"]),
                int(counts["covered_positions"]),
                {
                    name: int(count)
                    for name, count in counts["class_counts"].items()
                },
            ))
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

    Ask it of an OPEN score.  On an unopened one the answer is merely
    conservative -- a table naming its key columns nowhere but inside
    its own data file cannot be known to have them until that header is
    read -- and a spurious ``False`` costs the whole region the bulk
    scan for no gain in correctness.
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


def allele_arrays_folded_into(
    score: AlleleScore,
    chrom: str,
    start: int,
    end: int,
    score_ids: list[str],
    *,
    batch_size: int,
    alleles: RegionAlleles,
) -> Generator[RecordArrays, None, None]:
    """The bulk read, nucleotides folded off it, validated as usual.

    The array twin of :func:`records_folded_into`, and the same shape: a
    transducer that folds each batch and yields it onward.  What it
    yields is the batch's ``[:3]`` slice -- a plain ``RecordArrays`` --
    because the scan's array door (``validate_record_arrays``, ADR 0008)
    unpacks three names and raises on the five an
    :class:`~gain.genomic_resources.genomic_scores.AlleleRecordArrays`
    carries.

    Folding on the way IN is what lets the nucleotides reach this
    statistic without the door having to carry them: nothing downstream
    ever sees the widened batch, so nothing has to pair the two back up.

    That the fold precedes the door's verdict is unobservable: a region
    the door refuses raises out of the scan, and its accumulator is
    discarded with the failed task rather than merged.
    """
    def folded() -> Generator[AlleleRecordArrays, None, None]:
        for batch in score.fetch_region_allele_arrays(
                chrom, start, end, score_ids, batch_size=batch_size):
            alleles.add_allele_batch(
                batch.pos_begin, batch.reference, batch.alternative)
            yield batch

    yield from score.validate_record_arrays(
        (batch[:3] for batch in folded()), chrom)


def merge_region_alleles(
    resource_id: str,
    regions: Iterable[RegionAlleles | None],
) -> AlleleStatistics | None:
    """Fold the regions' counts, or ``None`` for a kind that has none."""
    ordered = regions_in_genomic_order(regions)
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
    with resource.open_raw_file(
            ALLELE_STATISTICS_FILE, mode="wt") as outfile:
        outfile.write(statistics.serialize())
