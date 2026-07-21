# pylint: disable=redefined-outer-name
"""Benchmark of the bigWig region-fetch path, in microseconds per record.

This is a *reporting* benchmark, not a gating test -- see
``test_score_read_path_benchmark`` for why a wall-clock threshold would only
flake on the Jenkins agents' ~2.7x spread.  What it reports is the cost of the
change made in #259: the bigWig backend used to serve a region by issuing one
``pyBigWig.intervals()`` call per **50 base pairs**, so a wide region cost one
range query per 50 bp of it regardless of how many records that covered.  The
window is now retuned toward a target number of records per call.

Three numbers are reported over the same dense 1 Mb / 20,000-interval fixture:

* the **score-layer region fetch** (``PositionScore.fetch_region_values``
  across the whole megabase) -- the end-to-end cost callers actually pay;
* the **old fixed 50 bp walk**, replayed here as a raw ``intervals()`` loop
  over the same range, which is the I/O the backend used to issue; and
* a **single unchunked call** over the whole range, the floor that no chunking
  strategy can beat.

The ratio of the second to the first is the improvement this issue claims.
Measuring the old walk as raw I/O (rather than keeping a deprecated code path
alive to time) keeps the comparison honest in the only dimension that changed:
how many range queries reach the file.

Every pass is measured cold: :func:`_cold_reset` clears the table's buffer and
the adaptive windows, and rewinds the buffered/direct switch, so no pass can
replay state another pass warmed.

Output visibility follows the sibling benchmark: the report goes through
``capsys.disabled()`` so it shows up under a plain ``pytest -v``, and is
swallowed by ``pytest -n`` (xdist) workers -- run single-threaded to see it.
"""
from __future__ import annotations

import pathlib
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass

import pytest
from gain.genomic_resources.genomic_position_table.table_bigwig import (
    AdaptiveFetchWindow,
    BigWigTable,
)
from gain.genomic_resources.genomic_scores import (
    PositionScore,
    build_score_from_resource,
)
from gain.genomic_resources.testing.builders import (
    a_bigwig_score,
    a_grr,
)
from gain.utils.regions import Region

# The case the issue quotes: 1 Mb carrying 20,000 back-to-back 50 bp runs.
# At the old 50 bp chunk size that is exactly one range query per record.
_INTERVAL_WIDTH = 50
_INTERVAL_COUNT = 20_000
_REGION_LEN = _INTERVAL_WIDTH * _INTERVAL_COUNT
_CHROM = "chr1"

# The chunk size the backend used before #259, replayed for comparison.
_LEGACY_CHUNK_BP = 50

# Kept small on purpose: a single pass of the legacy walk issues 20,000 range
# queries and takes well over a second, so the pass count is what keeps the
# whole benchmark inside the integration tier's budget.
_WARMUP_PASSES = 1
_TIMED_PASSES = 3


@dataclass(frozen=True)
class _Timing:
    """Per-record timing summary over the timed passes, in microseconds."""

    median_us: float
    min_us: float
    max_us: float


def _dense_bedgraph() -> str:
    return "\n".join(
        f"{_CHROM}  {i * _INTERVAL_WIDTH}  {(i + 1) * _INTERVAL_WIDTH}  "
        f"{i % 97 / 100.0 + 0.01:.2f}"
        for i in range(_INTERVAL_COUNT)
    )


@pytest.fixture(scope="module")
def dense_bigwig_score(
    tmp_path_factory: pytest.TempPathFactory,
) -> PositionScore:
    """A bigWig ``position_score`` of 20,000 50 bp runs over 1 Mb."""
    tmp_path: pathlib.Path = tmp_path_factory.mktemp("bigwig_dense")
    builder = (
        a_bigwig_score()
        .with_score("bw", "float")
        .with_data(_dense_bedgraph())
        .with_chrom_lens({_CHROM: _REGION_LEN})
    )
    repo = a_grr().with_resource("bw", builder).build_repo(tmp_path)
    score = build_score_from_resource(repo.get_resource("bw")).open()
    assert isinstance(score, PositionScore)
    return score


