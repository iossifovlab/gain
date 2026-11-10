# pylint: disable=C0114,C0116
import pytest
from gain.utils.regions import (
    BedRegion,
    Region,
    collapse,
    collapse_no_chrom,
    connected_component,
    difference,
)


@pytest.mark.parametrize(
    "regions,is_sorted,expected",
    [
        (
            [Region("1", 1, 10), Region("1", 10, 20)],
            False,
            [Region("1", 1, 20)],
        ),
        (
            [Region("1", 1, 10), Region("1", 11, 20)],
            False,
            [Region("1", 1, 10), Region("1", 11, 20)],
        ),
        (
            [Region("1", 1, 5), Region("1", 10, 20), Region("1", 15, 30)],
            False,
            [Region("1", 1, 5), Region("1", 10, 30)],
        ),
        (
            [Region("1", 10, 20), Region("1", 1, 5)],
            False,
            [Region("1", 1, 5), Region("1", 10, 20)],
        ),
        (
            [Region("1", 1, 5), Region("1", 4, 8), Region("1", 10, 20)],
            True,
            [Region("1", 1, 8), Region("1", 10, 20)],
        ),
        (
            [Region("1", 1, 20), Region("1", None, 10)],
            False,
            [Region("1", None, 20)],
        ),
    ],
    ids=[
        "touch",
        "one_apart",
        "extend_the_last_merged",
        "unsorted",
        "sorted",
        "open_start_before_start_1",
    ],
)
def test_collapse_merges_overlapping_and_touching_regions(
    regions: list[Region], is_sorted: bool, expected: list[Region],
) -> None:
    result = collapse(regions, is_sorted=is_sorted)

    assert result == expected


@pytest.mark.parametrize(
    "regions,expected",
    [
        (
            [BedRegion("1", 1, 10), BedRegion("1", 10, 20)],
            [BedRegion("1", 1, 20)],
        ),
        (
            [BedRegion("1", 1, 10), BedRegion("1", 11, 20)],
            [BedRegion("1", 1, 10), BedRegion("1", 11, 20)],
        ),
        (
            [
                BedRegion("1", 1, 5), BedRegion("1", 10, 20),
                BedRegion("1", 15, 30),
            ],
            [BedRegion("1", 1, 5), BedRegion("1", 10, 30)],
        ),
        (
            [BedRegion("1", 10, 20), BedRegion("1", 1, 5)],
            [BedRegion("1", 1, 5), BedRegion("1", 10, 20)],
        ),
    ],
    ids=["touch", "one_apart", "extend_the_last_merged", "unsorted"],
)
def test_collapse_no_chrom_merges_overlapping_and_touching_regions(
    regions: list[BedRegion], expected: list[BedRegion],
) -> None:
    result = collapse_no_chrom(regions)

    assert result == expected


@pytest.mark.parametrize(
    "regions,expected",
    [
        (
            [BedRegion("1", 1, 10), BedRegion("1", 10, 20)],
            [{BedRegion("1", 1, 10), BedRegion("1", 10, 20)}],
        ),
        (
            [BedRegion("1", 1, 10), BedRegion("1", 11, 20)],
            [{BedRegion("1", 1, 10)}, {BedRegion("1", 11, 20)}],
        ),
    ],
    ids=["touch", "one_apart"],
)
def test_connected_component_joins_regions_that_touch(
    regions: list[BedRegion], expected: list[set[BedRegion]],
) -> None:
    result = connected_component(regions)

    assert {frozenset(c) for c in result} == {
        frozenset(c) for c in expected}


@pytest.mark.parametrize(
    "regions1,regions2,expected",
    [
        (
            [Region("1", 1, 10)],
            [Region("1", 10, 20)],
            [Region("1", 1, 9)],
        ),
        (
            [Region("1", 10, 20)],
            [Region("1", 1, 10)],
            [Region("1", 11, 20)],
        ),
        (
            [Region("1", 1, 10)],
            [Region("1", 5, 9)],
            [Region("1", 1, 4), Region("1", 10, 10)],
        ),
        (
            [Region("1", 1, 10), Region("1", 20, 30)],
            [Region("1", 5, 8), Region("1", 22, 25)],
            [
                Region("1", 1, 4), Region("1", 9, 10),
                Region("1", 20, 21), Region("1", 26, 30),
            ],
        ),
        (
            [Region("1", None, 50)],
            [Region("1", 1, 20)],
            [Region("1", 21, 50)],
        ),
        (
            [Region("1", 10, None)],
            [Region("1", 20, 30)],
            [Region("1", 10, 19), Region("1", 31, None)],
        ),
        (
            [Region("1", 10, 50)],
            [Region("1", None, 20)],
            [Region("1", 21, 50)],
        ),
        (
            [Region("1", 50, 60)],
            [Region("1", 10, None)],
            [],
        ),
        (
            [
                Region("chr1", 1, 10), Region("chr2", 1, 100),
                Region("chr3", 1, 10),
            ],
            [Region("chr2", 10, 20), Region("chr4", 1, 5)],
            [
                Region("chr1", 1, 10), Region("chr2", 1, 9),
                Region("chr2", 21, 100), Region("chr3", 1, 10),
            ],
        ),
        (
            [Region("1", None, 10), Region("1", 20, 30)],
            [Region("1", 5, 25)],
            [Region("1", 1, 4), Region("1", 26, 30)],
        ),
        (
            [Region("chr1", 1, 100), Region("chr2", 1, 100)],
            [Region("chr1", 10, 20), Region("chr2", 10, 20)],
            [
                Region("chr1", 1, 9), Region("chr1", 21, 100),
                Region("chr2", 1, 9), Region("chr2", 21, 100),
            ],
        ),
        (
            [Region("chr1", 1, None), Region("chr2", 1, 100)],
            [Region("chr1", 10, 20), Region("chr2", 10, 20)],
            [
                Region("chr1", 1, 9), Region("chr1", 21, None),
                Region("chr2", 1, 9), Region("chr2", 21, 100),
            ],
        ),
        (
            [Region("chr1", 1, None), Region("chr2", 1, 100)],
            [Region("chr1", 10, None), Region("chr2", 10, 20)],
            [
                Region("chr1", 1, 9),
                Region("chr2", 1, 9), Region("chr2", 21, 100),
            ],
        ),
        (
            [Region("1", 1, None)],
            [Region("1", 20, None)],
            [Region("1", 1, 19)],
        ),
    ],
    ids=[
        "touch_at_the_stop",
        "touch_at_the_start",
        "one_position_left_at_the_stop",
        "next_cut_on_the_same_chrom",
        "open_start_in_the_first_list",
        "open_stop_in_the_first_list",
        "open_start_in_the_second_list",
        "open_stop_in_the_second_list",
        "second_list_on_other_chroms",
        "open_start_before_a_closed_start_in_the_first_list",
        "next_cut_on_the_next_chrom",
        "open_stop_before_a_cut_on_the_next_chrom",
        "open_stop_cut_before_a_cut_on_the_next_chrom",
        "open_stop_cut_on_one_chrom",
    ],
)
def test_difference_cuts_the_second_list_out_of_the_first(
    regions1: list[Region], regions2: list[Region], expected: list[Region],
) -> None:
    result = difference(regions1, regions2)

    assert result == expected


def test_symmetric_difference_sorts_an_open_start_first() -> None:
    result = difference(
        [Region("1", None, 10)], [Region("1", 20, 30)], symmetric=True,
    )

    assert result == [Region("1", None, 10), Region("1", 20, 30)]
