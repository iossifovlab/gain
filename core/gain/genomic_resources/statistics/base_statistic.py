from __future__ import annotations

from abc import abstractmethod
from collections.abc import Iterable
from typing import Any, NamedTuple, Protocol, Self

import numpy as np


class StoredStatistic(NamedTuple):
    """One statistics file a build writes, at the version its writer stamps.

    What the repair flow compares a resource's stored files against
    (gain#1586): the file is missing, or carries an older
    ``format_version``, exactly when the resource predates the schema.
    The version here MUST be the constant the writer's ``serialize``
    stamps, so the two cannot drift.
    """

    file: str
    format_version: int


class Statistic:
    """
    Base class genomic resource statistics.

    Statistics are generated using task graphs and aggregate values from
    a large amount of data. Each statistic should have a clearly defined
    single unit of data to process (for example, a nucleotide in a
    reference genome).
    """

    statistic_id: str
    description: str

    def __init__(self, statistic_id: str, description: str):
        self.statistic_id = statistic_id
        self.description = description

    @abstractmethod
    def add_value(self, value: Any) -> None:
        """Add a value to the statistic."""
        raise NotImplementedError

    def finish(self) -> None:
        """
        Perform final calculations for the statistic.

        This step is optional.

        This is called when resource iteration is complete.

        Can also be used when creating more complex resources via
        deserialization.
        """
        return

    @abstractmethod
    def merge(self, other: Statistic) -> None:
        """Merge the values from another statistic in place."""
        raise NotImplementedError

    @abstractmethod
    def serialize(self) -> str:
        """Return a serialized version of this statistic."""
        raise NotImplementedError

    @staticmethod
    @abstractmethod
    def deserialize(content: str) -> Statistic:
        """Create a statistic from serialized data."""
        raise NotImplementedError


class ScannedRegion(Protocol):
    # A structural description of three attributes; there is deliberately
    # nothing to call on it.
    # pylint: disable=too-few-public-methods
    """What every per-region accumulator shows the merge step.

    A statistic accumulated per scanned REGION -- coverage, allele
    content and fragments alike -- carries the extent it was scanned
    over, because the merge is only sound over a contig's
    non-overlapping windows.  What it accumulates INSIDE that extent is
    the statistic's own business and is deliberately absent here.
    """

    chrom: str
    start: int | None
    end: int | None


class MergeableRegion(ScannedRegion, Protocol):
    # The one thing to call on it; the attributes come from the base.
    # pylint: disable=too-few-public-methods
    """A scanned region that can absorb its neighbour to the right.

    What :class:`RegionFoldedStatistic` needs of the accumulators it
    holds, and no more: where the region was scanned (from
    :class:`ScannedRegion`) and how to fold the next one in.  Whether
    that fold adds counts, unions spans or sums histograms is the
    region's own business.
    """

    def merge(self, other: Self) -> None:
        """Fold the adjacent region to the right into this one."""
        ...


class RegionFoldedStatistic[R: MergeableRegion](Statistic):
    """A statistic accumulated one region per scanned window.

    The coverage, allele and fragment statistics are all built this way:
    the scan produces one region per window, and the statistic keeps one
    region per chromosome, folding each new window into the region
    already held for its contig.  Distinct chromosomes accumulate side
    by side; same-chromosome windows merge, which is where the regions'
    adjacency rule applies.

    Subclasses supply their id and description, their readers over the
    folded regions, and their own serialization.  Everything about the
    FOLD itself lives here.
    """

    # An abstract intermediate base: `serialize`/`deserialize` are each
    # concrete statistic's own, there being no common stored shape.
    # pylint: disable=abstract-method

    def __init__(self, statistic_id: str, description: str) -> None:
        super().__init__(statistic_id, description)
        self._regions: dict[str, R] = {}

    def fold_region(self, region: R) -> None:
        """Fold one region in, keyed by its chromosome."""
        held = self._regions.get(region.chrom)
        if held is None:
            self._regions[region.chrom] = region
        else:
            held.merge(region)

    def add_value(self, value: Any) -> None:  # ruff: ignore[unused-method-argument]
        # The suppression is load-bearing: ARG002 exempts a stub body
        # (`raise NotImplementedError`) but not one raising a message.
        # Named off `type(self)` so each subclass refuses in its own.
        raise TypeError(
            f"{type(self).__name__} accumulates regions, not values; "
            "use fold_region")

    def merge(self, other: Statistic) -> None:
        """Fold another statistics object's regions into this one.

        For statistics holding LIVE regions (the scan's own): two
        deserialized statistics carry no extents, so same-chromosome
        regions from two files refuse to merge as non-adjacent.

        Gated on the CONCRETE type rather than on this base: all three
        kinds fold regions identically, so a base-type gate would
        happily fold allele counts into a coverage statistic.
        ``type(self)`` makes that gate asymmetric where the three
        named-class checks it replaced were symmetric, which nothing
        observes: none of the three is subclassed.
        """
        if not isinstance(other, type(self)):
            raise TypeError("unexpected type of statistics to merge with")
        self._fold_all(other)

    def _fold_all(self, other: RegionFoldedStatistic[R]) -> None:
        # Split out so the loop reads `_regions` off a value annotated
        # as this class, not as `Statistic` -- same-class access, which
        # needs no suppression.  `merge` cannot say so in its own
        # signature: that is fixed by the abstract method it implements.
        for region in other._regions.values():
            self.fold_region(region)


def regions_in_genomic_order[R: ScannedRegion](
    regions: Iterable[R | None],
) -> list[R]:
    """The scan's regions in genomic order, kinds carrying none dropped.

    Ordered here rather than trusting the order the task arguments
    arrived in: the merge asserts adjacency, and a correct set of
    regions handed over shuffled would fail that assertion.
    """
    return sorted(
        (region for region in regions if region is not None),
        key=lambda region: (
            region.chrom,
            region.start if region.start is not None else 0))


