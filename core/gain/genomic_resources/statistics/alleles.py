"""Allele-content statistics for allele-score resources.

Vocabulary per ``CONTEXT.md`` and ADR 0020.  Where the coverage statistic
answers *where* a score holds data, this one answers *what* its rows are:
how many **alleles** a resource carries, over how many **covered
positions**, how those alleles distribute over the five **allele
classes**, and what the classes with structure look like inside -- the
4x4 ref->alt matrix for substitutions, length histograms for the two
anchored classes, and the **complex grid** for the rest.

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
``implementations/genomic_scores_impl/scan.py``.
"""
from __future__ import annotations

import json
from collections import Counter
from collections.abc import Generator, Iterable, Iterator, Mapping
from typing import IO, Any, NamedTuple

import numpy as np

from gain.genomic_resources.allele_classification import (
    ALLELE_BASES,
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
from gain.genomic_resources.statistics.length_histogram import (
    LENGTH_HISTOGRAM_BIN_COUNT,
    accumulate_bins,
    has_counts_to_plot,
    length_histogram_bin_index,
    plot_length_histogram,
)
from gain.utils.chromosome_order import natural_chromosome_key

ALLELE_STATISTICS_FILE = "statistics/alleles.json"

#: The global images the statistics build renders beside the file.  One
#: each, never per chromosome (ADR 0020): the per-chromosome numbers are
#: stored as data and rolled up for the picture.
ALLELE_INSERTION_LENGTHS_IMAGE_FILE = \
    "statistics/allele_insertion_lengths.png"
ALLELE_DELETION_LENGTHS_IMAGE_FILE = \
    "statistics/allele_deletion_lengths.png"
ALLELE_COMPLEX_GRID_IMAGE_FILE = "statistics/allele_complex_grid.png"

#: The five class names, in the order ADR 0020 states them -- which is
#: the order :class:`AlleleClass` declares them in, so this reads that
#: order rather than restating it.  The serialized class map is written
#: in it, so two builds of one resource produce byte-identical JSON
#: however the rows arrived.
CLASS_NAMES: tuple[str, ...] = tuple(
    allele_class.value for allele_class in AlleleClass)

#: The nucleotides an allele may be written with, in the order the
#: classifier's alphabet states them.  The substitution matrix's cells
#: are keyed and serialized in this one order, so two builds of one
#: resource produce byte-identical JSON however the rows arrived.
NUCLEOTIDES: tuple[str, ...] = tuple(ALLELE_BASES)

#: The sixteen ref->alt cells of the substitution matrix, row-major in
#: :data:`NUCLEOTIDES` order.  The identity pairs are cells like any
#: other: ADR 0020 classifies ``A>A`` as a substitution.
MATRIX_CELLS: tuple[tuple[str, str], ...] = tuple(
    (ref, alt) for ref in NUCLEOTIDES for alt in NUCLEOTIDES)

#: The longest allele length the complex grid resolves exactly.  A
#: length at or above it lands in the grid's top row or column, which
#: therefore reads "this many bases or more".  The clamp is TOTAL --
#: every complex row lands in exactly one cell, so the grid's total is
#: the complex class count and no overflow counter is needed.
#:
#: Exact lengths rather than the shared log2 ladder (gain#779): that
#: ladder's first bin is exactly length 1, which no complex pair can
#: have -- a 1->1 pair is a substitution -- and its second bin is
#: {2, 3}, which would put a 2->3 complex in the same cell as a 3bp
#: MNV and empty the diagonal of its meaning.  Part of the stored
#: format: it must not change once resources carry grids built from it.
COMPLEX_LENGTH_CLAMP = 64

#: How many occupied cells the complex grid may hold and still render as
#: a table of those cells rather than as the heatmap (gain#989).
#:
#: A judgement call, not a derivation: 32 rows is about where a table
#: stops being scannable, and a 64x64 grid starts having enough lit
#: cells to show shape.  Unlike :data:`COMPLEX_LENGTH_CLAMP` this is a
#: RENDERING choice and no part of the stored format.
#:
#: RAISING it is free: a resource built under the old value keeps a PNG
#: its page no longer references, which is the leftover image
#: :func:`save_allele_statistics` already documents.  LOWERING it needs
#: the resources rebuilt with ``--force``: a grid between the two
#: values would start asking for a PNG that was never written, and a
#: plain ``repo-stats`` will not notice -- the statistics hash covers
#: the table config, the score definitions and the data files, none of
#: which a constant here moves.
COMPLEX_GRID_TABLE_MAX_CELLS = 32

#: How a failed fold of these regions is named in the message.
_MERGE_FAILURE = "allele statistics"


def percentages_over[K](
    counts: Mapping[K, int], total: int,
) -> dict[K, str] | None:
    """Each count as a percentage of ``total``, ``None`` without one.

    The one place the ALLELES section writes a share of a count, so the
    classes column, the substitution matrix's cells and gain#989's
    complex table all say the same thing the same way.  The Coverage
    table above it still formats its own fractions inline and has no
    floor; unifying the two is not this slice's business.

    The result is text for an HTML page and can carry markup-significant
    characters -- the floor below begins with ``<`` -- so a template
    rendering it must escape, which the ``.jinja`` HTML templates do and
    the Markdown ones deliberately do not.

    Two answers a bare ``"%.2f%%"`` gets wrong:

    * A nonzero count too small to survive two decimals renders
      ``<0.01%``, never ``0.00%``.  On a real score ``complex`` is 881
      alleles out of 727,413,443 while ``other`` is genuinely empty --
      and telling those two apart is the whole reason a percentage is
      shown at all.
    * A zero total has no percentage, and the answer is ``None`` for
      the WHOLE map rather than per cell: the denominator is a property
      of the table, so the page drops the column instead of printing a
      row of nothing, as the coverage display falls back to raw counts
      when no denominator resolves.
    """
    if total <= 0:
        return None
    percentages: dict[K, str] = {}
    for key, count in counts.items():
        rendered = f"{100.0 * count / total:.2f}%"
        percentages[key] = \
            "<0.01%" if count and rendered == "0.00%" else rendered
    return percentages


def _length_label(length: int) -> str:
    """A complex-cell length, ``≥64`` at the clamp.

    Spelled with the SIGN rather than ``>=``, exactly as
    :func:`plot_complex_grid` labels its axes, so the table and the
    heatmap say the same thing about the same cell in the same
    characters.
    """
    if length >= COMPLEX_LENGTH_CLAMP:
        return f"≥{COMPLEX_LENGTH_CLAMP}"
    return str(length)


def _occupied_cells(
    grid: dict[tuple[int, int], int],
) -> list[tuple[tuple[int, int], int]]:
    """The grid's cells that hold alleles, most populated first.

    A zero-count cell is NOT occupied: the heatmap masks it out rather
    than colouring it, so it must not become a table row nor count
    towards :data:`COMPLEX_GRID_TABLE_MAX_CELLS` either.

    Ties break on the cell itself, so one resource's table lists its
    rows in the same order however the counts arrived.
    """
    return sorted(
        ((cell, count) for cell, count in grid.items() if count),
        key=lambda item: (-item[1], item[0]))


def _renders_as_table(grid: dict[tuple[int, int], int]) -> bool:
    """Whether these complex cells render as a table, not as a heatmap.

    The ONE statement of the choice: the info page asks it through
    :attr:`AlleleDisplay.complex_grid_renders_as_table` and
    :func:`save_allele_statistics` asks it directly, and a second
    spelling would cost either an image written for a page that renders
    a table or a page pointing at an image that was never written.
    """
    return len(_occupied_cells(grid)) <= COMPLEX_GRID_TABLE_MAX_CELLS


class AlleleCounts(NamedTuple):
    """One chromosome's -- or a whole resource's -- allele counts.

    ``class_counts`` is keyed by the class names in :data:`CLASS_NAMES`
    and sums to ``allele_count``: every row classifies, ``other``
    absorbing what does not parse as alleles.

    ``substitution_matrix`` is keyed by the ref/alt cells in
    :data:`MATRIX_CELLS`, upper-cased, and sums to the ``substitution``
    class count: a cell holds the rows whose pair classifies as a
    substitution of that cell, and nothing else lands in any cell.  It
    is ``None`` -- data unknown, distinct from a matrix of zeros --
    when restored from a file written before the matrix existed.
    """

    allele_count: int
    covered_positions: int
    class_counts: dict[str, int]
    substitution_matrix: dict[tuple[str, str], int] | None
    insertion_lengths: list[int] | None = None
    deletion_lengths: list[int] | None = None
    complex_grid: dict[tuple[int, int], int] | None = None

    def display(self) -> AlleleDisplay | None:
        """This entry's render payload, ``None`` if EVERY group is unknown.

        One seam for the whole Alleles section, with the groups
        independently optional inside it: collapsing the payload the
        moment any single group is missing would hide the groups a file
        does carry, and the rollout guarantees no particular
        combination.  A genuinely empty group still renders -- zeros
        for the matrix, an empty grid for the complex cells -- which is
        why "unknown" and "empty" are different answers here.

        Everything derived is computed here, at render time, and never
        stored: transitions are the four ``A<->G`` / ``C<->T`` cells and
        transversions the eight remaining OFF-DIAGONAL cells -- never
        "substitutions minus transitions", which would silently count
        the diagonal's identity rows as transversions.
        """
        matrix = self.substitution_matrix
        if matrix is None and self.insertion_lengths is None \
                and self.deletion_lengths is None \
                and self.complex_grid is None:
            return None
        transitions: int | None = None
        transversions: int | None = None
        ts_tv: float | None = None
        if matrix is not None:
            transitions = sum(
                count for cell, count in matrix.items()
                if cell in _TRANSITION_CELLS)
            transversions = sum(
                count for (ref, alt), count in matrix.items()
                if ref != alt and (ref, alt) not in _TRANSITION_CELLS)
            ts_tv = transitions / transversions if transversions else None
        return AlleleDisplay(
            None if matrix is None else dict(matrix),
            transitions,
            transversions,
            ts_tv,
            None if matrix is None
            else percentages_over(matrix, sum(matrix.values())),
            None if self.insertion_lengths is None
            else list(self.insertion_lengths),
            None if self.deletion_lengths is None
            else list(self.deletion_lengths),
            None if self.complex_grid is None else dict(self.complex_grid))


def _merged_matrix(
    left: dict[tuple[str, str], int] | None,
    right: dict[tuple[str, str], int] | None,
) -> dict[tuple[str, str], int] | None:
    """The elementwise sum of two matrices, unknown if either is.

    The one statement of the all-or-nothing rule -- as the coverage
    segments roll up: a total over a partially-unknown set would
    silently understate, so an unknown side makes the whole merge
    unknown rather than a smaller number.
    """
    if left is None or right is None:
        return None
    return {cell: left[cell] + right[cell] for cell in MATRIX_CELLS}


def _merged_bins(
    left: list[int] | None,
    right: list[int] | None,
) -> list[int] | None:
    """The binwise sum of two length histograms, unknown if either is.

    The :func:`_merged_matrix` rule on the fixed ladder: an unknown side
    makes the whole merge unknown rather than a smaller number.
    """
    if left is None or right is None:
        return None
    merged = list(left)
    accumulate_bins(merged, right)
    return merged


def _total(counts: Iterable[AlleleCounts]) -> AlleleCounts:
    """The roll-up of several chromosomes' counts into one.

    The one statement of what the ``global`` entry IS -- read when the
    statistic is serialized and when one is asked for its global counts,
    which is why that entry is never read back from the file.
    """
    class_counts = dict.fromkeys(CLASS_NAMES, 0)
    allele_count = 0
    covered = 0
    matrix: dict[tuple[str, str], int] | None = dict.fromkeys(
        MATRIX_CELLS, 0)
    insertions: list[int] | None = [0] * LENGTH_HISTOGRAM_BIN_COUNT
    deletions: list[int] | None = [0] * LENGTH_HISTOGRAM_BIN_COUNT
    grid: dict[tuple[int, int], int] | None = {}
    for entry in counts:
        allele_count += entry.allele_count
        covered += entry.covered_positions
        for name, count in entry.class_counts.items():
            class_counts[name] = class_counts.get(name, 0) + count
        matrix = _merged_matrix(matrix, entry.substitution_matrix)
        insertions = _merged_bins(insertions, entry.insertion_lengths)
        deletions = _merged_bins(deletions, entry.deletion_lengths)
        grid = _merged_grid(grid, entry.complex_grid)
    return AlleleCounts(
        allele_count, covered, class_counts, matrix,
        insertions, deletions, grid)


def _serialized(counts: AlleleCounts) -> dict[str, Any]:
    """One entry's JSON shape, the matrix nested ref -> alt -> count.

    The keys follow :data:`NUCLEOTIDES` order, so the file is
    byte-identical however the rows arrived.  An unknown matrix is
    OMITTED rather than written empty: absent must stay distinguishable
    from a genuine matrix of zeros.
    """
    entry: dict[str, Any] = {
        "allele_count": counts.allele_count,
        "covered_positions": counts.covered_positions,
        "class_counts": counts.class_counts,
    }
    if counts.substitution_matrix is not None:
        matrix = counts.substitution_matrix
        entry["substitution_matrix"] = {
            ref: {alt: matrix[ref, alt] for alt in NUCLEOTIDES}
            for ref in NUCLEOTIDES
        }
    if counts.insertion_lengths is not None:
        entry["insertion_length_histogram"] = list(counts.insertion_lengths)
    if counts.deletion_lengths is not None:
        entry["deletion_length_histogram"] = list(counts.deletion_lengths)
    if counts.complex_grid is not None:
        # Written ref-then-alt SORTED, not in encounter order: the cells
        # are a sparse dict, and two chunkings of one resource meet the
        # same pairs in different orders.  Sorting is what makes the
        # file byte-identical however the rows arrived.
        grid = counts.complex_grid
        nested: dict[str, dict[str, int]] = {}
        for ref_length, alt_length in sorted(grid):
            nested.setdefault(str(ref_length), {})[str(alt_length)] = \
                grid[ref_length, alt_length]
        entry["complex_grid"] = nested
    return entry


def _deserialized_grid(
    entry: dict[str, Any],
) -> dict[tuple[int, int], int] | None:
    """The stored complex grid back onto its cell keys, ``None`` absent."""
    stored = entry.get("complex_grid")
    if stored is None:
        return None
    return {
        (int(ref_length), int(alt_length)): int(count)
        for ref_length, row in stored.items()
        for alt_length, count in row.items()
    }


def _merged_grid(
    left: dict[tuple[int, int], int] | None,
    right: dict[tuple[int, int], int] | None,
) -> dict[tuple[int, int], int] | None:
    """The cellwise sum of two complex grids, unknown if either is.

    The :func:`_merged_matrix` rule again, over a SPARSE key set: a cell
    either side carries is a cell of the sum.
    """
    if left is None or right is None:
        return None
    merged = dict(left)
    for cell, count in right.items():
        merged[cell] = merged.get(cell, 0) + count
    return merged


def _deserialized_bins(
    entry: dict[str, Any], key: str,
) -> list[int] | None:
    """A stored length histogram, ``None`` when the file carries none."""
    stored = entry.get(key)
    if stored is None:
        return None
    bins = [0] * LENGTH_HISTOGRAM_BIN_COUNT
    accumulate_bins(bins, (int(count) for count in stored))
    return bins


def _deserialized_matrix(
    entry: dict[str, Any],
) -> dict[tuple[str, str], int] | None:
    """The stored matrix back onto its cell keys, ``None`` when absent."""
    stored = entry.get("substitution_matrix")
    if stored is None:
        return None
    return {
        (ref, alt): int(stored[ref][alt])
        for ref, alt in MATRIX_CELLS
    }


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
    :func:`~gain.genomic_resources.genomic_scores.records.clip_span` asked about
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
        # ``None`` only on a region restored from a file that predates
        # the matrix -- a scanned region always carries one, however
        # empty.  The keys are upper-cased into the cells: the scan
        # hands the nucleotides over RAW, and a matrix keyed on ``a``
        # would silently drop soft-masked rows no cell claims.
        self._substitution_matrix: dict[tuple[str, str], int] | None = \
            dict.fromkeys(MATRIX_CELLS, 0)
        self._insertion_lengths: list[int] | None = \
            [0] * LENGTH_HISTOGRAM_BIN_COUNT
        self._deletion_lengths: list[int] | None = \
            [0] * LENGTH_HISTOGRAM_BIN_COUNT
        self._complex_grid: dict[tuple[int, int], int] | None = {}
        self._last_pos: int | None = None

    @classmethod
    def frozen(
        cls,
        chrom: str,
        allele_count: int,
        covered_positions: int,
        class_counts: dict[str, int],
        *,
        substitution_matrix: dict[tuple[str, str], int] | None = None,
        insertion_lengths: list[int] | None = None,
        deletion_lengths: list[int] | None = None,
        complex_grid: dict[tuple[int, int], int] | None = None,
    ) -> RegionAlleles:
        """A region restored from serialized counts, with no scan state.

        ``substitution_matrix`` is ``None`` for a file written before
        the matrix existed -- data unknown, not a matrix of zeros.
        """
        region = cls(chrom, None, None)
        region.allele_count = allele_count
        region.covered_positions = covered_positions
        region._class_counts = {
            name: class_counts.get(name, 0) for name in CLASS_NAMES}
        region._substitution_matrix = None \
            if substitution_matrix is None else {
                cell: substitution_matrix.get(cell, 0)
                for cell in MATRIX_CELLS}
        region._insertion_lengths = insertion_lengths
        region._deletion_lengths = deletion_lengths
        region._complex_grid = complex_grid
        return region

    def counts(self) -> AlleleCounts:
        """This region's counts, class map keyed by class name."""
        return AlleleCounts(
            self.allele_count,
            self.covered_positions,
            dict(self._class_counts),
            None if self._substitution_matrix is None
            else dict(self._substitution_matrix),
            None if self._insertion_lengths is None
            else list(self._insertion_lengths),
            None if self._deletion_lengths is None
            else list(self._deletion_lengths),
            None if self._complex_grid is None
            else dict(self._complex_grid))

    def _owns(self, pos: int) -> bool:
        """Whether this region owns the row sitting at ``pos``."""
        return clip_span(pos, pos, self.start, self.end) is not None

    def _count_position(self, pos: int) -> None:
        if pos != self._last_pos:
            self.covered_positions += 1
            self._last_pos = pos

    def _count_pair(
        self, ref: str | None, alt: str | None, multiplicity: int,
    ) -> None:
        """Fold ``multiplicity`` rows of one ref/alt pair into the tallies.

        The one statement of what lands in the substitution matrix:
        exactly the pairs the classifier calls substitutions -- the
        identity pairs included -- keyed upper-cased, so a soft-masked
        ``a>g`` lands in the cell of the base it masks and the matrix
        total stays the ``substitution`` class count.
        """
        classification = classify_allele(ref, alt)
        self._class_counts[
            classification.allele_class.value] += multiplicity
        if classification.allele_class is AlleleClass.SUBSTITUTION \
                and self._substitution_matrix is not None:
            assert ref is not None
            assert alt is not None
            self._substitution_matrix[
                ref.upper(), alt.upper()] += multiplicity
        if classification.allele_class is AlleleClass.INSERTION \
                and self._insertion_lengths is not None:
            assert classification.length_change is not None
            self._insertion_lengths[length_histogram_bin_index(
                abs(classification.length_change))] += multiplicity
        if classification.allele_class is AlleleClass.DELETION \
                and self._deletion_lengths is not None:
            assert classification.length_change is not None
            self._deletion_lengths[length_histogram_bin_index(
                abs(classification.length_change))] += multiplicity
        if classification.allele_class is AlleleClass.COMPLEX \
                and self._complex_grid is not None:
            assert classification.ref_length is not None
            assert classification.alt_length is not None
            cell = (
                min(classification.ref_length, COMPLEX_LENGTH_CLAMP),
                min(classification.alt_length, COMPLEX_LENGTH_CLAMP))
            self._complex_grid[cell] = \
                self._complex_grid.get(cell, 0) + multiplicity

    def add_allele(
        self, pos: int, ref: str | None, alt: str | None,
    ) -> None:
        """Fold one row, read as the point at ``pos``."""
        if not self._owns(pos):
            return
        self.allele_count += 1
        self._count_position(pos)
        self._count_pair(ref, alt, 1)

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
            self._count_pair(*pair, multiplicity)

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
        self._substitution_matrix = _merged_matrix(
            self._substitution_matrix, other._substitution_matrix)
        self._insertion_lengths = _merged_bins(
            self._insertion_lengths, other._insertion_lengths)
        self._deletion_lengths = _merged_bins(
            self._deletion_lengths, other._deletion_lengths)
        self._complex_grid = _merged_grid(
            self._complex_grid, other._complex_grid)
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
            "Allele counts, covered positions, class totals, the "
            "substitution matrix, the indel length histograms and the "
            "complex grid")
        self._regions: dict[str, RegionAlleles] = {}

    def fold_region(self, region: RegionAlleles) -> None:
        """Fold one region's counts in, keyed by its chromosome."""
        held = self._regions.get(region.chrom)
        if held is None:
            self._regions[region.chrom] = region
        else:
            held.merge(region)

    def by_chromosome(self) -> dict[str, AlleleCounts]:
        """The per-chromosome counts, in natural chromosome order.

        Ordered here rather than downstream because this order REACHES
        the info page: :func:`build_allele_section_display` turns these
        entries into the Alleles table's rows as they come, and does
        not re-sort them the way its coverage and fragment siblings
        sort theirs.  What it replaces
        is not arrival order but the plain string sort
        :func:`regions_in_genomic_order` applies before the fold --
        exactly the order iossifovlab/gain#983 calls wrong.

        :meth:`serialize` reads it too, so the order reaches
        ``statistics/alleles.json``.  Nothing downstream reads that
        file positionally: :meth:`deserialize` and :func:`_total` are
        both order-blind, and ``calc_statistics_hash`` hashes the config
        and the source files, never the statistics content.
        """
        return {
            chrom: self._regions[chrom].counts()
            for chrom in sorted(self._regions, key=natural_chromosome_key)
        }

    def global_counts(self) -> AlleleCounts:
        """The roll-up over every chromosome.

        Off the regions directly rather than through
        :meth:`by_chromosome`: ``_total`` is order-blind, so the
        ordering that accessor does would be paid and thrown away.
        """
        return _total(region.counts() for region in self._regions.values())

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
                chrom: _serialized(counts)
                for chrom, counts in chromosomes.items()
            },
            "global": _serialized(_total(chromosomes.values())),
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
                substitution_matrix=_deserialized_matrix(counts),
                insertion_lengths=_deserialized_bins(
                    counts, "insertion_length_histogram"),
                deletion_lengths=_deserialized_bins(
                    counts, "deletion_length_histogram"),
                complex_grid=_deserialized_grid(counts),
            ))
        return result


