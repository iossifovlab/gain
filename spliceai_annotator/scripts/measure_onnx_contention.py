#!/usr/bin/env python
"""Does the ONNX backend answer the same thing twice under CPU contention?

Issue #1207. #1206 established that ONNX Runtime 1.27's CPU provider returns
**load-dependent** results when a session's intra-op pool holds more than one
thread: bitwise reproducible on a quiet box, and -- once several processes
oversubscribe the cores -- drifting by up to 3e-4 in a probability, ~5e-5 in a
``DS_*`` score, with the occasional ``DP_*`` argmax flip. That finding came
from one CPU (Intel i5-11600K, AVX-512, 12 vCPU) under a pytest-xdist harness,
and #1206 fixed only the integration job. The product default is still a
4-thread pool.

What it measures -- and what it does not
---------------------------------------
This is the standalone, GRR-free probe of #1207's brief: a ``session.run``
loop across contending processes, counting distinct answers. It is **not** an
annotation run. Whether the drift reaches annotated output -- annotate the
same VCF twice at the CLI's default job count and diff the SpliceAI columns --
is a separate measurement that this script does not perform.

The shipped backend, not a reimplementation: it calls
``spliceai_load_models`` / ``spliceai_predict`` from
``spliceai_annotator.spliceai_backend_onnx``, so the three load-bearing
session settings and the ``SPLICEAI_ONNX_INTRA_OP_THREADS`` knob are exercised
exactly as a real annotation run exercises them.

Each configuration forks ``--workers`` processes. Every worker opens its *own*
ensemble of five sessions -- which is the shape a task-graph worker has, since
each annotation process loads the models for itself -- and runs ``--passes``
ensemble predictions over **one fixed input**. Identical input, so every output
must be identical; any distinct output is drift, not arithmetic.

Why a start barrier
-------------------
The workers rendezvous on a barrier before their first timed pass. Without it
the processes stagger -- model loading alone takes far longer than a pass --
the box is never actually oversubscribed, and the run reports "no drift" for
the boring reason, which is the failure mode this whole measurement exists to
avoid. ``max conc`` confirms every worker really was inside the timed window;
because the barrier guarantees that, it is a well-formedness check, not
evidence of contention. The ``oversub`` column
(``threads x workers / cpu_count``) is what says whether the cores were
fought over at all.

Why the CLI's shape is the interesting one
------------------------------------------
gain's annotation CLI takes ``-j``/``--jobs`` defaulting to the machine's
processor count, and each worker process loads its own ensemble. At the shipped
4 intra-op threads that is roughly 4x core oversubscription by default --
*more* contention than the ~1.7x active (5 workers x 4 threads on 12 vCPU) that
produced the drift in CI.

Usage
-----
    python spliceai_annotator/scripts/measure_onnx_contention.py \\
        --workers 32 --passes 20 --threads 4 2 1 --deterministic both

Run it on a **quiet** host: the reported load average is part of the result,
and a box busy with other work measures that work as much as this one.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import multiprocessing as mp
import operator
import os
import pathlib
import queue
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from multiprocessing import synchronize
from typing import Any, NamedTuple

import numpy as np


@dataclass(frozen=True)
class DriftSummary:
    """How many different answers one fixed input produced, and how different.

    ``distinct == 1`` is the reproducible case. Anything above 1 means the
    same input returned more than one answer within the run.
    """

    passes: int
    distinct: int
    max_abs_deviation: float
    #: Digest of the first output, so two runs can be compared with each
    #: other. Within-run reproducibility is not the whole question: a thread
    #: count that never disagrees with itself may still answer differently
    #: from the shipped one, and changing the default would then move
    #: published scores rather than merely stabilise them.
    reference_digest: str


def digest_of(output: np.ndarray) -> str:
    """Exact identity of one answer.

    Exact -- a digest of the raw bytes -- because the question is
    reproducibility, not closeness: two answers that differ in the last
    mantissa bit are two answers.
    """
    return hashlib.sha256(output.tobytes()).hexdigest()


def distinct_answers(outputs: Iterable[np.ndarray]) -> dict[str, np.ndarray]:
    """Digest -> one representative array, in first-seen order.

    This is what a worker sends back instead of every array it produced. The
    two statistics that matter survive it exactly: duplicates cannot change a
    distinct *count*, and deviation is a function of the value, so a repeat of
    a value already seen cannot widen it. What it removes is the pile -- the
    parent would otherwise hold ``workers x passes`` arrays at once, which at
    ``--rows 32 --width 20201`` is about 10 GB for a population whose answer
    is one or two distinct values.
    """
    representatives: dict[str, np.ndarray] = {}
    for output in outputs:
        representatives.setdefault(digest_of(output), output)
    return representatives


def summarize_drift(outputs: Sequence[np.ndarray]) -> DriftSummary:
    """Count distinct answers and the widest deviation from the first one.

    The deviation is reported alongside the count to say *how much* the drift
    is worth caring about, which is what decides whether it can reach a
    published score.
    """
    if not outputs:
        raise ValueError(
            "no outputs to compare: an empty population must not be "
            "summarised, because every field of the result would read as "
            "agreement")
    return summarize_samples(distinct_answers(outputs), passes=len(outputs))


def summarize_samples(
    representatives: dict[str, np.ndarray],
    passes: int,
) -> DriftSummary:
    """Summarise a population already reduced to its distinct answers.

    Takes the deviation over the distinct values only -- every duplicate has
    the same value and so the same deviation, by definition.
    """
    if not representatives:
        raise ValueError("no answers to compare")
    reference = next(iter(representatives.values()))
    return DriftSummary(
        passes=passes,
        distinct=len(representatives),
        max_abs_deviation=max(
            float(np.max(np.abs(output - reference)))
            for output in representatives.values()),
        reference_digest=next(iter(representatives)),
    )


@dataclass(frozen=True)
class OverlapSummary:
    """How many workers were actually running at once.

    ``max_concurrent == 1`` means the workers took turns: the box was never
    oversubscribed and a "no drift" result from that run says nothing about
    contention. This is reported next to every result so such a run cannot be
    mistaken for a clean one.
    """

    max_concurrent: int


def summarize_overlap(
    spans: Sequence[tuple[float, float]],
) -> OverlapSummary:
    """Peak number of simultaneously-running workers, from their spans.

    Spans are half-open ``[start, end)``: a worker that stops at the instant
    another starts did not run *with* it, so ends are settled before starts at
    the same timestamp. Getting that backwards would report a serialized run
    as contended -- the exact false positive that would let a meaningless
    measurement pass for a real one.
    """
    events: list[tuple[float, int]] = []
    for start, end in spans:
        events.extend(((start, 1), (end, -1)))
    # Plain tuple order: at an equal timestamp -1 sorts before +1, so a span
    # ending where another begins does not count as concurrent.
    events.sort()

    concurrent = 0
    max_concurrent = 0
    for _, delta in events:
        concurrent += delta
        max_concurrent = max(max_concurrent, concurrent)

    return OverlapSummary(max_concurrent=max_concurrent)


#: A payload factory runs *inside* the worker process and returns the callable
#: that performs one timed pass. The factory is where a worker opens its own
#: ONNX sessions -- setup happens before the barrier and is never timed, which
#: is what makes the measurement about inference rather than model loading.
PayloadFactory = Callable[[], Callable[[], np.ndarray]]


class WorkerRun(NamedTuple):
    """What one worker sends back: when it ran, and what answers it saw.

    Answers arrive already reduced to their distinct values -- see
    `distinct_answers` for why the reduction happens in the child.
    """

    start: float
    end: float
    passes: int
    answers: dict[str, np.ndarray]


@dataclass(frozen=True)
class ProbeResult:
    """One configuration's answer: did it drift, did it contend, how fast."""

    workers: int
    drift: DriftSummary
    overlap: OverlapSummary
    #: The contended window: from the first worker leaving the barrier to the
    #: last one finishing. Throughput is measured over this, never over the
    #: wall clock -- see `run_probe`.
    timed_seconds: float
    #: Everything the run took, model loading and process startup included.
    wall_seconds: float
    passes_per_second: float
    #: Sampled *before* the workers start, so it describes the host this ran
    #: on rather than the load this run itself created.
    load_before: float


