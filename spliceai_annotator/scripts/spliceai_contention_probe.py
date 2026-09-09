#!/usr/bin/env python
"""Standalone contention probe for the SpliceAI model runtimes (#1275).

Answers one question: does a backend return **load-dependent** results? K
processes each load one committed model, predict the *same* fixed input N
times, and digest every output. If the runtime is reproducible the whole run
yields exactly one distinct digest, whatever K is. More than one means the
answer depends on how contended the cores are.

Needs no GRR and no annotation pipeline -- one model file and one synthetic
input, so it can run on any host.
"""
import argparse
import dataclasses
import hashlib
import json
import multiprocessing
import os
import pathlib
import platform
from collections.abc import Callable, Sequence
from typing import Any, cast

import numpy as np

#: One worker's view of the runtime: takes the fixed input, returns the
#: ensemble output. Deliberately narrow, so the probe's core can be exercised
#: without TensorFlow or ONNX Runtime present.
Predict = Callable[[np.ndarray], np.ndarray]


def digest_output(array: np.ndarray) -> str:
    """Content digest of a prediction, sensitive to a single ULP.

    Shape and dtype are part of the identity, not just the raw buffer: two
    differently-shaped views over the same bytes are different answers, and a
    probe that conflated them would under-report divergence.
    """
    digest = hashlib.sha256()
    digest.update(f"{array.dtype.str}:{array.shape}:".encode())
    digest.update(np.ascontiguousarray(array).tobytes())
    return digest.hexdigest()


def probe_predictions(
    predict: Predict,
    x: np.ndarray,
    iterations: int,
) -> tuple[str, ...]:
    """Digest of every prediction one worker makes on the same input.

    The input never changes, so every digest here should be identical. Any
    variation is the runtime answering differently for reasons that are not
    the data.
    """
    return tuple(digest_output(predict(x)) for _ in range(iterations))


@dataclasses.dataclass(frozen=True)
class RunSummary:
    """What one (K, thread-setting) point of the sweep found."""

    processes: int
    threads: int | None
    total_predictions: int
    distinct_outputs: int
    #: How many distinct answers each worker gave on its own. All 1s with
    #: `distinct_outputs > 1` means every process was internally consistent
    #: but processes disagreed; anything above 1 is a single process changing
    #: its answer between iterations.
    per_worker_distinct: tuple[int, ...]
    #: What each worker's runtime reported its intra-op pool actually was,
    #: and how many OS threads the process ended up holding. `threads` above
    #: is what was *asked* for; these are what happened. A `--threads 1` run
    #: that silently kept the default would look reproducible for the wrong
    #: reason, so the evidence travels with the result.
    observed_intra_op_threads: tuple[int, ...] = ()
    observed_os_threads: tuple[int, ...] = ()
    #: The box's 1/5/15-minute load as this point started. #1275 asks for it
    #: beside every figure, because a "reproducible" point taken on an idle
    #: box says nothing about a contended one.
    load_average: tuple[float, float, float] | None = None

    @property
    def reproducible(self) -> bool:
        return self.distinct_outputs <= 1


def summarise_run(
    processes: int,
    threads: int | None,
    worker_digests: Sequence[Sequence[str]],
    observed_intra_op_threads: Sequence[int] = (),
    observed_os_threads: Sequence[int] = (),
    load_average: tuple[float, float, float] | None = None,
) -> RunSummary:
    """Fold every worker's digests into one point of the sweep."""
    every = [digest for worker in worker_digests for digest in worker]
    return RunSummary(
        processes=processes,
        threads=threads,
        total_predictions=len(every),
        distinct_outputs=len(set(every)),
        per_worker_distinct=tuple(
            len(set(worker)) for worker in worker_digests),
        observed_intra_op_threads=tuple(observed_intra_op_threads),
        observed_os_threads=tuple(observed_os_threads),
        load_average=load_average,
    )


@dataclasses.dataclass(frozen=True)
class SweepSummary:
    """A whole sweep: every point, and the answer #1275 asks for."""

    runs: tuple[RunSummary, ...]
    #: Per intra/inter-op thread setting (None = the runtime's own default),
    #: the smallest process count at which more than one distinct answer
    #: appeared -- or None if the setting stayed reproducible throughout.
    first_divergent_processes: dict[int | None, int | None]


