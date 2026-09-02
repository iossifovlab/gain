"""The stored form of an indel group: an exact length map plus scalars.

ADR 0020 gives **segments**, **fragments** and **indels** one log2
binning, and until gain#1118 the indel groups stored theirs on it.  They
no longer do.  The ladder lumps {2, 3} into one bin and {4, 5, 6, 7}
into the next, which is exactly where indels live, so no exact minimum,
maximum, mean or median survived it -- and those four are what the
statistics table on the info page exists to show.

The ladder is still what the CHART is drawn on; it is derived from the
map at render time by :func:`indel_length_ladder` rather than stored
beside it, so the picture and the numbers beneath it cannot drift.  For
segments and fragments the ladder remains the stored format.

Split out of :mod:`gain.genomic_resources.statistics.alleles` when the
exact map arrived: the stored format of one allele class, its merge
rule and the statistics derived from it are one subject, and the module
they came from carries four other groups besides.
"""
from __future__ import annotations

from typing import NamedTuple

from gain.genomic_resources.statistics.length_histogram import (
    LENGTH_HISTOGRAM_BIN_COUNT,
    length_histogram_bin_index,
)

#: The longest indel length the stored map resolves exactly.  A length
#: at or above it folds into one overflow bucket keyed by the clamp,
#: which therefore reads "this many bases or more".  The clamp is TOTAL
#: in the sense :data:`COMPLEX_LENGTH_CLAMP` is: every indel lands in
#: exactly one bucket, so the map's values sum to the group's count.
#:
#: Exact lengths rather than the shared log2 ladder (gain#1118).  The
#: ladder's second bin is {2, 3} and its third {4, 5, 6, 7}, which is
#: where indels actually live, so NO exact min, max, mean or median
#: survives it -- and those four are what the statistics table exists to
#: show.  Part of the stored format: it must not change once resources
#: carry maps built from it.
#:
#: It must never fall BELOW :data:`~gain.genomic_resources.statistics.
#: length_histogram.LENGTH_HISTOGRAM_DISPLAY_CAP`, because the chart's
#: bins are derived from this map and a bin between the two would be
#: drawn from lengths the map had already folded away.  They are EQUAL
#: today, which is the tightest the rule allows and is what makes the
#: derived chart identical to the one the stored histograms drew: the
#: plot sums every bin at or above the cap into one overflow bar
#: anyway, so folding at the same length loses nothing it would have
#: drawn separately.  The consequence to know is the other direction --
#: the display cap is documented as free to change, and it is not free
#: to be RAISED any more.  Raising it means raising this first, which
#: is a stored-format change and needs every resource rebuilt.
INDEL_LENGTH_CLAMP = 8192


