from __future__ import annotations

from abc import abstractmethod
from collections.abc import Iterable
from typing import Any, Protocol, Self


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