def _cold_reset(score: PositionScore) -> Callable[[], None]:
    """Return a callable that puts the bigWig table back in a cold state.

    Clears the buffer, rewinds the buffered/direct switch so every pass takes
    the direct strategy, and rebuilds the adaptive windows so no pass inherits
    the density another pass already learned.
    """
    table = score.table
    assert isinstance(table, BigWigTable)

    def reset() -> None:
        table._buffer = []
        table._buffer_region = Region("?", -1, -1)
        table._last_pos = -(table.use_buffered_threshold + 1)
        table._direct_window = AdaptiveFetchWindow(table.direct_fetch_size)
        table._buffer_window = AdaptiveFetchWindow(table.buffer_fetch_size)

    return reset


def _measure(
    work: Callable[[], int],
    reset: Callable[[], None],
    expected_records: int,
) -> _Timing:
    """Time ``work`` over warmup + timed passes; return the timing summary.

    Every pass must consume ``expected_records``, so the benchmark can never
    report a fast number for a loop that dropped records.
    """
    for _ in range(_WARMUP_PASSES):
        reset()
        assert work() == expected_records
    samples: list[float] = []
    for _ in range(_TIMED_PASSES):
        reset()
        start = time.perf_counter()
        consumed = work()
        elapsed = time.perf_counter() - start
        assert consumed == expected_records
        samples.append(elapsed / expected_records * 1e6)
    return _Timing(
        median_us=statistics.median(samples),
        min_us=min(samples),
        max_us=max(samples),
    )


def _fetch_pass(score: PositionScore) -> Callable[[], int]:
    """A score-layer region fetch across the whole 1 Mb region."""
    def run() -> int:
        count = 0
        for _left, _right, values in score.fetch_region_values(
            _CHROM, 1, _REGION_LEN,
        ):
            count += 1
            assert values is not None
            assert values[0] is not None
        return count
    return run


def _legacy_walk_pass(score: PositionScore) -> Callable[[], int]:
    """Replay the pre-#259 I/O: one ``intervals()`` call per 50 bp."""
    table = score.table
    assert isinstance(table, BigWigTable)

    def run() -> int:
        bw_file = table._bw_file
        assert bw_file is not None
        count = 0
        start = 0
        while start < _REGION_LEN:
            stop = min(start + _LEGACY_CHUNK_BP, _REGION_LEN)
            intervals = bw_file.intervals(_CHROM, start, stop)
            if not intervals:
                start = stop
                continue
            count += len(intervals)
            start = intervals[-1][1]
        return count
    return run


def _single_call_pass(score: PositionScore) -> Callable[[], int]:
    """The floor: one unchunked ``intervals()`` call over the whole range."""
    table = score.table
    assert isinstance(table, BigWigTable)

    def run() -> int:
        bw_file = table._bw_file
        assert bw_file is not None
        return len(bw_file.intervals(_CHROM, 0, _REGION_LEN))
    return run


def test_bigwig_region_fetch_benchmark(
    dense_bigwig_score: PositionScore,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Report us/record for a wide bigWig region fetch and its old baseline.

    Reports only -- the assertions check that each pass really consumed all
    20,000 records, not that any of them was fast.
    """
    score = dense_bigwig_score
    reset = _cold_reset(score)

    fetch = _measure(_fetch_pass(score), reset, _INTERVAL_COUNT)
    legacy = _measure(_legacy_walk_pass(score), reset, _INTERVAL_COUNT)
    single = _measure(_single_call_pass(score), reset, _INTERVAL_COUNT)

    speedup = legacy.median_us / fetch.median_us

    with capsys.disabled():
        print(
            f"\n[bigwig region-fetch benchmark] "
            f"{_INTERVAL_COUNT} x {_INTERVAL_WIDTH} bp runs over "
            f"{_REGION_LEN} bp:\n"
            f"    score-layer region fetch    = "
            f"{fetch.median_us:8.3f} us/record"
            f"  (min {fetch.min_us:7.3f}, max {fetch.max_us:7.3f})\n"
            f"    old fixed {_LEGACY_CHUNK_BP} bp walk (I/O)  = "
            f"{legacy.median_us:8.3f} us/record"
            f"  (min {legacy.min_us:7.3f}, max {legacy.max_us:7.3f})\n"
            f"    single unchunked call (I/O) = "
            f"{single.median_us:8.3f} us/record"
            f"  (min {single.min_us:7.3f}, max {single.max_us:7.3f})\n"
            f"    fetch vs. old walk          = {speedup:8.1f}x",
        )
