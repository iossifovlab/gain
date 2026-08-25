from __future__ import annotations

from abc import abstractmethod
from collections.abc import Iterable
from typing import Any, Protocol


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

    A statistic accumulated per scanned REGION -- coverage and allele
    content both -- carries the extent it was scanned over, because the
    merge is only sound over a contig's non-overlapping windows.  What
    it accumulates INSIDE that extent is the statistic's own business
    and is deliberately absent here.
    """

    chrom: str
    start: int | None
    end: int | None


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
