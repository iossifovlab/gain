# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""The score layer owns the weight of a region record (#260)."""

import pathlib

import pytest
from gain.genomic_resources.genomic_scores import PositionScore
from gain.genomic_resources.testing.builders import a_position_score


@pytest.fixture
def position_score(tmp_path: pathlib.Path) -> PositionScore:
    res = (
        a_position_score()
        .with_score("test100way", "float", column_name="s1",
                    desc="test values")
        .with_data("""
            chrom  pos_begin  pos_end  s1
            1      10         19       1.0
            1      20         29       2.0
            1      30         39       3.0
        """)
        .build_resource(tmp_path)
    )
    return PositionScore(res).open()


def test_a_records_weight_is_the_bases_it_spans(
    position_score: PositionScore,
) -> None:
    assert list(position_score.fetch_region_weighted_values(
        "1", 10, 39, ["test100way"])) == [
            ([1.0], 10), ([2.0], 10), ([3.0], 10),
    ]


def test_a_records_weight_counts_only_the_queried_part(
    position_score: PositionScore,
) -> None:
    assert list(position_score.fetch_region_weighted_values(
        "1", 15, 24, ["test100way"])) == [
            ([1.0], 5), ([2.0], 5),
    ]
