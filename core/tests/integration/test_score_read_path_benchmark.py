# pylint: disable=redefined-outer-name
"""Benchmark of the genomic-score read path, in microseconds per record.

This is a *reporting* benchmark, not a gating test.  It measures the cost
of the two hot read paths that two upcoming optimisations target:

* a **raw table scan** -- iterating records straight off the tabix-backed
  ``GenomicPositionTable`` (``table.get_all_records()``), and
* a **score-layer region fetch** -- pulling parsed score values through
  ``GenomicScore.fetch_region_values``, which wraps every record in a
  ``ScoreLine`` and calls ``ScoreLine.get_score`` per declared score.

Both are measured against two resource shapes, because the two follow-up
optimisations help them very differently:

* a **narrow** single-score resource (the ``ScoreLine``/tuple-adapter cost
  dominates), and
* a **wide** many-score resource (the per-column score-definition lookup
  dominates).

Every timed pass is measured **cold**
--------------------------------------
``TabixGenomicPositionTable`` keeps a ``LineBuffer`` (``BUFFER_MAXSIZE``
= 20000 records) that a repeated same-region fetch replays *without*
re-reading the tabix file or rebuilding a single ``Line``.  Both fixtures
fit entirely in that buffer, so a naive "warm up once, then time N passes
over the same region" would time buffer replay -- pysam decode and ``Line``
construction happen only in the untimed warmup and the ``Line``->tuple
optimisation would be invisible in the fetch number.  To keep the fetch
symmetric with the always-cold raw scan (``get_all_records`` clears the
buffer and re-runs ``pysam.fetch`` every pass), :func:`_cold_reset` clears
the table's ``LineBuffer`` and its ``_last_call`` short-circuit state
*before* each pass, outside the timed region, forcing a real tabix read
and fresh ``Line`` objects every time.

Why this test never asserts a wall-clock threshold
--------------------------------------------------
The Jenkins agents in this project differ in raw speed by up to ~2.7x
(measured across ~60 green master builds: eyoree < piglet < pooh).  An
absolute microseconds-per-record threshold would therefore flake purely on
which agent picked up the build, and a relative threshold would need a
same-build baseline this benchmark does not have.  So correctness gates CI
and the timing only *informs* the reviewer: the numbers are printed into
the build log for a human to compare across commits, and the assertions
below check only that the benchmark actually did the work it claims to
time -- the exact record count, the exact parsed ``pos_begin`` sum, and
real (non-``None``) parsed values -- so it can never silently report a
fast number for an empty loop.

The reported figure is the **median** over :data:`_TIMED_PASSES` passes,
with the observed ``min`` and ``max`` printed alongside so a reader can
judge whether a cross-commit difference is signal or noise.  Median (not
"best of N") is used because best-of-N is biased toward the luckiest pass
and so is the wrong statistic for comparing two commits; the median is
robust to the occasional scheduler/GC spike (which surfaces in ``max``).

Output visibility (``-s`` and ``-n``)
-------------------------------------
The report is emitted through ``capsys.disabled()`` so it is visible in a
passing run under ``pytest -v`` (the integration job runs without ``-s``,
which would otherwise capture and hide a plain ``print``).  Note that under
``pytest -n`` (xdist) the xdist workers swallow this output entirely and
the numbers will NOT appear, even though the test still passes -- run this
benchmark single-threaded (as ``core/Jenkinsfile.integration`` does) to see
the report.
"""
from __future__ import annotations

import pathlib
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass

import pytest
from gain.genomic_resources.genomic_position_table.table_tabix import (
    TabixGenomicPositionTable,
)
from gain.genomic_resources.genomic_scores import (
    GenomicScore,
    build_score_from_resource,
)
from gain.genomic_resources.testing.builders import (
    PositionScoreBuilder,
    a_grr,
    a_position_score,
)

# Fixture sizes.  The wide shape carries ~454 scores to mirror the real
# resource the ``Line``->tuple and score-def-hoist optimisations target
# (dbNSFP, ~454 scores); the hoist's payoff scales with the score count, so
# a 60-score fixture would understate it.  Row counts are set so each timed
# pass runs long enough (tens to ~200 ms) that its microseconds-per-record
# is stable to a couple of significant figures -- the earlier tiny fixtures
# were noisy (~24% spread) because a pass was too short to average out
# scheduler/GC jitter.  The whole test (both shapes, build + all passes)
# runs in a handful of seconds, well inside the integration tier's budget.
_NARROW_ROWS = 4000
_WIDE_ROWS = 1000
_WIDE_SCORES = 454

# Untimed warmup passes, then timed passes whose median is reported.
_WARMUP_PASSES = 2
_TIMED_PASSES = 9

_CHROM = "1"


@dataclass(frozen=True)
class _Timing:
    """Per-record timing summary over the timed passes, in microseconds."""

    median_us: float
    min_us: float
    max_us: float


def _narrow_data(n_rows: int) -> str:
    """Single-score table: ``chrom pos_begin score`` at 1-based positions."""
    lines = ["chrom  pos_begin  score"]
    lines.extend(
        f"{_CHROM}  {pos}  {pos % 100 / 100.0 + 0.01:.3f}"
        for pos in range(1, n_rows + 1)
    )
    return "\n".join(lines) + "\n"


def _wide_data(n_rows: int, n_scores: int) -> str:
    """Many-score table: ``chrom pos_begin s0 s1 ... s{n-1}``."""
    header = ["chrom", "pos_begin", *(f"s{j}" for j in range(n_scores))]
    lines = ["  ".join(header)]
    for pos in range(1, n_rows + 1):
        cells = [_CHROM, str(pos)]
        cells.extend(
            f"{(pos + j) % 100 / 100.0 + 0.01:.3f}" for j in range(n_scores))
        lines.append("  ".join(cells))
    return "\n".join(lines) + "\n"


