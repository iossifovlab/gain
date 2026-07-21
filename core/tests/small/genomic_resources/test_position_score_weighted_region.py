# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""The score layer owns the weight of a region record (#260)."""

from typing import Any

import pytest
from gain.genomic_resources import GenomicResource
from gain.genomic_resources.aggregators import Aggregator
from gain.genomic_resources.genomic_scores import PositionScore
from gain.genomic_resources.repository import GR_CONF_FILE_NAME
from gain.genomic_resources.testing import build_inmemory_test_resource


@pytest.fixture
def position_score() -> PositionScore:
    res: GenomicResource = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
            scores:
              - id: test100way
                type: float
                desc: "test values"
                name: s1""",
        "data.mem": """
            chrom  pos_begin  pos_end  s1
            1      10         19       1.0
            1      20         29       2.0
            1      30         39       3.0
            """,
    })
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


def test_fetch_scores_agg_adds_each_record_once(
    position_score: PositionScore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Any, int]] = []
    original_add = Aggregator.add

    def spy(self: Aggregator, value: Any, count: int = 1) -> None:
        calls.append((value, count))
        original_add(self, value, count)

    monkeypatch.setattr(Aggregator, "add", spy)

    aggregators = position_score.fetch_scores_agg("1", 15, 24)

    assert calls == [(1.0, 5), (2.0, 5)]
    assert aggregators[0].get_final() == 1.5