class IndelLengths(NamedTuple):
    """One indel group's lengths: an exact map and four scalars.

    ``lengths`` maps a length in base pairs to how many alleles have it,
    clamped at :data:`INDEL_LENGTH_CLAMP` -- the key AT the clamp means
    "that long or longer".  The map is what the chart's bins and the
    median are derived from.

    The scalars are what keep the clamp from becoming a lie.
    ``alleles``, ``sum``, ``min`` and ``max`` are all accumulated on the
    UNCLAMPED length, so ``min``, ``max`` and the mean stay exact
    however far the tail runs; only a median landing in the overflow
    bucket degrades, and it says so.  A clamped map alone would
    understate the mean and cap the max -- the one statistic that
    exists to describe the tail.

    They are stored rather than derived even where the map could give
    them, because that is the whole point: ``sum`` cannot be recovered
    from a clamped map at all, and a ``max`` recovered from one would
    read 8192 for a 40,000 bp deletion.  ``alleles`` does equal the
    map's total and is kept beside the other three so the four merge as
    one thing and the file says outright what the mean is over.

    ``min`` and ``max`` are ``None`` exactly when ``alleles`` is zero: a
    group with no alleles has no shortest and no longest, and 0 is not a
    length any indel can have.
    """

    lengths: dict[int, int]
    #: Spelled as the column it sits under rather than ``count``, which
    #: a :class:`tuple` already means something else by -- the same
    #: rename, for the same reason, as :class:`MatrixCell`'s.  The
    #: STORED key stays ``count``, which is what it is in the file.
    alleles: int
    sum: int
    min: int | None
    max: int | None

    @property
    def mean(self) -> float | None:
        """The mean length, exact past the clamp.  ``None`` if empty."""
        if not self.alleles:
            return None
        return self.sum / self.alleles

    def middle_lengths(self) -> tuple[int, int] | None:
        """The one or two lengths the median is taken over.

        Both indices land on the same allele when the count is odd, so
        the pair collapses and the median is that length.  Kept as the
        PAIR rather than folded straight into an average, because
        whether the clamp blunted the answer is a property of these two
        lengths and cannot be read back off their mean -- see
        :attr:`median_is_clamped`.

        The rank arithmetic is the same convention
        :class:`~gain.annotation.aggregators.MedianAggregator` applies
        to a weighted value list, restated here rather than shared: that
        one is a private method on a stateful annotation-layer
        aggregator keyed on its own accumulated values, and reaching it
        would cost more than the six lines it saves.  If a third caller
        ever wants it, the extraction is a free ``weighted_median`` over
        ``(value, count)`` pairs in ``gain.utils``.
        """
        if not self.alleles:
            return None
        lower_index = (self.alleles - 1) // 2
        upper_index = self.alleles // 2
        lower = upper = None
        seen = 0
        for length in sorted(self.lengths):
            seen += self.lengths[length]
            if lower is None and seen > lower_index:
                lower = length
            if seen > upper_index:
                upper = length
                break
        if lower is None or upper is None:
            # Only reachable from a file whose ``count`` disagrees with
            # its map.  Degrade to "no median" rather than crash the
            # whole page render over one malformed group.
            return None
        return lower, upper

    @property
    def median(self) -> float | None:
        """The middle length, or the mean of the middle two if even.

        The standard convention, stated on the ALLELES rather than on
        the distinct lengths: {2, 3} is 2.5, and a group of one 2 and
        nine 3s has a median of 3, not 2.5.

        Read off the map, so it degrades where the map does.  When
        :attr:`median_is_clamped` this is a LOWER BOUND rather than the
        median, and the page renders it as one.
        """
        middle = self.middle_lengths()
        if middle is None:
            return None
        return sum(middle) / 2

    @property
    def has_counts_to_plot(self) -> bool:
        """Whether this group has anything to draw a chart of.

        The indel side of
        :func:`~gain.genomic_resources.statistics.length_histogram.
        has_counts_to_plot`, which asks the same question of the two
        coverage groups still stored on the ladder.  Unknown and
        known-and-empty are one answer for the same reason: the counts
        axis is logarithmic and can render neither.

        One spelling, because the statistics build and the page must
        agree exactly -- a build that skips the image while the page
        links it leaves a dangling thumbnail, and the reverse leaves a
        file nothing references.
        """
        return bool(self.alleles)

    @property
    def median_is_clamped(self) -> bool:
        """Whether either middle allele fell in the overflow bucket.

        The one statistic here the clamp can blunt, so it is asked
        outright rather than left to a reader to notice that a suspicious
        number is suspicious.

        Asked of the two middle LENGTHS, never of their average, which
        is the distinction a first cut got wrong.  Every key in the map
        is at most the clamp, so an average reaching it means BOTH
        middles were in the overflow bucket; a group with one middle
        below the clamp and one above averages to something under it and
        would have been published as though exact.  On a group of one
        1 bp and one 40,000 bp deletion that reads 4096.5, where the
        truth is 20,000.5 -- a fabricated number beside three exact ones.

        Since the clamped side is a floor, the average is a floor too:
        the true median is at least what :attr:`median` computes,
        whichever of the two middles was clamped.
        """
        middle = self.middle_lengths()
        return middle is not None and middle[1] >= INDEL_LENGTH_CLAMP