def _open_score(
    tmp_path: pathlib.Path, resource_id: str, builder: PositionScoreBuilder,
) -> GenomicScore:
    repo = a_grr().with_resource(resource_id, builder).build_repo(tmp_path)
    return build_score_from_resource(repo.get_resource(resource_id)).open()


@pytest.fixture(scope="module")
def narrow_score(
    tmp_path_factory: pytest.TempPathFactory,
) -> GenomicScore:
    """A tabix single-score position resource with ``_NARROW_ROWS`` rows."""
    tmp_path = tmp_path_factory.mktemp("narrow")
    builder = (
        a_position_score()
        .with_tabix()
        .with_score("score", "float")
        .with_data(_narrow_data(_NARROW_ROWS))
    )
    return _open_score(tmp_path, "narrow", builder)


@pytest.fixture(scope="module")
def wide_score(
    tmp_path_factory: pytest.TempPathFactory,
) -> GenomicScore:
    """A tabix many-score position resource with ``_WIDE_SCORES`` columns."""
    tmp_path = tmp_path_factory.mktemp("wide")
    builder = a_position_score().with_tabix()
    for j in range(_WIDE_SCORES):
        builder = builder.with_score(f"s{j}", "float", column_name=f"s{j}")
    builder = builder.with_data(_wide_data(_WIDE_ROWS, _WIDE_SCORES))
    return _open_score(tmp_path, "wide", builder)


def _cold_reset(score: GenomicScore) -> Callable[[], None]:
    """Return a callable that forces the next read to hit the tabix file.

    Clears the table's ``LineBuffer`` and its ``_last_call`` short-circuit
    so ``get_records_in_region`` cannot replay a warm buffer -- every timed
    fetch pass then pays the real pysam decode + ``Line`` construction cost,
    symmetric with the always-cold raw scan.  The raw scan does not touch
    the buffer, so for it this is a cheap no-op; either way it runs *outside*
    the timed region.
    """
    table = score.table
    assert isinstance(table, TabixGenomicPositionTable)

    def reset() -> None:
        table.buffer.clear()
        table._last_call = "", -1, -1

    return reset


def _measure(
    work: Callable[[], int],
    reset: Callable[[], None],
    expected_records: int,
) -> _Timing:
    """Time ``work`` over warmup + timed passes; return the timing summary.

    ``reset`` runs before every pass, outside the timed region, to put the
    table back in a cold state.  ``work`` performs one full pass and returns
    the number of records it consumed; every pass must consume
    ``expected_records`` or the benchmark is timing something other than
    what it claims to.
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


def _scan_pass(score: GenomicScore, n_rows: int) -> Callable[[], int]:
    """A raw table scan: iterate records straight off the table.

    Sums ``pos_begin`` over every record and checks it against the exact
    arithmetic-series total for positions ``1..n_rows``.  That both keeps
    the parse from being dead code the interpreter could skip AND acts as a
    real guard: it fails unless ``pos_begin`` was actually parsed correctly
    on every record, so the benchmark cannot time a loop that skips the
    parse or drops rows.
    """
    expected_checksum = n_rows * (n_rows + 1) // 2

    def run() -> int:
        count = 0
        checksum = 0
        for record in score.table.get_all_records():
            count += 1
            checksum += record.pos_begin
        assert checksum == expected_checksum
        return count

    return run


def _fetch_pass(
    score: GenomicScore, n_rows: int, n_scores: int,
) -> Callable[[], int]:
    """A score-layer region fetch across the whole chromosome.

    Asserts every row yields a parsed value for every declared score, so a
    silent regression to all-``None`` (which would make the fetch look fast)
    fails the benchmark instead of being reported as a speed-up.
    """
    def run() -> int:
        count = 0
        for _left, _right, values in score.fetch_region_values(
            _CHROM, 1, n_rows,
        ):
            count += 1
            assert values is not None
            assert len(values) == n_scores
            assert all(v is not None for v in values)
        return count
    return run


@pytest.mark.parametrize("shape", ["narrow", "wide"])
def test_score_read_path_benchmark(
    shape: str,
    narrow_score: GenomicScore,
    wide_score: GenomicScore,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Report us/record for the raw scan and the region fetch of one shape.

    Reports only -- see the module docstring for why this benchmark never
    asserts a timing threshold.  It does assert the correctness invariants
    (exact record counts, exact ``pos_begin`` sum, and non-``None`` values)
    that keep the reported number honest.
    """
    if shape == "narrow":
        score, n_rows, n_scores = narrow_score, _NARROW_ROWS, 1
    else:
        score, n_rows, n_scores = wide_score, _WIDE_ROWS, _WIDE_SCORES

    reset = _cold_reset(score)
    scan = _measure(_scan_pass(score, n_rows), reset, n_rows)
    fetch = _measure(_fetch_pass(score, n_rows, n_scores), reset, n_rows)

    with capsys.disabled():
        print(
            f"\n[score-read-path benchmark] {shape:<6} "
            f"({n_rows} rows x {n_scores} score(s)):\n"
            f"    raw table scan           = {scan.median_us:8.3f} us/record"
            f"  (min {scan.min_us:7.3f}, max {scan.max_us:7.3f})\n"
            f"    score-layer region fetch = {fetch.median_us:8.3f} us/record"
            f"  (min {fetch.min_us:7.3f}, max {fetch.max_us:7.3f})",
        )