#: The four transition cells: purine to purine and pyrimidine to
#: pyrimidine.  Everything else OFF the diagonal is a transversion; the
#: diagonal's identity pairs are neither.
_TRANSITION_CELLS: frozenset[tuple[str, str]] = frozenset(
    (("A", "G"), ("G", "A"), ("C", "T"), ("T", "C")))


class MatrixCell(NamedTuple):
    """One substitution-matrix cell as the page renders it.

    ``alleles`` is the stored count -- spelled as the column it sits
    under rather than ``count``, which a :class:`tuple` already means
    something else by.  The percentage is its share of the substitution
    class, ``None`` when no denominator resolves, which the page
    renders as no second line rather than as ``0.00%``.
    """

    alleles: int
    percentage: str | None


class AlleleDisplay(NamedTuple):
    """The Alleles section's render payload, one field per stored group.

    Raw cells come from the stored statistic; the derived numbers are
    computed by :meth:`AlleleCounts.display` -- which builds this --
    and never stored, as the coverage display derives its fractions.

    Every group is INDEPENDENTLY optional, because the statistics roll
    out lazily and a resource may have been rebuilt under any one of
    them: a file written between gain#778 and gain#779 carries a matrix
    and no lengths, and must render its matrix rather than losing the
    whole section.  Each of the page's sections therefore asks for its
    own group, and this payload exists at all whenever ANY group is
    known.
    """

    #: ``None`` when the file predates the matrix (gain#778).
    substitution_matrix: dict[tuple[str, str], int] | None
    #: The four ``A<->G`` / ``C<->T`` cells; ``None`` with no matrix.
    transitions: int | None
    #: The eight off-diagonal cells that are not transitions; the
    #: diagonal's identity pairs are neither.  ``None`` with no matrix.
    transversions: int | None
    #: ``None`` when there are no transversions -- the template renders
    #: "not applicable" rather than dividing -- and with no matrix.
    ts_tv: float | None
    #: Each cell's share of the substitution class, formatted by
    #: :func:`percentages_over`.  The denominator is read off the matrix
    #: rather than taken from ``class_counts``: the two are equal by
    #: :class:`AlleleCounts`'s invariant, and dividing the cells by
    #: their own total is what makes the sixteen of them come to
    #: exactly 100%.  ``None`` with no matrix, and with no
    #: substitutions to take a share of.
    substitution_percentages: dict[tuple[str, str], str] | None
    #: The three gain#779 groups, ``None`` when the file predates them.
    #: An empty grid is KNOWN and empty, which is not the same thing.
    insertion_lengths: list[int] | None = None
    deletion_lengths: list[int] | None = None
    complex_grid: dict[tuple[int, int], int] | None = None

    @property
    def nucleotides(self) -> tuple[str, ...]:
        """The matrix's axis labels, in stored order."""
        return NUCLEOTIDES

    def matrix_rows(self) -> list[tuple[str, list[MatrixCell]]]:
        """The matrix as table rows: reference base, then a cell per alt.

        Empty without a matrix; the page gates on the matrix itself
        rather than reading this to find out.  This only pairs each
        stored count with the share :meth:`AlleleCounts.display` already
        computed for it -- nothing is derived here.
        """
        matrix = self.substitution_matrix
        if matrix is None:
            return []
        percentages = self.substitution_percentages
        return [
            (ref, [
                MatrixCell(
                    matrix[ref, alt],
                    None if percentages is None else percentages[ref, alt])
                for alt in NUCLEOTIDES
            ])
            for ref in NUCLEOTIDES
        ]

    @property
    def complex_grid_renders_as_table(self) -> bool:
        """Whether the complex cells render as a table, not a heatmap.

        ``False`` without a grid, which the page never asks: it gates on
        the grid itself first, as it does for the matrix.
        """
        grid = self.complex_grid
        return grid is not None and _renders_as_table(grid)

    def complex_rows(self) -> list[tuple[str, str, int, str]]:
        """The occupied complex cells as table rows, most alleles first.

        A row is reference length, alternative length, alleles and the
        share of the complex class -- the lengths labelled as the
        heatmap's axes label them, so a clamped cell reads ``≥64`` in
        both.  Empty without a grid; the page gates on the grid itself
        rather than reading this to find out.

        The shares come from :func:`percentages_over`, the one rule the
        Alleles section writes a share by, so a rare cell reads ``<0.01%``
        here exactly as it does in the classes column.  Its denominator
        is the grid's own total, which the TOTAL clamp makes exactly the
        complex class count: every complex row lands in one cell, so
        these rows sum to 100%.

        That denominator is zero only when no cell is occupied, and then
        there are no rows to carry a share anyway -- so the helper's "no
        percentage at all" answer and this method's empty result are the
        same answer, and it is returned as one.
        """
        grid = self.complex_grid
        if grid is None:
            return []
        cells = _occupied_cells(grid)
        percentages = percentages_over(
            dict(cells), sum(count for _, count in cells))
        if percentages is None:
            return []
        return [
            (_length_label(ref_length), _length_label(alt_length),
             count, percentages[ref_length, alt_length])
            for (ref_length, alt_length), count in cells
        ]