#: How long to wait for a worker before declaring the run broken. Generous:
#: opening five ONNX sessions per worker is slow when many do it at once.
WORKER_TIMEOUT_SECONDS = 900.0


def _worker(
    factory: PayloadFactory,
    passes: int,
    barrier: synchronize.Barrier,
    results: mp.Queue,
) -> None:
    """Set up, rendezvous, then run ``passes`` timed passes."""
    run_once = factory()
    # Everything expensive is done; from here the workers move together.
    barrier.wait()
    start = time.perf_counter()
    outputs = [run_once() for _ in range(passes)]
    end = time.perf_counter()

    answers = distinct_answers(outputs)
    del outputs, run_once  # release this worker's ensemble before the parent
    results.put(WorkerRun(start, end, passes, answers))


def run_probe(
    factory: PayloadFactory,
    workers: int,
    passes: int,
    timeout: float = WORKER_TIMEOUT_SECONDS,
) -> ProbeResult:
    """Run ``workers`` processes of ``passes`` passes each, all at once.

    Every worker builds its own payload and waits on a barrier, so the timed
    window is one in which all of them are running. The outputs of every pass
    of every worker are pooled and compared as a single population: drift
    between two workers matters exactly as much as drift within one, since a
    user re-annotating a file gets a fresh process either way.

    Throughput is measured over the *contended window* -- first worker out of
    the barrier to last worker done -- and not over the wall clock. Opening
    five ONNX sessions costs far more than a pass and costs a different amount
    at each thread count, so charging it to throughput would rank the
    configurations by how fast they load. Both numbers are reported.

    Forks deliberately, so a payload factory need not be picklable and can
    close over whatever it builds. Note what that does *not* give you: the
    children inherit whatever the parent has imported, and this engine does
    not check that. Keeping the parent free of ONNX Runtime is the caller's
    job -- `main` does it by calling
    `refuse_to_measure_with_onnxruntime_loaded` -- because this package's own
    test suite legitimately has the backend imported and still needs to
    exercise the engine.
    """
    context = mp.get_context("fork")
    barrier = context.Barrier(workers, timeout=timeout)
    results: mp.Queue = context.Queue()
    processes = [
        context.Process(
            target=_worker, args=(factory, passes, barrier, results))
        for _ in range(workers)
    ]

    load_before = os.getloadavg()[0]
    wall_start = time.perf_counter()
    for process in processes:
        process.start()
    try:
        # Drain before joining: a child blocks on a full pipe until its
        # payload is consumed, so joining first would deadlock on larger
        # outputs.
        collected = [results.get(timeout=timeout) for _ in processes]
    except queue.Empty:
        for process in processes:
            process.terminate()
        raise RuntimeError(
            f"a worker produced no result within {timeout:.0f}s; the run is "
            f"incomplete and its numbers would be wrong. Exit codes: "
            f"{[process.exitcode for process in processes]}") from None
    for process in processes:
        process.join(timeout=timeout)
    wall_seconds = time.perf_counter() - wall_start

    failed = [process.exitcode for process in processes
              if process.exitcode not in (0, None)]
    if failed:
        raise RuntimeError(
            f"{len(failed)} of {workers} workers exited non-zero ({failed}); "
            f"the population is not what was asked for")

    # Ordered by when each worker started, so the reference answer -- and the
    # digest naming it -- does not depend on which worker won the race to the
    # queue.
    collected.sort(key=operator.attrgetter("start"))
    spans = [(run.start, run.end) for run in collected]
    answers: dict[str, np.ndarray] = {}
    for run in collected:
        for digest, output in run.answers.items():
            answers.setdefault(digest, output)
    total_passes = sum(run.passes for run in collected)
    timed_seconds = (
        max(end for _, end in spans) - min(start for start, _ in spans))

    return ProbeResult(
        workers=workers,
        drift=summarize_samples(answers, passes=total_passes),
        overlap=summarize_overlap(spans),
        timed_seconds=timed_seconds,
        wall_seconds=wall_seconds,
        passes_per_second=total_passes / timed_seconds,
        load_before=load_before,
    )


