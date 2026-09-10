#!/usr/bin/env python
"""What chunk size should the TensorFlow backend hand to ``predict``? (#427)

``Model.predict`` splits its input along the batch axis and evaluates one
chunk at a time. The size of that chunk is ``batch_size``, and when it is left
unset Keras picks **32**. The backend never set it, so every annotation ran at
32 -- which #400's sweep found to be close to the worst value available, ~1.9x
slower per window than 8-16 at the default window width.

This script is the measurement behind the constant that replaces that default.

What it measures -- and what it does not
----------------------------------------
The input is held **fixed** and only ``batch_size`` varies, so the total work
is identical across a row of the table: same windows, same models, same
arithmetic. Anything the sweep sees is the cost of the chunking alone. It is
not an annotation run and says nothing about end-to-end throughput; the
per-window numbers here are a *floor*, since a real run also reads the genome,
builds the one-hot windows and reconstructs deltas.

Why two widths
--------------
The interesting question is not "which batch size is fastest" but **what the
optimum is a function of**. If the cliff is a working-set effect it should
track ``rows x width`` -- doubling the window width should halve the best row
count -- and the fix is a budget over sequence positions, the shape the ONNX
backend already uses for its own (memory-driven) chunking. If instead the
optimum sits at the same row count at both widths, the fix is a plain row
constant and a budget would be false precision.

So the sweep runs the default window (``distance=50`` -> 10101) and the widest
one a *default* deployment builds (``distance=5000`` + the default
``max_insertion_length`` -> 20201), and the *shape* of the two curves is the
result. One width cannot answer it.

Choose the batch sizes so the two widths cover the **same position counts**
and the comparison becomes direct: ``--batch-sizes 8 16 32`` at 10101 and
``4 8 16`` at 20201 both sweep 80k / 162k / 323k positions. If cost tracks
positions the two curves lie on top of each other; if it tracks rows they are
offset by a factor of two.

Pick ``--rows`` as a whole multiple of every batch size measured. A ragged
final chunk is a second graph shape, so a configuration that divides evenly is
being compared against one that does not, and the difference is an artefact of
the row count rather than a property of the chunk size.

Why interleaved repetitions, and why the *minimum* is the headline
------------------------------------------------------------------
Configurations are measured round-robin within each repetition rather than
one configuration to exhaustion. A box that drifts -- thermal, or another
tenant arriving -- then biases every configuration equally instead of
punishing whichever one happened to run last.

The reported number is the **minimum** across repetitions, not the mean or
the median. Interference is one-sided: another process can only ever make a
pass slower, never faster, so the fastest observation is the one least
contaminated by it and the closest estimate of the uncontended cost. On a
build agent this is not a fine point -- an interleaved sweep taken while a CI
job was running measured the same configuration at 186 ms and 401 ms in
consecutive repetitions, and any averaging estimator reports the load rather
than the chunk size.

``loadavg`` is recorded per sample for the same reason: a row whose fastest
sample was taken under load is not a floor, it is an upper bound, and the
table cannot tell you which without it.

On a host that is *never* quiet -- a shared build agent, where absolute
milliseconds are unusable however many repetitions you take -- read
``relative_cost`` instead. Each configuration is divided by a reference
configuration measured in its own repetition, so a repetition run under load
and one run in a lull contribute the same ratios. See ``summarise``.

``relative_cost`` is comparable only **within a single invocation**: its
denominator is a configuration, so two runs over different batch-size sets
are normalised against different references. Never paste rows from separate
runs into one table -- sweep every point you mean to compare in one run.

One pass per *configuration* is discarded before the timed repetitions begin.
TensorFlow traces a graph per chunk shape, so every batch size pays its own
one-off on first use; warming only the first would leave that cost inside the
first timed repetition of all the others.

What calls the shipped code, and what does not
-----------------------------------------------
``--mode production`` calls the shipped ``spliceai_predict`` directly, so the
headline before/after figure measures the code that ships rather than a
reconstruction of it. Its "before" arm is the pre-#427 body -- the same
ensemble mean with the ``batch_size`` keyword absent.

The sweep modes cannot: they vary a chunk size across a range, and the
shipped function derives exactly one from the budget. They load the committed
models through the backend's own ``spliceai_load_models`` and reproduce the
ensemble mean around an explicit ``batch_size``, which is the whole of
``spliceai_predict`` apart from where that number comes from.

Peak RSS
--------
``--mode rss`` answers a different question -- whether a smaller chunk costs
memory -- and has to fork a fresh process per configuration to do it. Peak
RSS is a high-water mark that never falls, so several configurations measured
in one process report the first one's peak forever after. Each child loads
its own ensemble and reports its own ``ru_maxrss``.

Usage
-----
    python spliceai_annotator/scripts/measure_tf_batch_size.py \\
        --widths 10101 --batch-sizes 4 6 8 16 32 --rows 96 --reps 2
    python spliceai_annotator/scripts/measure_tf_batch_size.py \\
        --mode rss --rows 300 --widths 10101 --batch-sizes 6 16 32
    python spliceai_annotator/scripts/measure_tf_batch_size.py \\
        --mode production --rows 300 --reps 2 --widths 10101

Run it on a **quiet** host: the reported load average is part of the result.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import pathlib
import platform
import resource
import statistics
import time
from collections.abc import Callable
from typing import Any, cast

import numpy as np
from spliceai_annotator.utils import one_hot_encode

#: The annotator's default window (``distance=50``) and the widest one a
#: *default* deployment builds (``distance=5000`` plus the default
#: ``max_insertion_length`` of 200). The absolute ceiling is wider still --
#: 22001, at ``max_insertion_length=2000`` -- but it is not what a default
#: install runs. See ``SpliceAIAnnotator._width`` / ``_batch_width``.
DEFAULT_WIDTHS = (10101, 20201)

#: Candidate chunk sizes. Spans Keras's default (32) and the 8-16 plateau
#: #400 measured, with 2 and 4 below it to find the far side of the optimum
#: and 64 above to confirm the plateau past the cliff.
DEFAULT_BATCH_SIZES = (2, 4, 8, 12, 16, 24, 32, 64)

#: Rows of input per timed pass, held fixed while ``batch_size`` varies.
#: Should be a whole multiple of every chunk size measured (see the module
#: docstring). At ``rows == max(batch_sizes)`` that configuration is a single
#: chunk -- the unchunked baseline, which is a legitimate point to measure;
#: below it the configuration is silently clamped and measured twice, which
#: is not, and the CLI refuses it.
DEFAULT_ROWS = 96


def synthetic_input(rows: int, width: int) -> np.ndarray:
    """A one-hot window batch shaped exactly as the annotator's.

    Built through the shipped ``one_hot_encode`` rather than by filling an
    array directly, so the column convention and the ``int8`` dtype are what
    production feeds ``spliceai_predict`` *by construction* rather than by a
    comment that can go stale -- the same reasoning as
    ``measure_onnx_contention.one_hot_window``. It costs a per-character
    Python loop, which is irrelevant here: the input is built once per width,
    beside a model load that is far slower.

    Random bases rather than a real locus: a convolution's cost depends on
    the shape of its input, not on which nucleotide sits in a cell, and a
    synthetic input keeps the probe free of a GRR. The seed is derived from
    the width so a given width gets the same input on every run.
    """
    rng = np.random.default_rng(seed=width)
    return np.concatenate([
        one_hot_encode("".join(rng.choice(list("ACGT"), size=width)))[None, :]
        for _ in range(rows)
    ], axis=0)


def ensemble_predict(
    models: list, x: np.ndarray, batch_size: int | None,
) -> np.ndarray:
    """``spliceai_predict``'s body, with the chunk size exposed.

    ``batch_size=None`` is the pre-#427 body: it is the parameter's own
    default, so passing it is the same call Keras received when the backend
    named no chunk size at all.
    """
    return np.mean([
        models[m].predict(x, batch_size=batch_size, verbose=0)
        for m in range(5)
    ], axis=0)


def time_pass(
    models: list, x: np.ndarray, batch_size: int,
) -> tuple[float, float]:
    """Milliseconds per window for one ensemble pass, and the load during it.

    The load average is sampled *after* the pass so it reflects the window
    just measured rather than whatever preceded it.
    """
    start = time.perf_counter()
    ensemble_predict(models, x, batch_size)
    elapsed = time.perf_counter() - start
    return elapsed * 1000.0 / len(x), os.getloadavg()[0]


def measure_timing(
    widths: tuple[int, ...],
    batch_sizes: tuple[int, ...],
    rows: int,
    reps: int,
) -> list[dict[str, Any]]:
    """Sweep every (width, batch size) pair, interleaved across ``reps``."""
    from spliceai_annotator.spliceai_backend_tensorflow import (
        spliceai_load_models,
    )

    models = spliceai_load_models()
    samples: dict[tuple[int, int], list[tuple[float, float]]] = {
        (width, batch_size): []
        for width in widths for batch_size in batch_sizes
    }

    for width in widths:
        x = synthetic_input(rows, width)
        # Discard one pass per *configuration*, not one per width: Keras
        # traces a graph per chunk shape, so every batch size pays its own
        # one-off on first use. Warming only the first would leave that cost
        # inside the first timed repetition of all the others.
        for batch_size in batch_sizes:
            time_pass(models, x, batch_size)
        for rep in range(reps):
            # Alternate the direction each repetition. Interleaving cancels
            # drift *between* repetitions, but a monotone trend *inside* one
            # -- turbo decay, a co-tenant ramping up -- maps straight onto
            # batch size when the order never changes, and in a fixed
            # ascending sweep it pushes the largest chunk (the one this
            # measurement indicts) last every time. Reversing on odd
            # repetitions puts each configuration at both ends.
            order = batch_sizes if rep % 2 == 0 else batch_sizes[::-1]
            for batch_size in order:
                samples[width, batch_size].append(
                    time_pass(models, x, batch_size))
            print(
                f"  width {width} rep {rep + 1}/{reps} done", flush=True)

    return summarise(samples, rows, widths, batch_sizes, reps)


def summarise(
    samples: dict[tuple[int, int], list[tuple[float, float]]],
    rows: int,
    widths: tuple[int, ...],
    batch_sizes: tuple[int, ...],
    reps: int,
) -> list[dict[str, Any]]:
    """Absolute floors plus the load-robust per-repetition normalisation.

    ``relative_cost`` divides each configuration's time by a **reference
    configuration measured in the same repetition at the same width**, then
    takes the median of those ratios. Within one repetition the
    configurations run seconds apart, so they see essentially the same
    machine; dividing cancels whatever that machine happened to be doing. It
    is the only number here that survives a busy host, and it is the one the
    constant is chosen from. Absolute ``ms_per_window`` is kept because a
    ratio cannot tell you whether the whole sweep was slow.

    The reference is the **smallest batch size in the sweep** -- fixed in
    advance, not picked from the results. Normalising by the per-repetition
    *fastest* instead, as this did at first, makes whichever configuration
    won 1.00 by construction and pushes genuine ties above it, so a plateau
    reads as a slope and the width of the plateau cannot be told from the
    noise floor. Ratios here are therefore free to fall below 1.00.

    Because the denominator is a configuration rather than a constant,
    ``relative_cost`` is only comparable *within one invocation*: two runs
    over different batch-size sets have different references. Rows from
    separate runs must not be pasted into one table.
    """
    results = []
    reference_batch = min(batch_sizes)
    for width in widths:
        per_rep_floor = [
            samples[width, reference_batch][rep][0]
            for rep in range(reps)
        ]
        for batch_size in batch_sizes:
            values = samples[width, batch_size]
            times = [ms for ms, _ in values]
            ratios = [
                ms / floor
                for (ms, _), floor in zip(values, per_rep_floor, strict=True)
            ]
            results.append({
                "width": width,
                "batch_size": batch_size,
                "rows": rows,
                "positions": batch_size * width,
                # The floor: interference is one-sided, so the fastest pass
                # is the least contaminated. See the module docstring.
                "ms_per_window": round(min(times), 1),
                "median_ms_per_window": round(statistics.median(times), 1),
                "max_ms_per_window": round(max(times), 1),
                "relative_cost": round(statistics.median(ratios), 2),
                "relative_cost_per_rep": [round(r, 2) for r in ratios],
                "samples": [round(ms, 1) for ms in times],
                "loadavg_per_sample": [round(load, 1) for _, load in values],
            })
    return results


def _rss_child(
    width: int, batch_size: int, rows: int, queue: multiprocessing.Queue,
) -> None:
    """Load an ensemble, run one pass, report this process's peak RSS."""
    from spliceai_annotator.spliceai_backend_tensorflow import (
        spliceai_load_models,
    )

    models = spliceai_load_models()
    x = synthetic_input(rows, width)
    start = time.perf_counter()
    ensemble_predict(models, x, batch_size)
    elapsed = time.perf_counter() - start
    queue.put({
        "width": width,
        "batch_size": batch_size,
        "rows": rows,
        # ru_maxrss is kilobytes on Linux.
        "peak_rss_mb": round(
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
        "ms_per_window": round(elapsed * 1000.0 / rows, 1),
    })


def run_in_child(target: Callable[..., None], *args: Any) -> dict[str, Any]:
    """Run one measurement in a fresh process and return what it reported.

    Every configuration that reports peak RSS needs its own process:
    ``ru_maxrss`` is a high-water mark that never falls, so a second
    configuration measured in the same process inherits the first one's peak
    and can only ever look worse than it was.

    ``spawn`` rather than ``fork``: a forked child would inherit this
    process's TensorFlow state, and the point is to measure a runtime that
    started clean.
    """
    context = multiprocessing.get_context("spawn")
    queue: multiprocessing.Queue = context.Queue()
    child = context.Process(target=target, args=(*args, queue))
    child.start()
    result = queue.get()
    child.join()
    return cast(dict[str, Any], result)


def _production_child(
    width: int, rows: int, arm: str, queue: multiprocessing.Queue,
) -> None:
    """One end-to-end pass at the production batch, in a fresh process.

    ``shipped`` calls the real `spliceai_predict`, so what is measured is the
    code that ships rather than a reconstruction of it. ``keras-default`` is
    the body as it stood before #427 -- the same ensemble mean with no chunk
    size named, which is what makes Keras fall back to 32.
    """
    from spliceai_annotator import spliceai_backend_tensorflow as backend

    models = backend.spliceai_load_models()
    x = synthetic_input(rows, width)
    start = time.perf_counter()
    if arm == "shipped":
        backend.spliceai_predict(models, x)
    else:
        ensemble_predict(models, x, None)
    elapsed = time.perf_counter() - start
    queue.put({
        "width": width,
        "rows": rows,
        "arm": arm,
        "batch_size": (
            max(1, backend.TENSORFLOW_POSITION_BUDGET // width)
            if arm == "shipped" else 32),
        "peak_rss_mb": round(
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
        "ms_per_window": round(elapsed * 1000.0 / rows, 1),
        "loadavg": round(os.getloadavg()[0], 1),
    })


def measure_production(
    widths: tuple[int, ...], rows: int, reps: int,
) -> list[dict[str, Any]]:
    """The two arms of #427 at the batch `annotate_vcf` actually hands over.

    Both time and peak RSS come from the same pass, in its own process, so
    the memory number is this configuration's and not a high-water mark left
    behind by whatever ran before it -- and the two arms of a repetition are
    measured minutes apart on the same machine, which is what makes their
    *ratio* meaningful where neither absolute figure is.

    The arm order alternates between repetitions. Run always in the same
    order, whichever arm goes second collects any drift that happened in
    between; with `--reps 2` each arm runs first once and second once, so a
    win that survives both orderings is not an artefact of the ordering.
    """
    results = []
    for width in widths:
        for rep in range(reps):
            arms = ("keras-default", "shipped")
            for arm in (arms if rep % 2 == 0 else arms[::-1]):
                result = run_in_child(_production_child, width, rows, arm)
                result["rep"] = rep
                print(
                    f"  width {width} rep {rep + 1}/{reps} {arm}: "
                    f"{result['ms_per_window']} ms/window, "
                    f"{result['peak_rss_mb']} MB "
                    f"(load {result['loadavg']})", flush=True)
                results.append(result)
    return results


def measure_rss(
    widths: tuple[int, ...],
    batch_sizes: tuple[int, ...],
    rows: int,
) -> list[dict[str, Any]]:
    """Peak RSS per configuration, one fresh process each."""
    results = []
    for width in widths:
        for batch_size in batch_sizes:
            result = run_in_child(_rss_child, width, batch_size, rows)
            print(
                f"  width {width} batch {batch_size}: "
                f"{result['peak_rss_mb']} MB", flush=True)
            results.append(result)
    return results


def cpu_model() -> str:
    """The CPU's model name, or a best effort where /proc is not available."""
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as cpuinfo:
            for line in cpuinfo:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def write_payload(payload: dict[str, Any], out: pathlib.Path | None) -> None:
    if out is None:
        return
    out.write_text(json.dumps(payload, indent=2))
    print(f"\nwrote {out}")


def format_table(results: list[dict[str, Any]], column: str) -> str:
    """Render the sweep as a markdown table, one row per width."""
    widths = sorted({r["width"] for r in results})
    batch_sizes = sorted({r["batch_size"] for r in results})
    by_key = {(r["width"], r["batch_size"]): r[column] for r in results}

    header = "| width | " + " | ".join(str(b) for b in batch_sizes) + " |"
    rule = "|---" * (len(batch_sizes) + 1) + "|"
    lines = [header, rule]
    for width in widths:
        cells = [str(by_key.get((width, b), "--")) for b in batch_sizes]
        lines.append(f"| {width} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--mode", choices=("timing", "rss", "production"), default="timing",
        help="timing: ms/window sweep. rss: peak RSS, one process per "
             "config. production: the shipped path vs the pre-#427 body, at "
             "the batch annotate_vcf hands over.")
    parser.add_argument(
        "--widths", type=int, nargs="+", default=list(DEFAULT_WIDTHS))
    parser.add_argument(
        "--batch-sizes", type=int, nargs="+",
        default=list(DEFAULT_BATCH_SIZES))
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS)
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument(
        "--out", type=pathlib.Path,
        help="write the full result as JSON here")
    args = parser.parse_args()

    if args.mode == "timing" and args.rows < max(args.batch_sizes):
        parser.error(
            f"--rows {args.rows} is below the largest --batch-sizes "
            f"{max(args.batch_sizes)}, which silently clamps that "
            f"configuration to {args.rows} rows and measures it twice")
    ragged = [b for b in args.batch_sizes if args.rows % b] \
        if args.mode == "timing" else []
    if ragged:
        print(
            f"warning: --rows {args.rows} is not a multiple of {ragged}; "
            f"those configurations pay for a second, ragged chunk shape")

    widths = tuple(args.widths)
    batch_sizes = tuple(args.batch_sizes)

    if args.mode == "timing":
        results = measure_timing(widths, batch_sizes, args.rows, args.reps)
        column = "ms_per_window"
    elif args.mode == "production":
        results = measure_production(widths, args.rows, args.reps)
        column = "ms_per_window"
    else:
        results = measure_rss(widths, batch_sizes, args.rows)
        column = "peak_rss_mb"

    payload = {
        "mode": args.mode,
        "rows": args.rows,
        # `rss` is the only mode that does not repeat.
        "reps": args.reps if args.mode != "rss" else 1,
        "host": platform.node(),
        "cpu_count": os.cpu_count(),
        # Which ISA a convolution ran on is part of the result, not trivia --
        # the same reasoning as `spliceai_contention_probe.capture_host`.
        "cpu_model": cpu_model(),
        "loadavg": [round(v, 2) for v in os.getloadavg()],
        "results": results,
    }
    print()
    if args.mode == "production":
        print(f"production ({args.rows} rows), per repetition:")
        print("| width | rep | first arm | ms keras | ms shipped | speedup "
              "| RSS keras | RSS shipped | RSS ratio |")
        print("|---" * 9 + "|")
        for width in widths:
            for rep in range(args.reps):
                pair = [
                    r for r in results
                    if r["width"] == width and r["rep"] == rep
                ]
                keras = next(r for r in pair if r["arm"] == "keras-default")
                shipped = next(r for r in pair if r["arm"] == "shipped")
                speedup = keras["ms_per_window"] / shipped["ms_per_window"]
                rss_ratio = shipped["peak_rss_mb"] / keras["peak_rss_mb"]
                print(
                    f"| {width} | {rep + 1} | {pair[0]['arm']} "
                    f"| {keras['ms_per_window']} "
                    f"| {shipped['ms_per_window']} | {speedup:.2f}x "
                    f"| {keras['peak_rss_mb']} | {shipped['peak_rss_mb']} "
                    f"| {rss_ratio:.2f}x |")
        write_payload(payload, args.out)
        return

    print(f"{column} ({args.mode}, {args.rows} rows):")
    print(format_table(results, column))
    if args.mode == "timing":
        print()
        print(f"relative_cost (1.00 = batch {min(batch_sizes)}, the "
              f"reference configuration; load-robust, and comparable only "
              f"within this run):")
        print(format_table(results, "relative_cost"))

    write_payload(payload, args.out)


if __name__ == "__main__":
    main()