def summarise_sweep(runs: Sequence[RunSummary]) -> SweepSummary:
    """Fold the sweep into the K at which each thread setting first drifts."""
    first: dict[int | None, int | None] = {}
    for run in sorted(runs, key=lambda r: r.processes):
        if run.threads not in first:
            first[run.threads] = None
        if not run.reproducible and first[run.threads] is None:
            first[run.threads] = run.processes
    return SweepSummary(runs=tuple(runs), first_divergent_processes=first)


#: The annotator's own window at the default `distance=50`: 10000 + 2*50 + 1.
#: Chosen so the probe exercises the same shape production does.
DEFAULT_WIDTH = 10101


def fixed_input(width: int = DEFAULT_WIDTH, seed: int = 0) -> np.ndarray:
    """The one input every worker predicts on, one-hot A/C/G/T.

    Seeded rather than constant: an all-A window is a degenerate input whose
    convolutions could plausibly reduce more stably than real sequence. Every
    worker derives it from the same seed, so a divergence is the runtime's,
    never the data's.
    """
    bases = np.random.default_rng(seed).integers(0, 4, size=width)
    x = np.zeros((1, width, 4), dtype=np.float32)
    x[0, np.arange(width), bases] = 1.0
    return x


@dataclasses.dataclass(frozen=True)
class HostInfo:
    """Where a sweep ran -- #1275 asks for all of this beside every figure."""

    hostname: str
    cpu_model: str
    cores: int
    #: #1206 left "oversubscription degree alone, or AVX-512 kernels?" open,
    #: so which ISA a result came from is part of the result.
    avx512: bool
    load_average: tuple[float, float, float]


def read_host_info(
    cpuinfo: str,
    hostname: str,
    cores: int,
    load_average: tuple[float, float, float],
) -> HostInfo:
    """Build the host record from an already-read /proc/cpuinfo."""
    model = ""
    flags: set[str] = set()
    for line in cpuinfo.splitlines():
        key, _, value = line.partition(":")
        key = key.strip()
        if key == "model name" and not model:
            model = value.strip()
        elif key == "flags" and not flags:
            flags = set(value.split())
    return HostInfo(
        hostname=hostname,
        cpu_model=model,
        cores=cores,
        avx512=any(flag.startswith("avx512") for flag in flags),
        load_average=load_average,
    )


def capture_host() -> HostInfo:
    """Read the running host's record from the real sources."""
    try:
        cpuinfo = pathlib.Path("/proc/cpuinfo").read_text()
    except OSError:
        cpuinfo = ""
    load = os.getloadavg()
    return read_host_info(
        cpuinfo=cpuinfo,
        hostname=platform.node(),
        cores=os.cpu_count() or 0,
        load_average=(load[0], load[1], load[2]),
    )


def default_process_sweep(cores: int) -> tuple[int, ...]:
    """Doubling process counts up to, and including, the core count.

    The core count is the point of the sweep: the annotation CLI's ``-j``
    defaults to it, so that is the contention degree the shipped default
    actually produces. The integration job's five xdist workers sit well
    below it.
    """
    counts = []
    k = 1
    while k < cores:
        counts.append(k)
        k *= 2
    counts.append(cores)
    return tuple(counts)


#: The committed models live beside the package, not beside this script.
MODELS_DIR = (
    pathlib.Path(__file__).resolve().parent.parent
    / "spliceai_annotator" / "models"
)

#: Mirrors `spliceai_backend_onnx.DEFAULT_ONNX_INTRA_OP_THREADS`. Re-stated
#: rather than imported, the way `tests/test_onnx_equivalence.py` re-states
#: its constants: importing the annotator package pulls in `gain.annotation`,
#: and this probe must stay runnable from a bare checkout. A test pins the
#: two together so the copy cannot drift.
ONNX_INTRA_OP_THREADS = 4


@dataclasses.dataclass(frozen=True)
class ProbeConfig:
    """One sweep's worth of instructions."""

    backend: str
    model: int
    width: int
    iterations: int
    processes: tuple[int, ...]
    #: None means "leave the runtime's own default alone" -- the arm that
    #: reproduces what the shipped backend does today.
    threads: tuple[int | None, ...]
    seed: int
    json_path: pathlib.Path | None