def refuse_to_measure_with_onnxruntime_loaded() -> None:
    """Refuse to fork a parent that already holds ONNX Runtime.

    Every child would start with an inherited copy of the parent's runtime
    state and thread pools rather than opening its own ensemble -- so the run
    would no longer measure the shape being measured, and Python warns that
    forking a multi-threaded process can deadlock outright.

    Checked here, at the measurement entry point, rather than inside
    `run_probe`: this package's own test suite legitimately imports the
    backend at collection time, and a guard buried in the engine would fail
    those tests instead of protecting a measurement.
    """
    if "onnxruntime" in sys.modules:
        raise RuntimeError(
            "onnxruntime is already imported in this process: forking now "
            "would hand every worker an inherited copy of its runtime state "
            "and thread pools instead of the fresh ensemble this measures. "
            "Run this script directly, so the backend is imported only "
            "inside the payload factory, which runs in the child.")


#: Window width for ``distance=50``, the annotator's default: 10000 + 2*50 + 1.
DEFAULT_WIDTH = 10101


def one_hot_window(width: int, rows: int, seed: int) -> np.ndarray:
    """A fixed ``(rows, width, 4)`` one-hot batch, built by the shipped encoder.

    Goes through the annotator's own ``one_hot_encode`` rather than
    reconstructing the encoding here, so the base-to-column convention and the
    int8 dtype are what production feeds ``spliceai_predict`` by construction
    rather than by a comment that can go stale. A measurement of "does the
    answer reproduce" would stay green on a wrong encoding, so nothing else
    would catch the drift.

    Seeded, so every worker and every configuration scores the *same*
    sequence and any difference in the answer is the runtime's.

    ``rows`` matters beyond timing: the drift under test comes from the order
    a threaded reduction accumulates in, and the batch axis changes both the
    shapes ONNX Runtime sees and how the shipped chunking splits them.

    Called in the worker, before the barrier, so its cost is never timed.
    """
    # pylint: disable=import-outside-toplevel
    from spliceai_annotator import utils

    rng = np.random.default_rng(seed)
    return np.stack([
        utils.one_hot_encode(
            "".join(rng.choice(("A", "C", "G", "T"), size=width)))
        for _ in range(rows)
    ])


