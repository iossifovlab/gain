# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pytest
from gain.utils.regions import BedRegion, bundle_regions


def test_consecutive_regions_pack_in_order_while_they_fit_the_budget() -> None:
    # 10 + 10 fit a budget of 25; the third region would take the bundle
    # to 30, so it opens the next one, which the fourth then joins.
    regions = [
        BedRegion("chr1", 1, 10), BedRegion("chr2", 1, 10),
        BedRegion("chr3", 1, 10), BedRegion("chr4", 1, 5),
    ]

    bundles = bundle_regions(regions, 25)

    assert bundles == [regions[:2], regions[2:]]


def test_a_region_longer_than_the_budget_is_a_bundle_of_its_own() -> None:
    # A chromosome is never split: the 100-base region stands alone, and
    # the small regions on either side of it pack among themselves.
    regions = [
        BedRegion("chrM", 1, 5), BedRegion("chr1", 1, 100),
        BedRegion("chrU1", 1, 5), BedRegion("chrU2", 1, 5),
    ]

    bundles = bundle_regions(regions, 20)

    assert bundles == [[regions[0]], [regions[1]], regions[2:]]


@pytest.mark.parametrize("budget", [0, -1])
def test_a_budget_of_zero_or_less_is_one_region_per_bundle(
    budget: int,
) -> None:
    regions = [BedRegion("chr1", 1, 10), BedRegion("chr2", 1, 10)]

    bundles = bundle_regions(regions, budget)

    assert bundles == [[regions[0]], [regions[1]]]
