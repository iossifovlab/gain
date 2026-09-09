"""The contract the three region-folded statistics share.

The coverage, allele and fragment statistics accumulate one region per
scanned window rather than value by value.  What they share -- the
refusal to take a bare value, the type gate on ``merge``, and the
adjacency rule the fold inherits from the regions -- is asserted here
once for all three, so a change to the shared base cannot quietly alter
one of them while its own test module keeps passing.

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
    merge_region_coverage,
)
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


REGION_FOLDED = [
    pytest.param(
        RegionFolded(
            CoverageStatistics,
            lambda chrom: RegionCoverage.frozen(chrom, 1, None),
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
            lambda chrom: RegionFragments.frozen(chrom, 1, None),
            lambda stats: set(stats.fragments_by_chromosome()),
            merge_region_fragments,
            "could not merge the fragment statistics of"),
        id="fragments"),
]


@pytest.mark.parametrize("statistic", REGION_FOLDED)
def test_the_statistic_refuses_a_bare_value(
    statistic: RegionFolded,
) -> None:
    # The whole message, not just a fragment of it: it names the class
    # it came from, and a shared base deriving that name is exactly the
    # thing that could silently start naming the base instead.
    built = statistic.build()

    with pytest.raises(TypeError) as caught:
        built.add_value(1)

    assert str(caught.value) == (
        f"{type(built).__name__} accumulates regions, not values; "
        "use fold_region")


# Each kind paired with a DIFFERENT one.  All three fold regions the
# same way, so a shared base gating on the base type rather than on the
# concrete one would happily fold alleles into coverage; these pairs are
# what refuses that.
FOREIGN_PAIRS = [
    pytest.param(
        CoverageStatistics, AlleleStatistics, id="coverage-given-alleles"),
    pytest.param(
        AlleleStatistics, FragmentStatistics, id="alleles-given-fragments"),
    pytest.param(
        FragmentStatistics, CoverageStatistics, id="fragments-given-coverage"),
]


@pytest.mark.parametrize(("build", "build_foreign"), FOREIGN_PAIRS)
def test_the_statistic_refuses_to_merge_another_kind(
    build: Callable[[], Any],
    build_foreign: Callable[[], Any],
) -> None:
    with pytest.raises(
            TypeError,
            match="unexpected type of statistics to merge with"):
        build().merge(build_foreign())


@pytest.mark.parametrize("statistic", REGION_FOLDED)
def test_merging_two_statistics_of_one_chromosome_needs_adjacency(
    statistic: RegionFolded,
) -> None:
    # Two DESERIALIZED statistics carry no extents, so same-chromosome
    # regions from two files refuse to merge as non-adjacent.  The fold
    # does not assert this itself -- it inherits the refusal from the
    # regions -- so what this pins is that merge still routes through
    # fold_region rather than around it.
    left = statistic.build()
    left.fold_region(statistic.a_region("chr1"))
    right = statistic.build()
    right.fold_region(statistic.a_region("chr1"))

    with pytest.raises(ValueError, match="adjacent-and-in-order"):
        left.merge(right)


@pytest.mark.parametrize("statistic", REGION_FOLDED)
def test_merging_lands_the_others_regions_in_this_statistic(
    statistic: RegionFolded,
) -> None:
    # The positive half of merge, which the refusals above cannot see:
    # that the fold moves the OTHER's regions into THIS one, and in that
    # direction.  Folding them into `other` instead would leave this
    # assertion short a chromosome -- and would still satisfy every
    # refusal test, because a region self-merged is non-adjacent to
    # itself and raises the very error they expect.
    left = statistic.build()
    left.fold_region(statistic.a_region("chr1"))
    right = statistic.build()
    right.fold_region(statistic.a_region("chr2"))

    left.merge(right)

    assert statistic.chromosomes(left) == {"chr1", "chr2"}
    assert statistic.chromosomes(right) == {"chr2"}


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
