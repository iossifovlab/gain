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
time -- the right record count, and real (non-``None``) parsed values --
so it can never silently report a fast number for an empty loop.

The report is emitted through ``capsys.disabled()`` so it is visible in a
passing run under ``pytest -v`` (the integration job runs without ``-s``,
which would otherwise capture and hide a plain ``print``).
"""
from __future__ import annotations

import pathlib
import time
from collections.abc import Callable

import pytest
from gain.genomic_resources.genomic_scores import (
    GenomicScore,
    build_score_from_resource,
)
from gain.genomic_resources.testing.builders import (
    PositionScoreBuilder,
    a_grr,
    a_position_score,
)

# Sizes are chosen so the whole benchmark runs in a couple of seconds while
# still timing enough records that microseconds-per-record is stable to a
# couple of significant figures.
_NARROW_ROWS = 4000
_WIDE_ROWS = 1500
_WIDE_SCORES = 60

# Timed passes per measurement; the reported figure is the fastest pass
# (least perturbed by scheduler/GC noise), after one un-timed warmup pass.
_WARMUP_PASSES = 1
_TIMED_PASSES = 3

_CHROM = "1"


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


def _best_us_per_record(
    work: Callable[[], int], expected_records: int,
) -> float:
    """Run ``work`` warmup+timed passes; return best microseconds/record.

    ``work`` performs one full pass and returns the number of records it
    consumed; every pass must consume ``expected_records`` or the benchmark
    is timing something other than what it claims to.
    """
    for _ in range(_WARMUP_PASSES):
        assert work() == expected_records
    best = float("inf")
    for _ in range(_TIMED_PASSES):
        start = time.perf_counter()
        consumed = work()
        elapsed = time.perf_counter() - start
        assert consumed == expected_records
        best = min(best, elapsed / expected_records * 1e6)
    return best


def _scan_pass(score: GenomicScore) -> Callable[[], int]:
    """A raw table scan: iterate records straight off the table.

    Touches ``pos_begin`` on every record so the parse is not dead code the
    interpreter could skip.
    """
    def run() -> int:
        count = 0
        checksum = 0
        for record in score.table.get_all_records():
            count += 1
            checksum += record.pos_begin
        assert checksum > 0
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
    (record counts and non-``None`` values) that keep the reported number
    honest.
    """
    if shape == "narrow":
        score, n_rows, n_scores = narrow_score, _NARROW_ROWS, 1
    else:
        score, n_rows, n_scores = wide_score, _WIDE_ROWS, _WIDE_SCORES

    scan_us = _best_us_per_record(_scan_pass(score), n_rows)
    fetch_us = _best_us_per_record(
        _fetch_pass(score, n_rows, n_scores), n_rows)

    with capsys.disabled():
        print(
            f"\n[score-read-path benchmark] {shape:<6} "
            f"({n_rows} rows x {n_scores} score(s)): "
            f"raw table scan = {scan_us:7.3f} us/record | "
            f"score-layer region fetch = {fetch_us:7.3f} us/record",
        )
