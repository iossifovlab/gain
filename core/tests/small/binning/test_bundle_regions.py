# pylint: disable=W0621,C0114,C0116,W0212,W0613
from gain.binning.cli import bundle_regions
from gain.utils.regions import BedRegion


def region(chrom: str, start: int, stop: int) -> BedRegion:
    return BedRegion(chrom, start, stop)


def test_consecutive_regions_pack_in_order_while_they_fit_the_budget() -> None:
    # 10 + 10 fit a budget of 25; the third region would take the bundle
    # to 30, so it opens the next one, which the fourth then joins.
    regions = [
        region("chr1", 1, 10), region("chr2", 1, 10),
        region("chr3", 1, 10), region("chr4", 1, 5),
    ]

    bundles = bundle_regions(regions, 25)

    assert bundles == [regions[:2], regions[2:]]


def test_a_region_longer_than_the_budget_is_a_bundle_of_its_own() -> None:
    # A chromosome is never split: the 100-base region stands alone, and
    # the small regions on either side of it pack among themselves.
    regions = [
        region("chrM", 1, 5), region("chr1", 1, 100),
        region("chrU1", 1, 5), region("chrU2", 1, 5),
    ]

    bundles = bundle_regions(regions, 20)

    assert bundles == [[regions[0]], [regions[1]], regions[2:]]


def test_a_budget_of_zero_is_one_region_per_bundle() -> None:
    regions = [region("chr1", 1, 10), region("chr2", 1, 10)]

    bundles = bundle_regions(regions, 0)

    assert bundles == [[regions[0]], [regions[1]]]
