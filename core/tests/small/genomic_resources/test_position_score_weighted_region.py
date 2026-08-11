# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""The score layer owns the weight of a region record (#260)."""

import pathlib
from collections.abc import Generator

import pytest
from gain.genomic_resources.genomic_scores import (
    PositionScore,
)
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


def test_a_record_the_query_clips_to_nothing_is_not_yielded(
    position_score: PositionScore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-positive weight never reaches the caller (gain#639).

    ``fetch_region_segment_scores`` deliberately yields an out-of-region
    record through, and clipping it inverts its span (or zeroes it, for a
    record starting one past the query).  The misconfigured backend this
    implies is refused at ``open()`` only where the defect is visible
    there; one whose index cannot be decoded is declined with a warning
    and reads on unvalidated (gain#553, ADR 0008).  This is the read
    ``PositionScoreAnnotator`` aggregates from, and annotation never
    scans, so this skip is what stands between such a backend and a
    non-positive weight in an aggregator.
    """
    def outside(
        chrom: str,
        pos_begin: int | None = None,
        pos_end: int | None = None,
        scores: list[str] | None = None,
    ) -> Generator[tuple[int, int, list[float]], None, None]:
        # a [20, 25] record, clipped by the region read to a [10, 16] query
        yield (20, 16, [9.9])
        # a [17, ...] record, clipped to a ZERO span -- pins the <= of the
        # skip's `weight <= 0`, which the inverted record alone would let
        # weaken to `< 0`
        yield (17, 16, [5.5])
        yield (10, 14, [1.0])

    monkeypatch.setattr(
        position_score, "fetch_region_segment_scores", outside)

    assert list(position_score.fetch_region_weighted_values(
        "1", 10, 16, ["test100way"])) == [([1.0], 5)]