#: An indel group that was scanned and holds nothing -- distinct from a
#: group that was never scanned, which is ``None``.
#:
#: Its map is shared, so nothing may mutate it.  Nothing does: it is the
#: identity the roll-up starts from, and :func:`merged_indels` copies
#: both sides through :meth:`IndelTally.restored` rather than folding
#: into either.  Add a path that mutates a group in place and it must
#: start from ``IndelTally()``, not from this.
NO_INDELS = IndelLengths({}, 0, 0, None, None)


def indel_length_ladder(lengths: IndelLengths) -> list[int]:
    """The exact map binned onto the shared log2 ladder, for the chart.

    Derived at render time rather than stored (gain#1118), so the chart
    and the statistics beside it cannot drift: there is one source of
    truth and the picture is a view of it.

    The result is identical to the histogram this replaced, not merely
    close.  ``plot_length_histogram`` sums every bin at or above its
    display cap into a single overflow bar, and
    :data:`INDEL_LENGTH_CLAMP` equals that cap -- so the lengths the map
    folded together are exactly the ones the chart was going to add up
    anyway.  Below the cap the binning is the same function on the same
    lengths.
    """
    bins = [0] * LENGTH_HISTOGRAM_BIN_COUNT
    for length, count in lengths.lengths.items():
        bins[length_histogram_bin_index(length)] += count
    return bins


class IndelTally:
    """A mutable indel group, accumulated row by row.

    The scan's counterpart to :class:`IndelLengths`, which is what a
    region hands out.  Separate because the map is updated in place: a
    tuple rebuilt per row would copy the whole map each time, which on
    a resource whose deletions run to thousands of distinct lengths is
    quadratic in the lengths seen.
    """

    def __init__(self) -> None:
        self.lengths: dict[int, int] = {}
        self.alleles = 0
        self.sum = 0
        self.min: int | None = None
        self.max: int | None = None

    @classmethod
    def restored(cls, lengths: IndelLengths) -> IndelTally:
        """A tally holding what a stored group already counted."""
        tally = cls()
        tally.lengths = dict(lengths.lengths)
        tally.alleles = lengths.alleles
        tally.sum = lengths.sum
        tally.min = lengths.min
        tally.max = lengths.max
        return tally

    def merge(self, other: IndelTally) -> None:
        """Fold another group of the same kind into this one.

        The ONE statement of how two indel groups come together, so the
        region merge and the global roll-up cannot drift: maps add per
        length, ``count`` and ``sum`` add, and the extremes take the
        extreme.  No re-clamping -- both maps are already keyed on
        clamped lengths, while ``min``/``max`` are exact on both sides
        and stay exact here.
        """
        for length, count in other.lengths.items():
            self.lengths[length] = self.lengths.get(length, 0) + count
        self.alleles += other.alleles
        self.sum += other.sum
        if other.min is not None:
            self.min = other.min if self.min is None \
                else min(self.min, other.min)
        if other.max is not None:
            self.max = other.max if self.max is None \
                else max(self.max, other.max)

    def add(self, length: int, multiplicity: int) -> None:
        """Fold ``multiplicity`` alleles of one exact length in.

        Written out longhand rather than with ``min()``/``max()``,
        which is not style: this runs once per DISTINCT ref/alt pair
        per scanned batch, and the two builtin calls measured 158ns of
        the 258ns this method had added over the fixed-bin increment it
        replaced.  Comparing in place brings it to ~20ns over.  An
        all-substitution score never reaches here at all -- the class
        gate above decides that -- so the cost lands only on indel rows.
        """
        bucket = min(INDEL_LENGTH_CLAMP, length)
        self.lengths[bucket] = self.lengths.get(bucket, 0) + multiplicity
        self.alleles += multiplicity
        # On the UNCLAMPED length, which is what keeps these exact.
        self.sum += length * multiplicity
        smallest = self.min
        if smallest is None or length < smallest:
            self.min = length
        largest = self.max
        if largest is None or length > largest:
            self.max = length

    def frozen(self) -> IndelLengths:
        """This group as the inert record a region hands out."""
        return IndelLengths(
            dict(self.lengths), self.alleles, self.sum, self.min, self.max)