class AlleleChromosomeRow(NamedTuple):
    """One chromosome's allele counts, as the info page renders them.

    The chromosome is a FIELD rather than a mapping key, so a row is
    self-contained exactly as the coverage and fragment rows are: the
    template reads a row, never a pair it has to keep together.
    """

    chrom: str
    covered_positions: int
    allele_count: int


class AlleleSectionDisplay(NamedTuple):
    """The Alleles section's render payload: the table and its totals.

    Built in the implementation layer, as :class:`CoverageDisplay` and
    :class:`FragmentDisplay` are, so the template renders fields off an
    inert record rather than calling methods on the statistic itself.
    """

    rows: list[AlleleChromosomeRow]
    class_counts: dict[str, int]
    class_percentages: dict[str, str] | None
    """Each class name's share of ``allele_count``, or ``None``.

    Computed HERE rather than on :class:`AlleleDisplay` because the two
    numbers a share needs -- the class counts and the allele total --
    are stored fields that every file carries, while that payload
    collapses when every OPTIONAL group is unknown.  Computing them
    there dropped this column for a file written before the matrix
    (gain#777) whose shares were perfectly resolvable.

    ``None`` when there are no alleles to take a share of, which drops
    the column; :func:`percentages_over` owns that rule.
    """
    detail: AlleleDisplay | None
    """The optional groups: matrix, indel lengths, complex grid.

    ``None`` when the file carries none of them; each group inside it
    stays independently optional, as :class:`AlleleDisplay` explains.
    """

    @property
    def covered_positions(self) -> int:
        """The table's total, summed off the rows it shows.

        Derived rather than stored, as :attr:`CoverageDisplay.
        global_covered` and :attr:`FragmentDisplay.global_fragments`
        are: the counts have one source, so a total cannot drift from
        the rows under it.
        """
        return sum(row.covered_positions for row in self.rows)

    @property
    def allele_count(self) -> int:
        """The allele total, summed off the rows, as above."""
        return sum(row.allele_count for row in self.rows)