def assert_sessions_configured(
    models: Sequence[Any],
    *,
    threads: int,
    deterministic: bool,
) -> None:
    """Check the opened sessions really carry the arm being claimed.

    The deterministic arm reaches its flag by wrapping the backend's session
    options factory, which works only because the shipped loader resolves that
    factory as a module global at call time. Inline that one expression -- an
    innocuous-looking refactor -- and the arm silently becomes a copy of the
    default one, reporting the same numbers under a different label. That is
    the reassuring direction, and nothing else in the run would notice, so the
    session is asked what it is actually set to.
    """
    for model in models:
        options = model.get_session_options()
        if options.intra_op_num_threads != threads:
            raise RuntimeError(
                f"session opened with intra_op_num_threads="
                f"{options.intra_op_num_threads}, expected {threads}")
        if options.use_deterministic_compute != deterministic:
            raise RuntimeError(
                f"session opened with use_deterministic_compute="
                f"{options.use_deterministic_compute}, expected "
                f"{deterministic}; the arm would measure the wrong thing")


@dataclass(frozen=True)
class OnnxEnsemblePayload:
    """Opens its own five-session ensemble, then scores one fixed window.

    Everything here happens in the worker: the import of the backend, the
    sessions, the window. That is deliberate -- it mirrors a task-graph worker,
    where the models load at module import in each process, and it keeps the
    parent free of ONNX Runtime so `run_probe`'s fork stays honest.
    """

    threads: int
    deterministic: bool
    width: int = DEFAULT_WIDTH
    rows: int = 1
    seed: int = 1207

    def __call__(self) -> Callable[[], np.ndarray]:
        # pylint: disable=import-outside-toplevel
        # Deferred on purpose: importing this at module scope would load ONNX
        # Runtime into the *parent*, which `run_probe` refuses to fork.
        # No `type: ignore` here: CI runs mypy from the project directory,
        # where the annotator resolves as source (#1327). Checked from
        # anywhere else it resolves to the installed distribution, which
        # ships no py.typed marker -- run it the way CI does.
        import spliceai_annotator.spliceai_backend_onnx as backend

        # Read by `spliceai_session_options` when it opens each session, so
        # setting it here -- before any session exists -- is what the shipped
        # `SPLICEAI_ONNX_INTRA_OP_THREADS` knob does for a real process.
        os.environ[backend.ONNX_INTRA_OP_THREADS_ENV] = str(self.threads)

        if self.deterministic:
            self._request_deterministic_compute(backend)
        models = backend.spliceai_load_models()
        assert_sessions_configured(
            models, threads=self.threads, deterministic=self.deterministic)
        window = one_hot_window(self.width, self.rows, self.seed)

        def run_once() -> np.ndarray:
            return backend.spliceai_predict(models, window)

        return run_once

    @staticmethod
    def _request_deterministic_compute(backend: Any) -> None:
        """Add ``use_deterministic_compute`` to the shipped session options.

        The shipped backend has no switch for this flag -- whether it should
        gain one is part of what #1207 decides -- so this arm reaches it by
        wrapping the backend's own options factory in the worker process,
        rather than by copying the loader. Copying it would let the arms
        drift apart silently: a warmup pass or a provider option added to
        the shipped loader would then reach only two of the three arms.
        """
        shipped_options = backend.spliceai_session_options

        def deterministic_options() -> object:
            options = shipped_options()
            options.use_deterministic_compute = True
            return options

        backend.spliceai_session_options = deterministic_options


