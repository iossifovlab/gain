"""A length statistic's exact record: stored, folded, read and charted.

ADR 0020 gave **segments**, **fragments** and **indels** one log2
binning for their length histograms.  The ladder lumps {2, 3} into one
bin and {4, 5, 6, 7} into the next, so no exact minimum, maximum, mean
or median survives it -- and those four are what the statistics table
on an info page exists to show.  So each statistic stores an
:class:`ExactLengths` record instead: an exact ``{length: count}`` map
clamped at :data:`LENGTH_MAP_CLAMP`, beside four scalars accumulated on
the unclamped length.

The ladder is still what the CHART is drawn on; it is derived from the
map at render time by :func:`length_ladder` rather than stored beside
it, so the picture and the numbers beneath it cannot drift.

Nothing here knows what KIND of thing has a length.  The indel groups of
an allele score were the first users (gain#1118), a position score's
segments the second (gain#1543) and a fragment score's fragments the
third (gain#1544).  The record's stored key for the count is ``count``
whatever the kind, and the row formatter takes the group label from its
caller, so "alleles", "segments" and "fragments" all fit.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, NamedTuple

import numpy as np

from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.statistics.length_histogram import (
    LENGTH_HISTOGRAM_BIN_COUNT,
    length_histogram_bin_index,
    plot_length_histogram,
)

#: The longest length the stored map resolves exactly.  A length at or
#: above it folds into one overflow bucket keyed by the clamp, which
#: therefore reads "this many bases or more".  The clamp is TOTAL in the
#: sense :data:`COMPLEX_LENGTH_CLAMP` is: every length lands in exactly
#: one bucket, so the map's values sum to the record's count.
#:
#: Part of the stored format: it must not change once resources carry
#: maps built from it.
#:
#: It must never fall BELOW :data:`~gain.genomic_resources.statistics.
#: length_histogram.LENGTH_HISTOGRAM_DISPLAY_CAP`, because the chart's
#: bins are derived from this map and a bin between the two would be
#: drawn from lengths the map had already folded away.  They are EQUAL,
#: which is the tightest the rule allows and is what makes the derived
#: chart identical to one drawn from a stored ladder: the plot sums every
#: bin at or above the cap into one overflow bar anyway, so folding at
#: the same length loses nothing it would have drawn separately.  The
#: consequence to know is the other direction -- the display cap is
#: documented as free to change, and it is not free to be RAISED.
#: Raising it means raising this first, which is a stored-format change
#: and needs every resource rebuilt.
LENGTH_MAP_CLAMP = 8192


class ExactLengths(NamedTuple):
    """One group's lengths: an exact map and four scalars.

    ``lengths`` maps a length in base pairs to how many items have it,
    clamped at :data:`LENGTH_MAP_CLAMP` -- the key AT the clamp means
    "that long or longer".  The map is what the chart's bins and the
    median are derived from.

    The scalars are what keep the clamp from becoming a lie.  ``total``,
    ``sum``, ``min`` and ``max`` are all accumulated on the UNCLAMPED
    length, so ``min``, ``max`` and the mean stay exact however far the
    tail runs; only a median landing in the overflow bucket degrades,
    and it says so.  A clamped map alone would understate the mean and
    cap the max -- the one statistic that exists to describe the tail.

    They are stored rather than derived even where the map could give
    them, because that is the whole point: ``sum`` cannot be recovered
    from a clamped map at all, and a ``max`` recovered from one would
    read 8192 for a 40,000 bp deletion.  ``total`` does equal the map's
    total and is kept beside the other three so the four merge as one
    thing and the file says outright what the mean is over.

    ``min`` and ``max`` are ``None`` exactly when ``total`` is zero: a
    group with no items has no shortest and no longest, and 0 is not a
    length anything here can have.
    """

    lengths: dict[int, int]
    #: How many items the map counts.  Spelled ``total`` rather than
    #: ``count``, which a :class:`tuple` already means something else
    #: by -- the reason :class:`MatrixCell` spells its count ``alleles``;
    #: a record that does not know what it counts needs a neutral word.
    #: The STORED key is ``count``, which is what it is in the file.
    total: int
    sum: int
    min: int | None
    max: int | None

    @property
    def mean(self) -> float | None:
        """The mean length, exact past the clamp.  ``None`` if empty."""
        if not self.total:
            return None
        return self.sum / self.total

    def middle_lengths(self) -> tuple[int, int] | None:
        """The one or two lengths the median is taken over.

        Both indices land on the same item when the count is odd, so
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
        if not self.total:
            return None
        lower_index = (self.total - 1) // 2
        upper_index = self.total // 2
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

        The standard convention, stated on the ITEMS rather than on the
        distinct lengths: {2, 3} is 2.5, and a group of one 2 and nine
        3s has a median of 3, not 2.5.

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

        Asked of a KNOWN record; an unknown group is the callers' ``None``
        and gets the same answer, because the counts axis is logarithmic
        and can render neither, and a chart of nothing under a lengths
        heading states nothing either.

        One spelling, because the statistics build and the page must
        agree exactly -- a build that skips the image while the page
        links it leaves a dangling thumbnail, and the reverse leaves a
        file nothing references.
        """
        return bool(self.total)

    def stored(self) -> dict[str, Any]:
        """This record as the statistics file writes it.

        Keys SORTED and written as strings: the map is sparse and two
        chunkings of one resource meet its lengths in different orders,
        so sorting is what makes the file byte-identical however the
        rows arrived.  The count is written under ``count``.
        """
        return {
            "lengths": {
                str(length): self.lengths[length]
                for length in sorted(self.lengths)
            },
            "count": self.total,
            "sum": self.sum,
            "min": self.min,
            "max": self.max,
        }

    @classmethod
    def from_stored(cls, stored: dict[str, Any]) -> ExactLengths:
        """The record a statistics file holds under one group's key.

        ``min`` and ``max`` are read tolerantly and the other three are
        not, which is deliberate rather than sloppy: those two are
        legitimately ``null`` for a group that was scanned and found
        nothing, so absent and null must read alike.  A group missing
        its map, count or sum is a malformed file, and raising names it.
        """
        minimum = stored.get("min")
        maximum = stored.get("max")
        return cls(
            {
                int(length): int(count)
                for length, count in stored["lengths"].items()
            },
            int(stored["count"]),
            int(stored["sum"]),
            None if minimum is None else int(minimum),
            None if maximum is None else int(maximum),
        )

    @property
    def median_is_clamped(self) -> bool:
        """Whether either middle item fell in the overflow bucket.

        The one statistic here the clamp can blunt, so it is asked
        outright rather than left to a reader to notice that a suspicious
        number is suspicious.

        Asked of the two middle LENGTHS, never of their average.  Every
        key in the map is at most the clamp, so an average reaching it
        means BOTH middles were in the overflow bucket; a group with one
        middle below the clamp and one above averages to something under
        it and would otherwise be published as though exact.  On a group
        of one 1 bp and one 40,000 bp deletion that reads 4096.5, where
        the truth is 20,000.5 -- a fabricated number beside three exact
        ones.

        Since the clamped side is a floor, the average is a floor too:
        the true median is at least what :attr:`median` computes,
        whichever of the two middles was clamped.
        """
        middle = self.middle_lengths()
        return middle is not None and middle[1] >= LENGTH_MAP_CLAMP


#: A group that was scanned and holds nothing -- distinct from a group
#: that was never scanned, which is ``None``.
#:
#: Its map is shared, so nothing may mutate it.  Nothing does: it is the
#: identity a roll-up starts from, and :func:`merged_lengths` copies
#: both sides through :meth:`LengthTally.restored` rather than folding
#: into either.  Add a path that mutates a group in place and it must
#: start from ``LengthTally()``, not from this.
NO_LENGTHS = ExactLengths({}, 0, 0, None, None)


def length_ladder(lengths: ExactLengths) -> list[int]:
    """The exact map binned onto the shared log2 ladder, for the chart.

    Derived at render time rather than stored, so the chart and the
    statistics beside it cannot drift: there is one source of truth and
    the picture is a view of it.

    The result is identical to a histogram stored on the ladder, not
    merely close.  ``plot_length_histogram`` sums every bin at or above
    its display cap into a single overflow bar, and
    :data:`LENGTH_MAP_CLAMP` equals that cap -- so the lengths the map
    folded together are exactly the ones the chart was going to add up
    anyway.  Below the cap the binning is the same function on the same
    lengths.
    """
    bins = [0] * LENGTH_HISTOGRAM_BIN_COUNT
    for length, count in lengths.lengths.items():
        bins[length_histogram_bin_index(length)] += count
    return bins


class LengthTally:
    """A mutable group of lengths, accumulated one distinct length at a time.

    The scan's counterpart to :class:`ExactLengths`, which is what a
    region hands out.  Separate because the map is updated in place: a
    tuple rebuilt per row would copy the whole map each time, which on
    a resource whose lengths run to thousands of distinct values is
    quadratic in the lengths seen.

    Dict-backed, so its cost is in the DISTINCT lengths seen, not in
    the clamp: right for a statistic that meets few distinct lengths
    per batch and folds each with its multiplicity.  A statistic that
    meets its lengths as whole arrays wants :class:`LengthArrayTally`.
    """

    def __init__(self) -> None:
        self.lengths: dict[int, int] = {}
        self.total = 0
        self.sum = 0
        self.min: int | None = None
        self.max: int | None = None

    @classmethod
    def restored(cls, lengths: ExactLengths) -> LengthTally:
        """A tally holding what a stored group already counted."""
        tally = cls()
        tally.lengths = dict(lengths.lengths)
        tally.total = lengths.total
        tally.sum = lengths.sum
        tally.min = lengths.min
        tally.max = lengths.max
        return tally

    def merge(self, other: LengthTally) -> None:
        """Fold another group of the same kind into this one.

        The ONE statement of how two groups come together, so the
        region merge and the global roll-up cannot drift: maps add per
        length, ``total`` and ``sum`` add, and the extremes take the
        extreme.  No re-clamping -- both maps are already keyed on
        clamped lengths, while ``min``/``max`` are exact on both sides
        and stay exact here.
        """
        for length, count in other.lengths.items():
            self.lengths[length] = self.lengths.get(length, 0) + count
        self.total += other.total
        self.sum += other.sum
        self.min, self.max = _merged_extremes(self, other)

    def add(self, length: int, multiplicity: int) -> None:
        """Fold ``multiplicity`` items of one exact length in.

        Written out longhand rather than with ``min()``/``max()``,
        which is not style: this runs once per DISTINCT length per
        scanned batch, and the two builtin calls measured 158ns of the
        258ns this method had added over a fixed-bin increment.
        Comparing in place brings it to ~20ns over.
        """
        bucket = min(LENGTH_MAP_CLAMP, length)
        self.lengths[bucket] = self.lengths.get(bucket, 0) + multiplicity
        self.total += multiplicity
        # On the UNCLAMPED length, which is what keeps these exact.
        self.sum += length * multiplicity
        smallest = self.min
        if smallest is None or length < smallest:
            self.min = length
        largest = self.max
        if largest is None or length > largest:
            self.max = length

    def frozen(self) -> ExactLengths:
        """This group as the inert record a region hands out."""
        return ExactLengths(
            dict(self.lengths), self.total, self.sum, self.min, self.max)


class LengthArrayTally:
    """A mutable group of lengths, accumulated a whole batch at a time.

    The other scan-side counterpart to :class:`ExactLengths`, for a
    statistic that meets its lengths as column arrays and in numbers
    the dict tally cannot afford -- a position score's segments run to
    billions.  One ``int64`` array of ``LENGTH_MAP_CLAMP + 1`` counters
    stands in for the map, so a batch folds in with one ``bincount``
    and the cost is bounded by the clamp, not by the run count.  The
    map appears only when the group is frozen, sparse and with plain
    Python ints, so a record from here is indistinguishable from one
    the dict tally froze.

    ``total``, ``sum``, ``min`` and ``max`` are the same four scalars
    the dict tally keeps, accumulated on the UNCLAMPED batch.
    """

    def __init__(self) -> None:
        self._counts = np.zeros(LENGTH_MAP_CLAMP + 1, dtype=np.int64)
        self.total = 0
        self.sum = 0
        self.min: int | None = None
        self.max: int | None = None

    def add(self, length: int) -> None:
        """Fold one length in, with no numpy vector call.

        The scalar twin of :meth:`add_batch`, for the lengths a caller
        meets one at a time -- the per-record scan's runs, and the runs
        a region can only measure once its neighbours are known.  It
        runs once per segment, and a position score's segments run to
        billions: a vector call here, even a one-element ``bincount``,
        would cost microseconds where a counter increment costs a few
        hundred nanoseconds.  The extremes are updated longhand for the
        reason :meth:`LengthTally.add` gives.

        No check that ``length`` is positive, again like the dict
        tally: a run's length is ``end - begin + 1`` over a row span, at
        least 1 by construction, and the check belongs where lengths
        arrive from outside -- :meth:`add_batch`.
        """
        self._counts[min(length, LENGTH_MAP_CLAMP)] += 1
        self.total += 1
        # On the UNCLAMPED length, which is what keeps these exact.
        self.sum += length
        smallest = self.min
        if smallest is None or length < smallest:
            self.min = length
        largest = self.max
        if largest is None or length > largest:
            self.max = length

    def add_batch(self, lengths: np.ndarray) -> None:
        """Fold a whole batch of lengths in.

        ``lengths`` is an integer array of any width; the fold widens to
        ``int64`` itself.  Every length must be at least 1: ``bincount``
        would accept a 0 silently and put it in counter 0, which is not
        a length anything can have, so the batch is checked before it
        is folded -- and a refused batch leaves the tally as it was.
        """
        if not lengths.size:
            return
        smallest = int(lengths.min())
        if smallest < 1:
            raise ValueError(f"length must be positive: {smallest}")
        self._counts += np.bincount(
            np.minimum(lengths, LENGTH_MAP_CLAMP),
            minlength=LENGTH_MAP_CLAMP + 1)
        self.total += lengths.size
        # On the UNCLAMPED lengths, which is what keeps these exact.
        self.sum += int(lengths.sum(dtype=np.int64))
        if self.min is None or smallest < self.min:
            self.min = smallest
        largest = int(lengths.max())
        if self.max is None or largest > self.max:
            self.max = largest

    def merge(self, other: LengthArrayTally) -> None:
        """Fold another group of the same kind into this one.

        :meth:`LengthTally.merge` over arrays: the counters add
        elementwise, ``total`` and ``sum`` add, and the extremes take
        the extreme through the same rule.
        """
        self._counts += other._counts
        self.total += other.total
        self.sum += other.sum
        self.min, self.max = _merged_extremes(self, other)

    def frozen(self) -> ExactLengths:
        """This group as the inert record a region hands out.

        The map is SPARSE -- a counter that stayed at zero is no key --
        because that is the map the dict tally builds, and the file
        must not say which tally wrote it.
        """
        populated = np.flatnonzero(self._counts)
        return ExactLengths(
            dict(zip(
                populated.tolist(), self._counts[populated].tolist(),
                strict=True)),
            self.total, self.sum, self.min, self.max)


def _merged_extremes(
    left: LengthTally | LengthArrayTally,
    right: LengthTally | LengthArrayTally,
) -> tuple[int | None, int | None]:
    """The ``min`` and ``max`` of two groups taken together.

    A side that holds nothing has no extremes and contributes none;
    two sides that both hold something take the smaller ``min`` and the
    larger ``max``.  One function for both tallies, so the rule cannot
    drift between them.
    """
    known_mins = [m for m in (left.min, right.min) if m is not None]
    known_maxs = [m for m in (left.max, right.max) if m is not None]
    return (
        min(known_mins) if known_mins else None,
        max(known_maxs) if known_maxs else None,
    )


def merged_lengths(
    left: ExactLengths | None,
    right: ExactLengths | None,
) -> ExactLengths | None:
    """The sum of two groups, unknown if either is.

    The :func:`_merged_matrix` rule over the exact length maps: an
    unknown side makes the whole merge unknown rather than a smaller
    number.  The arithmetic itself is :meth:`LengthTally.merge`, so the
    roll-up here and the region merge in the scan are one rule.
    """
    if left is None or right is None:
        return None
    tally = LengthTally.restored(left)
    tally.merge(LengthTally.restored(right))
    return tally.frozen()


def merged_tallies(
    left: LengthTally | None,
    right: LengthTally | None,
) -> LengthTally | None:
    """The same rule between two SCANNED groups, folded left in place.

    For a statistic whose regions hold a dict tally that may be unknown
    -- the allele score's indel groups.
    """
    if left is None or right is None:
        return None
    left.merge(right)
    return left


def folded_groups(
    groups: Iterable[LengthArrayTally | ExactLengths | None],
) -> ExactLengths | None:
    """:func:`folded_lengths` over whatever each region holds.

    The same all-or-nothing rule, over the two forms a region's lengths
    take: a scanned region holds its array tally and a region restored
    from a statistics file holds the stored record.  Tallies add
    elementwise in the array domain, into ONE counter block allocated
    only if there is a tally to fold; records add per length in the map
    domain; and the two halves meet once at the end.  So a scan's fold
    costs the clamp per region rather than a dict fold per contig --
    a fragment score can carry thousands of contigs with thousands of
    distinct lengths each -- while a file's fold costs the keys
    ``json.loads`` already parsed and builds no array at all
    (gain#1565).
    """
    arrays: LengthArrayTally | None = None
    records = LengthTally()
    for group in groups:
        if group is None:
            return None
        if isinstance(group, LengthArrayTally):
            if arrays is None:
                arrays = LengthArrayTally()
            arrays.merge(group)
        else:
            records.merge(LengthTally.restored(group))
    if arrays is not None:
        records.merge(LengthTally.restored(arrays.frozen()))
    return records.frozen()


def folded_lengths(
    records: Iterable[ExactLengths | None],
) -> ExactLengths | None:
    """The fold of every chromosome's record, ``None`` if any is unknown.

    What a statistic's ``global`` entry IS for a length group: the
    :func:`merged_lengths` rule over all the chromosomes, so a partial
    roll-up can never silently understate.  A file that stored the
    counts without the records (format version 1 of ``coverage.json``
    and ``fragments.json``) passes its count gate and fails this one.
    """
    result: ExactLengths | None = NO_LENGTHS
    for lengths in records:
        if lengths is None:
            return None
        result = merged_lengths(result, lengths)
    return result


def stored_lengths(
    entry: Mapping[str, Any], key: str,
) -> ExactLengths | None:
    """A stored record under ``key``, ``None`` when the entry carries none.

    The record is the ONLY thing read.  A file predating it -- one
    written with the log2 ladder histogram the record replaced
    (``insertion_length_histogram``, ``segment_length_histogram``,
    ``fragment_length_histogram``) -- carries no record, so the group
    reads as unknown and the page says "not computed" until the resource
    is rebuilt.

    That is one reader rather than a compatibility branch, deliberately
    (ADR 0020 as amended by gain#1118).  A branch that read the old
    histograms would have to publish them as an :class:`ExactLengths`
    whose exact map, sum, min and max are all unrecoverable, so every
    statistic in the table would be a guess at bin resolution presented
    as a number.
    """
    stored = entry.get(key)
    if stored is None:
        return None
    return ExactLengths.from_stored(stored)


def write_length_chart(
    resource: GenomicResource,
    filename: str,
    lengths: ExactLengths | None,
    kind: str,
) -> None:
    """Draw the group's chart into the resource, exactly when it has one.

    A group that is unknown, or known and empty, writes no image -- the
    info page's section is what says which, and it asks
    :attr:`ExactLengths.has_counts_to_plot` too, so the build and the
    page cannot disagree about whether an image exists.  What skipping
    costs is a previous build's image left behind when a group empties
    out, and nothing links the leftover: the page reads the stored
    record, not the directory.
    """
    if lengths is None or not lengths.has_counts_to_plot:
        return
    with resource.open_raw_file(filename, mode="wb") as outfile:
        plot_length_histogram(outfile, length_ladder(lengths), kind)


def _trimmed(value: float) -> str:
    """A length to two decimals, with trailing zeros trimmed off.

    A median of exactly 2 reads ``2`` rather than ``2.00``: these are
    base-pair counts, and most of them land on whole numbers -- an even
    item count straddling two lengths is the only thing that puts a
    half there.  Two decimals is what the shares on an info page use, so
    a mean of 2.33 reads at the same resolution as a 2.33% share.
    """
    return f"{value:.2f}".rstrip("0").rstrip(".")


class LengthStatisticsRow(NamedTuple):
    """One group as the statistics table renders it.

    Every field is already TEXT.  The table shows five different kinds
    of number -- a count, two exact lengths and two averages, one of
    which can be a floor -- and formatting each where it is built keeps
    the template free of a second rule about how a length is written.

    ``group`` is whatever the caller labels the row: "insertions" on an
    allele page, "segments" or "fragments" elsewhere.  The count column
    is ``total`` for the same reason the record's is.
    """

    group: str
    total: str
    min: str
    max: str
    mean: str
    median: str

    @classmethod
    def of(cls, group: str, lengths: ExactLengths) -> LengthStatisticsRow:
        """One group's row; the LENGTH cells empty when it holds none.

        A group that was scanned and found nothing has a genuine count
        of zero, which renders as ``0`` -- that is the answer, not a
        missing one.  What it has no answer for is a shortest, longest
        or average length, so those four render empty rather than as
        zeros, which would read as items of length nothing.
        """
        mean = lengths.mean
        # The middle pair read ONCE, not once through each property:
        # both walk a sorted copy of the map, which at the clamp's
        # 8192 distinct keys is ~0.9ms a walk.
        middle = lengths.middle_lengths()
        median = None if middle is None else sum(middle) / 2
        is_clamped = middle is not None and middle[1] >= LENGTH_MAP_CLAMP
        return cls(
            group,
            str(lengths.total),
            "" if lengths.min is None else str(lengths.min),
            "" if lengths.max is None else str(lengths.max),
            "" if mean is None else _trimmed(mean),
            # The one cell the clamp can blunt: past it the map knows
            # only "this long or longer", so the median is written as
            # the floor it is -- with the same sign every clamped cell
            # on these pages uses, so one page says one thing one way.
            # min, max and mean are exact from the scalars and need no
            # such hedge.
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