#: What one value folded into a numeric accumulator may be, once numpy's own
#: scalars have been normalized to the Python value they hold.  ``bool``
#: rides in through ``int``, which it subclasses -- a bool score under a
#: number histogram really does produce a 0/1 histogram.
#:
#: Shared by ``NumberHistogram`` and ``MinMaxValue`` for the reason
#: :func:`non_numeric_error` below is: the twins fold the same values, so the
#: allow-list is stated once (gain#1338, gain#1358).  It lives here rather than
#: with the histogram because ``histogram.py`` imports ``min_max.py`` and not
#: the reverse.
#:
#: Named for Python's types to keep it distinct from
#: ``NUMBER_HISTOGRAM_VALUE_TYPES``: that one is declared score
#: ``value_type`` STRINGS, this one is the type of a single folded value.
#:
#: Public rather than folded into :func:`as_python_number`, because each
#: twin tests it INLINE before calling that: ``add_value`` runs per value of
#: every record, a Python float is what the scan hands it, and a function
#: call on that path costs measurably more than one ``isinstance`` against a
#: hoisted tuple (gain#1358 measured +8% against +3%).  Hoisted rather than
#: written as a tuple literal at each check, because a tuple of names is
#: rebuilt on every call: 1M ``isinstance`` calls, best of 5 (gain#1338),
#: 112 ns/call for a 3-member literal against 52 ns hoisted.  ``float`` first
#: because ``isinstance`` tests a tuple in order.
PYTHON_NUMBER_TYPES = (float, int)


def as_python_number(value: Any, what: str) -> float | int:
    """The Python number a value that is NOT already one folds as.

    The slow path of the numeric contract ``NumberHistogram`` and
    ``MinMaxValue`` share, reached only by a value ``np.isnan`` accepted and
    the caller's inline :data:`PYTHON_NUMBER_TYPES` test refused -- text and
    ``Decimal`` never get here, and neither does a Python number.

    numpy's own scalars fold as the Python value they hold, which is what an
    enumerated allow-list checked BEFORE normalizing got wrong: ``np.float32``
    is not a ``float`` (only ``np.float64`` subclasses it) and ``np.bool_``
    is not an ``np.integer``, so all of them were refused as non-numeric even
    though ``add_batch`` folds them and a gene score's column really does
    arrive as one (gain#1338).  ``item()`` rather than a wider allow-list:
    widening alone would not make the histogram's two arms agree, because
    numpy 2 keeps ``np.float32 - <python float>`` in float32 and that picks a
    different bin at the edges -- the witness is in
    ``test_add_batch_matches_add_value_loop_float32_at_bin_edges``.

    Anything else is refused: ``np.isnan`` accepting a value does not make it
    one a reducer can fold.  A complex or a 0-d array passes it, and
    ``min()`` would order either without complaint and leave the extremum
    holding it (gain#1358).  The refusal names what the CALLER handed over,
    not what it was normalized to, so a nullified score's reason does not
    report a ``np.complex128`` as a plain ``complex``.
    """
    folded = value.item() if isinstance(value, np.generic) else value
    if not isinstance(folded, PYTHON_NUMBER_TYPES):
        raise non_numeric_error(value, what)
    return folded


#: What a numeric accumulator's nan skip raises for a value it cannot read
#: as a number -- the complaints :func:`non_numeric_error` re-words, stated
#: once so the twins that catch them cannot drift apart.  ``TypeError`` is
#: ``np.isnan`` on text or ``Decimal``; ``ValueError`` is ``np.isnan`` on a
#: SEQUENCE, which answers with an array whose coercion to the ``bool`` the
#: skip needs is what raises.  Catching only the first let the second escape
#: every statistics pass's per-score containment and abort the whole build
#: (gain#1337).  A one-element sequence is the exception: its array coerces,
#: and :func:`as_python_number` refuses it below the skip instead.
NON_NUMERIC_ERRORS = (TypeError, ValueError)


def non_numeric_error(value: Any, what: str) -> TypeError:
    """The one wording of a numeric accumulator's refusal.

    ``what`` names the accumulator in the message; the rule is one -- the
    same shape as :func:`refuse_unmergeable`, and here for the same reason:
    ``NumberHistogram`` and ``MinMaxValue`` are twins that fold the same
    values under the same numeric contract, and a reader of a nullified
    score's reason should not be able to tell which of them produced it.

    Returned rather than raised, so a caller can raise it ``from`` the
    numpy error it is re-wording -- one of :data:`NON_NUMERIC_ERRORS`.
    """
    return TypeError(
        f"Cannot add non numerical value {value!r} ({type(value)}) to {what}",
    )


def refuse_unmergeable(
    what: str,
    left: ScannedRegion,
    right: ScannedRegion,
) -> None:
    """Refuse a pair that is not adjacent-and-in-order on one contig.

    Region statistics are only ever produced over a contig's
    non-overlapping windows, so anything else reaching a merge is a
    wiring error -- and it is exactly the adjacency that lets the
    per-region counts simply add.  Refusing it loudly is the difference
    between a failed build and a silently wrong statistic.

    ``what`` names the statistic in the message; the rule is one.
    """
    if left.chrom != right.chrom:
        raise ValueError(
            f"{what} merge across chromosome boundaries: "
            f"{left.chrom} and {right.chrom}")
    if left.end is None or right.start is None \
            or left.end + 1 != right.start:
        raise ValueError(
            f"{what} regions are not adjacent-and-in-order: "
            f"{left.chrom}:{left.start}-{left.end} then "
            f"{right.chrom}:{right.start}-{right.end}")