def _positive(raw: str) -> int:
    value = int(raw)
    if value < 1:
        raise argparse.ArgumentTypeError(
            f"expected a positive integer: {raw!r}")
    return value


def _positive_list(raw: str) -> tuple[int, ...]:
    return tuple(_positive(part) for part in raw.split(","))


def _thread_list(raw: str) -> tuple[int | None, ...]:
    return tuple(
        None if part.strip() == "auto" else _positive(part)
        for part in raw.split(","))


def parse_args(argv: Sequence[str] | None = None) -> ProbeConfig:
    """Read a sweep off the command line."""
    cores = os.cpu_count() or 1
    sweep_default = default_process_sweep(cores)
    parser = argparse.ArgumentParser(
        description="Does a SpliceAI model runtime answer differently under "
                    "load? (iossifovlab/gain#1275)")
    parser.add_argument(
        "--backend", choices=sorted(("tensorflow", "onnx")),
        default="tensorflow",
        help="model runtime to probe (default: the shipped one)")
    parser.add_argument(
        "--model", type=_positive, default=1,
        help="which of the five ensemble models to load (default: 1)")
    parser.add_argument(
        "--width", type=_positive, default=DEFAULT_WIDTH,
        help=f"sequence length (default: {DEFAULT_WIDTH}, distance=50)")
    parser.add_argument(
        "--iterations", type=_positive, default=3,
        help="predictions per worker after the barrier (default: 3)")
    parser.add_argument(
        "--processes", type=_positive_list,
        default=sweep_default,
        help="comma-separated contending process counts "
             f"(default: {','.join(str(k) for k in sweep_default)})")
    parser.add_argument(
        "--threads", type=_thread_list, default=(None, 1),
        help="comma-separated intra/inter-op settings; 'auto' leaves the "
             "runtime's own default (default: auto,1)")
    parser.add_argument(
        "--seed", type=int, default=0,
        help="seed for the fixed input (default: 0)")
    parser.add_argument(
        "--json", dest="json_path", type=pathlib.Path, default=None,
        help="write the full result here as JSON")
    args = parser.parse_args(argv)
    return ProbeConfig(
        backend=args.backend,
        model=args.model,
        width=args.width,
        iterations=args.iterations,
        processes=tuple(args.processes),
        threads=tuple(args.threads),
        seed=args.seed,
        json_path=args.json_path,
    )


def _os_thread_count() -> int:
    try:
        return len(os.listdir("/proc/self/task"))
    except OSError:
        return 0


def _load_tensorflow(model: int, threads: int | None) -> tuple[Predict, int]:
    """Load one Keras model, optionally pinning the thread pools first.

    Both settings must be applied before TensorFlow initialises its runtime,
    which is why this runs inside the worker process and why the parent never
    imports TensorFlow.
    """
    # pylint: disable=import-outside-toplevel
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
    import tensorflow as tf
    if threads is not None:
        tf.config.threading.set_intra_op_parallelism_threads(threads)
        tf.config.threading.set_inter_op_parallelism_threads(threads)
    keras_model = tf.keras.models.load_model(
        str(MODELS_DIR / f"spliceai{model}.h5"))
    # 0 is TensorFlow's way of saying "size it to the machine".
    observed = tf.config.threading.get_intra_op_parallelism_threads()

    def predict(x: np.ndarray) -> np.ndarray:
        return cast("np.ndarray", keras_model.predict(x, verbose=0))

    return predict, observed


def _load_onnx(model: int, threads: int | None) -> tuple[Predict, int]:
    """Open one ONNX session with the shipped backend's settings applied.

    #1275 asks for the ONNX arm as a comparison baseline, so it must be the
    configuration that actually ships -- the three settings #297 and #400
    found load-bearing -- not stock ONNX Runtime.
    """
    # pylint: disable=import-outside-toplevel
    import onnxruntime as ort
    options = ort.SessionOptions()
    options.add_session_config_entry("session.intra_op.allow_spinning", "0")
    options.graph_optimization_level = \
        ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED
    options.intra_op_num_threads = (
        ONNX_INTRA_OP_THREADS if threads is None else threads)
    session = ort.InferenceSession(
        str(MODELS_DIR / f"spliceai{model}.onnx"), options,
        providers=["CPUExecutionProvider"])
    name = session.get_inputs()[0].name

    def predict(x: np.ndarray) -> np.ndarray:
        return cast("np.ndarray", session.run(None, {name: x})[0])

    return predict, options.intra_op_num_threads


