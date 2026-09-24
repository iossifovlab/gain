"""The contract the three region-folded statistics share.

The coverage, allele and fragment statistics accumulate one region per
scanned window rather than value by value.  What they share -- the
fold that keeps one region per chromosome, and the adjacency rule it
inherits from the regions -- is asserted here once for all three, so a
change to the shared base cannot quietly alter one of them while its
own test module keeps passing.

Each statistic's own behaviour (what it counts, what it serializes) is
asserted in its own module; only what is COMMON belongs here.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, NamedTuple

import pytest
from gain.genomic_resources.statistics.alleles import (
    AlleleStatistics,
    RegionAlleles,
    merge_region_alleles,
)
from gain.genomic_resources.statistics.coverage import (
    CoverageStatistics,
    RegionCoverage,
    SegmentSummary,
    merge_region_coverage,
)
from gain.genomic_resources.statistics.exact_lengths import ExactLengths
from gain.genomic_resources.statistics.fragments import (
    FragmentStatistics,
    RegionFragments,
    merge_region_fragments,
)


class RegionFolded(NamedTuple):
    """One region-folded statistic under test, end to end.

    One table for both halves -- the statistic and its scan-side fold --
    so the two cannot come to describe different regions for the same
    kind while each keeps passing.
    """

    build: Callable[[], Any]
    a_region: Callable[[str], Any]
    chromosomes: Callable[[Any], set[str]]
    """The chromosomes the statistic ended up holding."""
    fold: Callable[..., Any]
    """The module-level ``merge_region_*`` the scan calls."""
    failed_to: str
    """The phrase that fold must use when it cannot merge."""


#: One item of length 1, as a restored region holds it: the frozen
#: rows below carry a record, because a restored region holds its
#: record as read rather than a mergeable tally, and the adjacency
#: refusal is what keeps that from ever mattering.
ONE_LENGTH = ExactLengths({1: 1}, 1, 1, 1, 1)

REGION_FOLDED = [
    pytest.param(
        RegionFolded(
            CoverageStatistics,
            lambda chrom: RegionCoverage.frozen(
                chrom, 1, SegmentSummary(1, ONE_LENGTH)),
            lambda stats: set(stats.covered_by_chromosome()),
            merge_region_coverage,
            "could not merge the coverage of"),
        id="coverage"),
    pytest.param(
        RegionFolded(
            AlleleStatistics,
            lambda chrom: RegionAlleles.frozen(chrom, 1, {"other": 1}),
            lambda stats: set(stats.by_chromosome()),
            merge_region_alleles,
            "could not merge the allele statistics of"),
        id="alleles"),
    pytest.param(
        RegionFolded(
            FragmentStatistics,
            lambda chrom: RegionFragments.frozen(chrom, 1, ONE_LENGTH),
            lambda stats: set(stats.fragments_by_chromosome()),
            merge_region_fragments,
            "could not merge the fragment statistics of"),
        id="fragments"),
]


@pytest.mark.parametrize("statistic", REGION_FOLDED)
def test_folding_no_regions_produces_no_statistic(
    statistic: RegionFolded,
) -> None:
    # A kind that contributed nothing is absent, not empty-but-present:
    # the caller writes no file at all for a None.
    assert statistic.fold("a_resource", []) is None
    assert statistic.fold("a_resource", [None, None]) is None


@pytest.mark.parametrize("statistic", REGION_FOLDED)
def test_folding_keeps_every_chromosome_and_drops_the_absent_kinds(
    statistic: RegionFolded,
) -> None:
    # Distinct chromosomes accumulate side by side rather than merging,
    # so no adjacency applies -- and a None among them is a kind that
    # produced nothing there, dropped rather than folded.
    statistics = statistic.fold(
        "a_resource",
        [statistic.a_region("chr2"), None, statistic.a_region("chr1")])

    assert statistics is not None
    assert statistic.chromosomes(statistics) == {"chr1", "chr2"}


@pytest.mark.parametrize("statistic", REGION_FOLDED)
def test_a_failed_fold_names_the_resource_and_re_raises(
    statistic: RegionFolded,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Two regions on one chromosome carrying no extents are not
    # adjacent, so the fold fails.  It must say WHICH statistic of WHICH
    # resource could not be merged -- the operator reads that line to
    # know where to look -- and must still re-raise, so the build fails
    # rather than silently writing a half-merged statistic.  The three
    # phrases name different things ON PURPOSE, so a shared fold has to
    # be told which to use rather than picking one.
    a_region = statistic.a_region
    with caplog.at_level(logging.ERROR, logger="grr_manage"), \
            pytest.raises(ValueError, match="adjacent-and-in-order"):
        statistic.fold("a_resource", [a_region("chr1"), a_region("chr1")])

    assert f"{statistic.failed_to} <a_resource>" in caplog.text