def merged_indels(
    left: IndelLengths | None,
    right: IndelLengths | None,
) -> IndelLengths | None:
    """The sum of two indel groups, unknown if either is.

    The :func:`_merged_matrix` rule over the exact length maps: an
    unknown side makes the whole merge unknown rather than a smaller
    number.  The arithmetic itself is :meth:`IndelTally.merge`, so the
    roll-up here and the region merge in the scan are one rule.
    """
    if left is None or right is None:
        return None
    tally = IndelTally.restored(left)
    tally.merge(IndelTally.restored(right))
    return tally.frozen()


def merged_tallies(
    left: IndelTally | None,
    right: IndelTally | None,
) -> IndelTally | None:
    """The same rule between two SCANNED groups, folded left in place."""
    if left is None or right is None:
        return None
    left.merge(right)
    return left


def _trimmed(value: float) -> str:
    """A length to two decimals, with trailing zeros trimmed off.

    A median of exactly 2 reads ``2`` rather than ``2.00``: these are
    base-pair counts, and most of them land on whole numbers -- an even
    allele count straddling two lengths is the only thing that puts a
    half there.  Two decimals is what the shares on this page use, so a
    mean of 2.33 reads at the same resolution as a 2.33% share.
    """
    return f"{value:.2f}".rstrip("0").rstrip(".")


class IndelStatisticsRow(NamedTuple):
    """One indel group as the statistics table renders it.

    Every field is already TEXT.  The table shows five different kinds
    of number -- an allele count, two exact lengths and two averages,
    one of which can be a floor -- and formatting each where it is
    built keeps the template free of a second rule about how a length
    is written.
    """

    group: str
    alleles: str
    min: str
    max: str
    mean: str
    median: str

    @classmethod
    def of(cls, group: str, lengths: IndelLengths) -> IndelStatisticsRow:
        """One group's row; the LENGTH cells empty when it holds none.

        A group that was scanned and found nothing has a genuine count
        of zero, which renders as ``0`` -- that is the answer, not a
        missing one.  What it has no answer for is a shortest, longest
        or average length, so those four render empty rather than as
        zeros, which would read as indels of length nothing.
        """
        mean = lengths.mean
        # The middle pair read ONCE, not once through each property:
        # both walk a sorted copy of the map, which at the clamp's
        # 8192 distinct keys is ~0.9ms a walk.
        middle = lengths.middle_lengths()
        median = None if middle is None else sum(middle) / 2
        is_clamped = middle is not None and middle[1] >= INDEL_LENGTH_CLAMP
        return cls(
            group,
            str(lengths.alleles),
            "" if lengths.min is None else str(lengths.min),
            "" if lengths.max is None else str(lengths.max),
            "" if mean is None else _trimmed(mean),
            # The one cell the clamp can blunt: past it the map knows
            # only "this long or longer", so the median is written as
            # the floor it is -- with the sign the complex grid's
            # clamped cells already use, so one page says one thing one
            # way.  min, max and mean are exact from the scalars and
            # need no such hedge.
            #
            # The floor is the COMPUTED value, not the clamp.  With both
            # middles in the overflow bucket the two coincide and this
            # reads "≥8192"; with only the upper one clamped the average
            # is smaller, and it is still a true lower bound -- the
            # clamped side can only be longer.  Printing the clamp there
            # would overstate a median that is genuinely below it.
            "" if median is None
            else f"≥{_trimmed(median)}" if is_clamped
            else _trimmed(median),
        )