def build_allele_section_display(
    statistics: AlleleStatistics,
) -> AlleleSectionDisplay:
    """Turn the stored allele counts into the section's payload.

    The rows are built over :meth:`AlleleStatistics.by_chromosome`
    rather than replacing it: that accessor already orders the
    chromosomes naturally (gain#983) and ``serialize`` reads it too, so
    the ordering has one owner.

    One walk of the regions serves both the rows and the roll-up, as
    :meth:`AlleleStatistics.serialize` does it: ``_total`` is
    order-blind, so folding the ordered entries it already has costs
    nothing over :meth:`AlleleStatistics.global_counts`, which would
    rebuild every entry a second time.
    """
    chromosomes = statistics.by_chromosome()
    global_counts = _total(chromosomes.values())
    return AlleleSectionDisplay(
        [
            AlleleChromosomeRow(
                chrom, counts.covered_positions, counts.allele_count)
            for chrom, counts in chromosomes.items()
        ],
        dict(global_counts.class_counts),
        percentages_over(
            global_counts.class_counts, global_counts.allele_count),
        global_counts.display(),
    )


def region_alleles_for(
    score: GenomicScore,
    chrom: str,
    start: int | None,
    end: int | None,
) -> RegionAlleles | None:
    """A region accumulator for an allele score, ``None`` for other kinds.

    Gated on the BUILT SCORE CLASS rather than the resource type string.

    Until 2026.8.5 that was load-bearing: ``allele_score`` and the
    deprecated ``np_score`` both built an
    :class:`~gain.genomic_resources.genomic_scores.allele.AlleleScore` while
    ``equivalent_resource_types`` aliased neither to the other, so a gate
    written on type strings skipped ``np_score`` silently (gain#777).
    ``np_score`` is gone (gain#920) and only one spelling reaches here now,
    but the class gate stays: it is the property this statistic actually
    depends on -- that the score reads alleles -- and a type-string gate
    would have to be revisited by the next spelling that builds an
    ``AlleleScore``.
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
    :class:`~gain.genomic_resources.genomic_scores.records.AlleleRecordArrays`
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


def plot_complex_grid(
    outfile: IO,
    grid: dict[tuple[int, int], int],
) -> None:
    """Render the complex ``(len_ref, len_alt)`` cells as a heatmap.

    Drawn over the FULL clamped square rather than only the occupied
    cells, so the diagonal -- where the MNVs sit -- is visible as a
    diagonal and an empty region reads as empty rather than as a
    missing axis.  The axes are exact lengths, which is the whole point
    of the cell scheme (gain#779): a 2->3 complex sits one cell off the
    diagonal from a 3bp MNV, and a binned axis would have hidden that.

    Counts span orders of magnitude on real scores, so the COLOUR is
    log-scaled -- the same choice the length histograms make on their
    count axis -- with empty cells left as the background rather than
    coloured as a genuine zero.
    """
    # pylint: disable=import-outside-toplevel
    import matplotlib
    matplotlib.use("agg")
    import matplotlib.colors
    import matplotlib.pyplot as plt

    from gain.genomic_resources.histogram import (
        HISTOGRAM_LABELS_FONT_SIZE,
    )

    side = COMPLEX_LENGTH_CLAMP
    # Lengths are 1-based and the clamp is inclusive, so cell (r, a)
    # sits at index (r - 1, a - 1) of a ``side`` by ``side`` square.
    counts = np.zeros((side, side), dtype=np.int64)
    for (ref_length, alt_length), count in grid.items():
        counts[ref_length - 1, alt_length - 1] = count
    masked = np.ma.masked_equal(counts, 0)

    figure, axes = plt.subplots(figsize=(10, 10))
    image = axes.imshow(
        masked,
        origin="lower",
        extent=(0.5, side + 0.5, 0.5, side + 0.5),
        norm=matplotlib.colors.LogNorm(
            vmin=1, vmax=max(grid.values(), default=1)),
        interpolation="nearest",
        aspect="equal")
    ticks = [1, *range(8, side + 1, 8)]
    # Through the same label the table's clamped rows are written with,
    # so "the picture and the table say the same thing about the same
    # cell" is enforced by the code rather than asserted in a comment:
    # the last tick IS the clamp, and only that one reads with the sign.
    labels = [_length_label(tick) for tick in ticks]
    for set_ticks, set_labels in (
        (axes.set_xticks, axes.set_xticklabels),
        (axes.set_yticks, axes.set_yticklabels),
    ):
        set_ticks(ticks)
        set_labels(labels, fontsize=HISTOGRAM_LABELS_FONT_SIZE)
    axes.set_xlabel(
        "alternative length (bp)", fontsize=HISTOGRAM_LABELS_FONT_SIZE)
    axes.set_ylabel(
        "reference length (bp)", fontsize=HISTOGRAM_LABELS_FONT_SIZE)
    colorbar = figure.colorbar(image, ax=axes, shrink=0.8)
    colorbar.set_label("alleles", fontsize=HISTOGRAM_LABELS_FONT_SIZE)
    figure.tight_layout()
    figure.savefig(outfile, format="png")
    plt.close(figure)


def save_allele_statistics(
    resource: GenomicResource,
    statistics: AlleleStatistics | None,
) -> None:
    """Write the statistics into the resource, with their global images.

    Laid out like ``save_and_plot_coverage``: the file first, then one
    image per group that has something to draw.  A group the resource
    publishes nothing for writes no image -- the info page's section is
    what says whether that is "not computed" or "genuinely none".

    An EMPTY group is skipped just as an unknown one is, in both twins.
    Every group here applies to every allele score, so plotting the
    empty ones would put an all-zero deletion histogram on each of the
    many scores that carry only substitutions -- and a logarithmic count
    axis cannot draw one at all.  What skipping costs is a previous
    build's image left behind when a group empties out, and nothing
    links the leftover: the page reads the stored counts, not the
    directory.
    """
    if statistics is None:
        return
    with resource.open_raw_file(
            ALLELE_STATISTICS_FILE, mode="wt") as outfile:
        outfile.write(statistics.serialize())
    counts = statistics.global_counts()
    for item, image, lengths in (
        ("insertion", ALLELE_INSERTION_LENGTHS_IMAGE_FILE,
         counts.insertion_lengths),
        ("deletion", ALLELE_DELETION_LENGTHS_IMAGE_FILE,
         counts.deletion_lengths),
    ):
        if not has_counts_to_plot(lengths):
            continue
        with resource.open_raw_file(image, mode="wb") as imagefile:
            plot_length_histogram(imagefile, lengths, item)
    # The same question the page asks: a grid sparse enough to be
    # tabled publishes no image, so writing one would leave a file
    # nothing references (gain#989).
    if counts.complex_grid \
            and not _renders_as_table(counts.complex_grid):
        with resource.open_raw_file(
                ALLELE_COMPLEX_GRID_IMAGE_FILE, mode="wb") as imagefile:
            plot_complex_grid(imagefile, counts.complex_grid)