_LOADERS = {"tensorflow": _load_tensorflow, "onnx": _load_onnx}

#: Set in each worker by the pool initializer; the workers meet here so that
#: every measured prediction happens while all K processes are running.
_BARRIER: Any = None

#: Generous: a worker that has to load a model on a saturated box can take
#: a while, and a hang should fail rather than wedge the sweep.
_BARRIER_TIMEOUT_SECONDS = 900


def _init_worker(barrier: Any) -> None:
    global _BARRIER  # pylint: disable=global-statement
    _BARRIER = barrier


def _run_worker(
    spec: tuple[str, int, int | None, int, int, int],
) -> tuple[tuple[str, ...], int, int]:
    """One contending process: load, warm up, sync, then measure."""
    backend, model, threads, width, seed, iterations = spec
    predict, observed = _LOADERS[backend](model, threads)
    x = fixed_input(width, seed)
    # Untimed: the first call builds the graph and allocates the arenas, and
    # doing that while other workers are already predicting would measure
    # start-up skew rather than contention.
    predict(x)
    if _BARRIER is not None:
        _BARRIER.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
    return probe_predictions(predict, x, iterations), observed, \
        _os_thread_count()


def run_point(
    config: ProbeConfig,
    processes: int,
    threads: int | None,
) -> RunSummary:
    """Run one (process count, thread setting) point of the sweep."""
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(processes)
    spec = (
        config.backend, config.model, threads,
        config.width, config.seed, config.iterations)
    load = os.getloadavg()
    with ctx.Pool(
        processes, initializer=_init_worker, initargs=(barrier,),
    ) as pool:
        results = pool.map(_run_worker, [spec] * processes, chunksize=1)
    return summarise_run(
        processes=processes,
        threads=threads,
        worker_digests=[digests for digests, _, _ in results],
        observed_intra_op_threads=[observed for _, observed, _ in results],
        observed_os_threads=[os_threads for _, _, os_threads in results],
        load_average=(load[0], load[1], load[2]),
    )


def run_sweep(config: ProbeConfig) -> SweepSummary:
    """Every thread setting crossed with every process count."""
    runs = []
    for threads in config.threads:
        for processes in config.processes:
            run = run_point(config, processes, threads)
            print(
                f"  {config.backend:<10} threads={_label(threads):<4} "
                f"K={run.processes:<3} "
                f"distinct={run.distinct_outputs:<3} "
                "observed_intra_op="
                f"{sorted(set(run.observed_intra_op_threads))} "
                f"os_threads={sorted(set(run.observed_os_threads))}",
                flush=True)
            runs.append(run)
    return summarise_sweep(runs)


def _label(threads: int | None) -> str:
    return "auto" if threads is None else str(threads)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the sweep and report it."""
    config = parse_args(argv)
    host = capture_host()
    print(
        f"host={host.hostname} cpu={host.cpu_model!r} cores={host.cores} "
        f"avx512={host.avx512} load={host.load_average}")
    print(
        f"backend={config.backend} model={config.model} "
        f"width={config.width} iterations={config.iterations} "
        f"processes={config.processes} "
        f"threads={[_label(t) for t in config.threads]}")
    sweep = run_sweep(config)

    print("\nfirst divergent process count, per thread setting:")
    for threads, first in sweep.first_divergent_processes.items():
        verdict = "none up to " + str(max(config.processes)) \
            if first is None else str(first)
        print(f"  threads={_label(threads):<4} {verdict}")

    if config.json_path is not None:
        report = {
            "issue": "iossifovlab/gain#1275",
            "host": dataclasses.asdict(host),
            "config": {
                **dataclasses.asdict(config),
                "threads": [_label(t) for t in config.threads],
                "json_path": str(config.json_path),
            },
            "runs": [dataclasses.asdict(run) for run in sweep.runs],
            "first_divergent_processes": {
                _label(threads): first
                for threads, first in sweep.first_divergent_processes.items()
            },
        }
        config.json_path.write_text(json.dumps(report, indent=2, default=str))
        print(f"\nwrote {config.json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
