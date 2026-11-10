# pylint: disable=W0621,C0114,C0116
"""Edge cases of the region checks of ``gain.utils.regions``.

The cases sit at the bounds that the checks compare: regions that touch,
regions one position apart, and regions with an open bound. Each table
compares ``Region`` objects with ``==``, because ``str`` hides an open
bound: ``Region("1", None, 50)`` prints as ``1``.
"""

import pathlib
from collections.abc import Iterator

import pysam
import pytest
from gain.genomic_resources.testing import setup_tabix
from gain.utils.regions import (
    Region,
    get_chromosome_length_tabix,
    intersection,
)


@pytest.mark.parametrize(
    "regions1,regions2,expected",
    [
        # A region of the second list ends where one of the first starts
        ([Region("1", 10, 20)], [Region("1", 5, 10)], [Region("1", 10, 10)]),
        # A region of the first list ends where one of the second starts
        ([Region("1", 5, 10)], [Region("1", 10, 20)], [Region("1", 10, 10)]),
        # The regions are one position apart
        ([Region("1", 11, 20)], [Region("1", 1, 10)], []),
        ([Region("1", 1, 10)], [Region("1", 11, 20)], []),
        # The regions start at the same position
        ([Region("1", 10, 20)], [Region("1", 10, 15)], [Region("1", 10, 15)]),
        # An open start in the first list
        ([Region("1", None, 20)], [Region("1", 5, 30)], [Region("1", 5, 20)]),
        # An open start in the first list, against a start at the first base.
        # ``Region("1", 1, 20)`` covers the same positions. The case pins
        # that intersection keeps the open start of the first list.
        (
            [Region("1", None, 20)], [Region("1", 1, 30)],
            [Region("1", None, 20)],
        ),
        # An open start in the second list
        ([Region("1", 5, 20)], [Region("1", None, 10)], [Region("1", 5, 10)]),
        # An open stop in the first list
        ([Region("1", 10, None)], [Region("1", 5, 20)], [Region("1", 10, 20)]),
        # An open stop in the second list, against an open start
        (
            [Region("1", None, 20)], [Region("1", 10, None)],
            [Region("1", 10, 20)],
        ),
        # An open stop in the second list, against a closed start that
        # comes after the start of the second list
        ([Region("1", 10, 20)], [Region("1", 5, None)], [Region("1", 10, 20)]),
        ([Region("1", 50, 60)], [Region("1", 10, None)], [Region("1", 50, 60)]),
        # An open stop in the second list covers each region of the first
        # list on its chrom, not only the first one
        (
            [Region("1", 10, 20), Region("1", 30, 40)],
            [Region("1", 5, None)],
            [Region("1", 10, 20), Region("1", 30, 40)],
        ),
        # The same lists in the other order
        ([Region("1", 10, None)], [Region("1", 50, 60)], [Region("1", 50, 60)]),
        # Lists on two chroms
        (
            [Region("1", 1, 10), Region("2", 1, 10)], [Region("2", 5, 15)],
            [Region("2", 5, 10)],
        ),
        (
            [Region("1", 1, 10), Region("2", 1, 10)],
            [Region("1", 5, 15), Region("2", 8, 20)],
            [Region("1", 5, 10), Region("2", 8, 10)],
        ),
    ],
)
def test_intersection_of_region_lists(
    regions1: list[Region], regions2: list[Region], expected: list[Region],
) -> None:
    assert intersection(regions1, regions2) == expected


@pytest.mark.parametrize(
    "reg1,reg2,expected",
    [
        (Region("1", 1, 10), Region("1", None, 5), False),
        (Region("1", 1, 10), Region("1", 5, None), False),
        (Region("1", None, 10), Region("1", None, 5), True),
        (Region("1", None, 10), Region("1", 5, None), False),
        (Region("1", 5, None), Region("1", None, 10), False),
        (Region("1", 5, None), Region("1", 10, None), True),
    ],
)
def test_region_contains_a_region_with_one_open_bound(
    reg1: Region, reg2: Region, expected: bool,
) -> None:
    assert reg1.contains(reg2) is expected


@pytest.mark.parametrize(
    "reg1,reg2,expected",
    [
        (Region("1", 1, 10), Region("1", None, 5), True),
        (Region("1", 1, 10), Region("1", 5, None), True),
        (Region("1", 5, 10), Region("1", None, 4), False),
        (Region("1", 5, 10), Region("1", 11, None), False),
        (Region("1", None, 10), Region("1", 5, None), True),
        (Region("1", None, 10), Region("1", 11, None), False),
        (Region("1", 5, None), Region("1", None, 10), True),
        (Region("1", 5, None), Region("1", None, 4), False),
    ],
)
def test_region_intersects_a_region_with_one_open_bound(
    reg1: Region, reg2: Region, expected: bool,
) -> None:
    assert reg1.intersects(reg2) is expected


@pytest.mark.parametrize(
    "reg1,reg2,expected",
    [
        (Region("1", 1, 10), Region("1", 10, 20), Region("1", 10, 10)),
        (Region("1", 10, 20), Region("1", 1, 10), Region("1", 10, 10)),
        (Region("1", None, 10), Region("1", 10, 20), Region("1", 10, 10)),
        (Region("1", 10, None), Region("1", 1, 10), Region("1", 10, 10)),
    ],
)
def test_region_intersection_of_regions_that_share_one_position(
    reg1: Region, reg2: Region, expected: Region,
) -> None:
    assert reg1.intersection(reg2) == expected


@pytest.fixture
def contig_length_tabix(
    tmp_path: pathlib.Path,
) -> Iterator[pysam.TabixFile]:
    filepath = tmp_path / "lengths.txt.gz"
    setup_tabix(
        filepath,
        """
            #chrom pos_begin pos_end
            first  1         1
            long   500000    500010
        """, seq_col=0, start_col=1, end_col=2,
    )
    with pysam.TabixFile(str(filepath)) as tabix:
        yield tabix


@pytest.mark.parametrize(
    "chrom,last_pos,step,precision",
    [
        # The contig is longer than the step
        ("long", 500_010, 1_000, 100),
        # The only record is at the first base
        ("first", 1, 1_000, 1),
    ],
)
def test_get_chromosome_length_tabix_is_just_past_the_last_record(
    contig_length_tabix: pysam.TabixFile,
    chrom: str, last_pos: int, step: int, precision: int,
) -> None:
    length = get_chromosome_length_tabix(
        contig_length_tabix, chrom, step=step, precision=precision)

    assert length is not None
    assert last_pos < length <= last_pos + precision
