#!/usr/bin/env python
"""Time the fused ONNX graph against the five-session ONNX ensemble.

Compares the two ONNX backends of the SpliceAI annotator on the same input:

* ``onnx``: five ONNX Runtime sessions, the mean taken in NumPy.
* ``onnx-fused``: one session over ``models/spliceai_fused.onnx``, which
  holds the five members and their mean in one graph.

It calls the shipped ``spliceai_load_models`` and ``spliceai_predict`` of
each backend, so the session options, the ``ONNX_POSITION_BUDGET`` chunking
and the ``SPLICEAI_ONNX_INTRA_OP_THREADS`` knob are the ones an annotation
run uses.

For each backend and each window width the script reports:

* the median wall time of one ``spliceai_predict`` pass over ``--reps``
  repetitions, after one untimed warm-up pass;
* the peak RSS of the process.

Each configuration runs in its own ``spawn`` process. ``ru_maxrss`` is a
high-water mark that never falls, so two configurations in one process
would share the peak of the larger one. A spawned child also starts without
the parent's state.

The default widths are the annotator's default window (``distance`` 50,
10101 positions) and the widest window of a default deployment (``distance``
5000, 20201 positions). The input is a synthetic one-hot batch, built
through the shipped ``one_hot_encode`` with a seed derived from the width,
so both backends get the same input.

The script prints the CPU model, the CPU count and the load average. A busy
host measures its other work too, so run it on a quiet host.

Usage
-----
    python spliceai_annotator/scripts/measure_onnx_fused.py
    python spliceai_annotator/scripts/measure_onnx_fused.py \\
        --widths 10101 20201 --rows 32 --reps 5 --out result.json
"""
from __future__ import annotations

import argparse
import importlib
import json
import multiprocessing
import os
import pathlib
import platform
import resource
import statistics
import time
from collections.abc import Sequence
from typing import Any, cast

import numpy as np
from spliceai_annotator.utils import one_hot_encode

BACKEND_MODULES = {
    "onnx": "spliceai_annotator.spliceai_backend_onnx",
    "onnx-fused": "spliceai_annotator.spliceai_backend_onnx_fused",
}
DEFAULT_WIDTHS = (10101, 20201)
DEFAULT_ROWS = 32
DEFAULT_REPS = 5


def synthetic_input(rows: int, width: int) -> np.ndarray:
    """A one-hot window batch shaped and typed as the annotator's.

    Random bases: the cost of a convolution depends on the shape of its
    input, not on the bases. The seed is derived from the width, so a width
    gets the same input in every process.
    """
    rng = np.random.default_rng(seed=width)
    return np.concatenate([
        one_hot_encode("".join(rng.choice(list("ACGT"), size=width)))[None, :]
        for _ in range(rows)
    ], axis=0)


def _measure_child(
    backend: str, width: int, rows: int, reps: int,
    queue: multiprocessing.Queue,
) -> None:
    """Load ``backend``, time ``reps`` passes, and report to ``queue``."""
    module = importlib.import_module(BACKEND_MODULES[backend])
    x = synthetic_input(rows, width)
    models = module.spliceai_load_models()
    try:
        module.spliceai_predict(models, x)
        times = []
        for _ in range(reps):
            start = time.perf_counter()
            module.spliceai_predict(models, x)
            times.append((time.perf_counter() - start) * 1000.0)
    finally:
        module.spliceai_close()
    queue.put({
        "backend": backend,
        "width": width,
        "rows": rows,
        "reps": reps,
        "median_ms_per_pass": round(statistics.median(times), 1),
        # ru_maxrss is kilobytes on Linux.
        "peak_rss_mb": round(
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
        "pid": os.getpid(),
    })


def run_in_child(
    backend: str, width: int, rows: int, reps: int,
) -> dict[str, Any]:
    """Run one configuration in a fresh ``spawn`` process."""
    context = multiprocessing.get_context("spawn")
    queue: multiprocessing.Queue = context.Queue()
    child = context.Process(
        target=_measure_child, args=(backend, width, rows, reps, queue))
    child.start()
    result = queue.get()
    child.join()
    if child.exitcode != 0:
        raise RuntimeError(
            f"{backend} @ {width} exited with code {child.exitcode}")
    return cast(dict[str, Any], result)


def cpu_model() -> str:
    """The CPU model name, or a best effort where /proc is not available."""
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as cpuinfo:
            for line in cpuinfo:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def format_table(results: list[dict[str, Any]]) -> str:
    """Render the results as a markdown table, one row per configuration."""
    lines = [
        "| backend | width | median ms per pass | peak RSS MB |",
        "|---|---|---|---|",
    ]
    lines.extend(
        f"| {r['backend']} | {r['width']} | {r['median_ms_per_pass']} "
        f"| {r['peak_rss_mb']} |"
        for r in results)
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> None:
    """Measure every backend at every width and print the table."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--widths", type=int, nargs="+", default=list(DEFAULT_WIDTHS))
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS)
    parser.add_argument("--reps", type=int, default=DEFAULT_REPS)
    parser.add_argument(
        "--out", type=pathlib.Path, default=None,
        help="also write the results as JSON to this path")
    args = parser.parse_args(argv)

    loadavg_before = os.getloadavg()
    results = [
        run_in_child(backend, width, args.rows, args.reps)
        for width in args.widths
        for backend in BACKEND_MODULES
    ]
    loadavg = os.getloadavg()

    threads = os.environ.get("SPLICEAI_ONNX_INTRA_OP_THREADS", "default")
    print(f"cpu: {cpu_model()}, cpu count {os.cpu_count()}")
    print(
        f"load average before: {' '.join(f'{v:.2f}' for v in loadavg_before)}"
        f", after: {' '.join(f'{v:.2f}' for v in loadavg)}")
    print(
        f"rows {args.rows}, reps {args.reps}, "
        f"SPLICEAI_ONNX_INTRA_OP_THREADS {threads}\n")
    print(format_table(results))

    if args.out is not None:
        args.out.write_text(json.dumps({
            "cpu_model": cpu_model(),
            "cpu_count": os.cpu_count(),
            "loadavg_before": list(loadavg_before),
            "loadavg": list(loadavg),
            "intra_op_threads": threads,
            "results": results,
        }, indent=2))
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
