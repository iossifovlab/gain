# pylint: disable=redefined-outer-name
"""Benchmark of region aggregation, in milliseconds per annotated region.

A *reporting* benchmark, not a gating test -- see
``test_score_read_path_benchmark`` for why a wall-clock threshold would
only flake on the Jenkins agents' ~2.7x spread.

What it reports is the cost of the change made in #260.  The
position-score annotator used to aggregate a region by *replicating*: a
record covering N base pairs of the queried region became N identical
copies in a Python list, so aggregating a region cost one aggregator step
per base pair.  A record now reaches the aggregator once, carrying its
weight, so the cost is one step per **record**.

Two numbers are reported over the same fixture -- the case the issue
quotes, a 500 kb region backed by 2,000 records:

* the **weighted annotate**, end to end through the pipeline, which is
  what a caller pays today; and
* the **replicated aggregation**, replayed here over the same score-layer
  fetch, which is the work the annotator used to do.

Both pay the same score-layer fetch, so the difference between them is
exactly the aggregation strategy.  Replaying the old strategy as raw work
(rather than keeping a dead code path alive to time) keeps the comparison
honest in the only dimension that changed.

The two results are asserted to agree to within a relative 1e-9, which is
the accuracy claim of #260 stated as a test: the weighted mean is *not*
bit-identical to the replicated one -- it rounds once per record instead
of once per base pair, which is the more accurate operation -- but it is
far inside any tolerance a consumer could care about.

Output visibility follows the sibling benchmarks: the report goes through
``capsys.disabled()`` so it shows up under a plain ``pytest -v``, and is
swallowed by ``pytest -n`` (xdist) workers -- run single-threaded to see
it.
"""
from __future__ import annotations

import pathlib
import statistics
import textwrap
import time
from collections.abc import Callable
from dataclasses import dataclass

import pytest
from gain.annotation.annotatable import Region
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.annotation.annotation_pipeline import AnnotationPipeline
from gain.genomic_resources.aggregators import MeanAggregator
from gain.genomic_resources.genomic_scores import PositionScore
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing.builders import (
    a_grr,
    a_position_score,
)

# The case the issue quotes: a 500 kb region carrying 2,000 back-to-back
# 250 bp records.  Replicated, that is 500,000 aggregator steps; weighted,
# it is 2,000.
_RECORD_COUNT = 2_000
_RECORD_SPAN = 250
_REGION_LEN = _RECORD_COUNT * _RECORD_SPAN
_CHROM = "chr1"
_RESOURCE_ID = "dense_position_score"
_SCORE_ID = "test100way"

_WARMUP_PASSES = 1
_TIMED_PASSES = 5


@dataclass(frozen=True)
class _Timing:
    """Per-region timing summary over the timed passes, in milliseconds."""

    median_ms: float
    min_ms: float
    max_ms: float


def _dense_data() -> str:
    """``chrom pos_begin pos_end score``, 1-based, back-to-back records."""
    lines = ["chrom  pos_begin  pos_end  score"]
    for i in range(_RECORD_COUNT):
        begin = 1 + i * _RECORD_SPAN
        end = begin + _RECORD_SPAN - 1
        lines.append(f"{_CHROM}  {begin}  {end}  {i % 100 / 100.0 + 0.01:.3f}")
    return "\n".join(lines) + "\n"


@pytest.fixture(scope="module")
def dense_repo(
    tmp_path_factory: pytest.TempPathFactory,
) -> GenomicResourceRepo:
    """A tabix position score of ``_RECORD_COUNT`` wide records."""
    tmp_path: pathlib.Path = tmp_path_factory.mktemp("dense")
    builder = (
        a_position_score()
        .with_tabix()
        .with_score(_SCORE_ID, "float", column_name="score")
        .with_data(_dense_data())
    )
    return a_grr().with_resource(
        _RESOURCE_ID, builder).build_repo(tmp_path)


def _measure(work: Callable[[], float]) -> tuple[_Timing, float]:
    """Time ``work`` over warmup + timed passes; return timing and result.

    Every pass must produce the same result, so the benchmark can never
    report a fast number for a loop that quietly did less work.
    """
    expected = work()
    for _ in range(_WARMUP_PASSES):
        assert work() == expected
    samples: list[float] = []
    for _ in range(_TIMED_PASSES):
        start = time.perf_counter()
        result = work()
        elapsed = time.perf_counter() - start
        assert result == expected
        samples.append(elapsed * 1e3)
    return _Timing(
        median_ms=statistics.median(samples),
        min_ms=min(samples),
        max_ms=max(samples),
    ), expected


def _weighted_pass(pipeline: AnnotationPipeline) -> Callable[[], float]:
    """Annotate the whole region through the pipeline, as a caller would."""
    region = Region(_CHROM, 1, _REGION_LEN)

    def run() -> float:
        result = pipeline.annotate(region)
        value = result["test100"]
        assert isinstance(value, float)
        return value

    return run


def _replicated_pass(score: PositionScore) -> Callable[[], float]:
    """Replay the pre-#260 strategy: one copy of a value per base pair."""
    def run() -> float:
        raw: list[float] = []
        for left, right, values in score.fetch_region_values(
            _CHROM, 1, _REGION_LEN, [_SCORE_ID],
        ):
            if values is None:
                continue
            raw.extend([values[0]] * (right - left + 1))  # type: ignore[misc]
        assert len(raw) == _REGION_LEN
        value = MeanAggregator().aggregate(raw)
        assert isinstance(value, float)
        return value

    return run


def test_region_aggregation_benchmark(
    dense_repo: GenomicResourceRepo,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Report ms/region for the weighted and the replicated aggregation.

    Reports only -- see the module docstring for why this benchmark never
    asserts a timing threshold.  It does assert that both strategies
    consumed the whole region and agree on its mean.
    """
    pipeline = load_pipeline_from_yaml(textwrap.dedent(f"""
        - position_score:
            resource_id: {_RESOURCE_ID}
            region_length_cutoff: {_REGION_LEN}
            attributes:
            - source: {_SCORE_ID}
              name: test100
              aggregator: mean
        """), dense_repo)

    with pipeline:
        weighted, weighted_mean = _measure(_weighted_pass(pipeline))

        score = PositionScore(dense_repo.get_resource(_RESOURCE_ID)).open()
        replicated, replicated_mean = _measure(_replicated_pass(score))
        score.close()

    assert weighted_mean == pytest.approx(replicated_mean, rel=1e-9)

    with capsys.disabled():
        print(
            f"\n[region-aggregation benchmark] "
            f"{_REGION_LEN} bp region x {_RECORD_COUNT} records:\n"
            f"    weighted annotate     = "
            f"{weighted.median_ms:8.3f} ms/region"
            f"  (min {weighted.min_ms:7.3f}, max {weighted.max_ms:7.3f})\n"
            f"    replicated (pre-#260) = "
            f"{replicated.median_ms:8.3f} ms/region"
            f"  (min {replicated.min_ms:7.3f}, "
            f"max {replicated.max_ms:7.3f})\n"
            f"    speed-up              = "
            f"{replicated.median_ms / weighted.median_ms:8.1f}x",
        )