def format_row(payload: OnnxEnsemblePayload, result: ProbeResult) -> str:
    """One markdown table row, ready to paste into the issue.

    Reads the arm's labels off the payload that actually ran, rather than
    taking them again as arguments: a row cannot then be labelled with a
    configuration other than the one measured.

    ``oversub`` is what says whether the cores were actually fought over:
    ``max conc`` only reports that every worker was inside the timed window,
    which the barrier guarantees, so it can confirm the run was well-formed
    but never that it was contended. A digest is shown only when the run
    agreed with itself -- with more than one answer there is no single one to
    name.
    """
    oversubscription = payload.threads * result.workers / processor_count()
    answer = (
        result.drift.reference_digest[:12] if result.drift.distinct == 1
        else f"({result.drift.distinct} answers)")
    return (
        f"| {payload.threads} | {'yes' if payload.deterministic else 'no'} | "
        f"{result.workers} | {oversubscription:.1f}x | "
        f"{result.drift.passes} | "
        f"{result.overlap.max_concurrent} | "
        f"{result.drift.distinct} | "
        f"{result.drift.max_abs_deviation:.3e} | "
        f"{result.passes_per_second:.2f} | "
        f"{result.timed_seconds:.1f} | {result.wall_seconds:.1f} | "
        f"{result.load_before:.1f} | {answer} |"
    )


TABLE_HEADER = (
    "| threads | determ | workers | oversub | passes | max conc | distinct | "
    "max dev | passes/s | timed s | wall s | load before | answer |\n"
    "|---|---|---|---|---|---|---|---|---|---|---|---|---|"
)


def processor_count() -> int:
    """``os.cpu_count()``, which is documented as possibly ``None``."""
    return os.cpu_count() or 1


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Command line: which configurations to sweep, and at what scale."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--workers", type=int, nargs="+", default=[processor_count()],
        help="worker process counts to sweep; defaults to the processor "
             "count, which is what the annotation CLI's -j defaults to. "
             "Sweep it to find where distinct answers first appear")
    parser.add_argument(
        "--passes", type=int, default=10,
        help="ensemble predictions per worker")
    parser.add_argument(
        "--threads", type=int, nargs="+", default=[4, 1],
        help="intra-op thread counts to sweep (4 is the shipped default)")
    parser.add_argument(
        "--deterministic", choices=["off", "on", "both"], default="off",
        help="also measure with use_deterministic_compute set")
    parser.add_argument(
        "--width", type=int, default=DEFAULT_WIDTH,
        help=f"window width; {DEFAULT_WIDTH} is distance=50, the default")
    parser.add_argument(
        "--rows", type=int, default=1,
        help="windows per prediction call. The drift under test is a "
             "reduction-order effect, and the batch axis changes it; the "
             "shipped path chunks a batch to ONNX_POSITION_BUDGET // width "
             "rows per session.run")
    parser.add_argument("--seed", type=int, default=1207)
    return parser.parse_args(argv)


def describe_host() -> str:
    """Host, CPU model and whether it has AVX-512.

    #1206 left open whether the drift needs heavy oversubscription or
    AVX-512 kernels specifically, so a result that does not name the ISA
    cannot be compared against the box the drift was found on.
    """
    cpuinfo = pathlib.Path("/proc/cpuinfo").read_text(encoding="utf-8")
    model = next(
        (line.split(":", 1)[1].strip() for line in cpuinfo.splitlines()
         if line.startswith("model name")),
        "unknown")
    # The token only ever appears in a flags line, so the whole-file test is
    # equivalent to parsing one out.
    avx512 = "avx512f" in cpuinfo
    return (f"{os.uname().nodename}  {model}  cpus: {processor_count()}  "
            f"avx512: {'yes' if avx512 else 'no'}")


def main(argv: Sequence[str] | None = None) -> None:
    """Sweep the requested configurations and print a pasteable table."""
    refuse_to_measure_with_onnxruntime_loaded()
    args = parse_args(argv)
    determinism_arms = {
        "off": [False], "on": [True], "both": [False, True],
    }[args.deterministic]

    print(f"# host: {describe_host()}")
    print(f"# width: {args.width}  rows: {args.rows}  seed: {args.seed}  "
          f"passes/worker: {args.passes}")
    print(TABLE_HEADER)

    arms = itertools.product(args.workers, args.threads, determinism_arms)
    for workers, threads, deterministic in arms:
        payload = OnnxEnsemblePayload(
            threads=threads, deterministic=deterministic,
            width=args.width, rows=args.rows, seed=args.seed)
        result = run_probe(payload, workers=workers, passes=args.passes)
        print(format_row(payload, result), flush=True)


if __name__ == "__main__":
    main()
