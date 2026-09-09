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
import itertools
import json
import multiprocessing
import os
import pathlib
import platform
from collections.abc import Callable, Sequence
from typing import Any, Protocol, cast

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
    #: The intra-op setting each worker's runtime reported back, and the
    #: OS thread count the process ended up holding. The first *echoes the
    #: request* on both backends (ONNX returns the field just assigned;
    #: TensorFlow returns the configured value, 0 meaning "size to the
    #: machine"), so it confirms the setter ran but is not independent
    #: evidence. `observed_os_threads` is the independent measurement: it
    #: separates a pinned worker from an auto one whatever was asked.
    observed_intra_op_threads: tuple[int, ...] = ()
    observed_os_threads: tuple[int, ...] = ()
    #: The box's 1/5/15-minute load when this point *finished* -- the load
    #: the point itself created. #1275 asks for it beside every figure,
    #: because a "reproducible" point taken on an idle box says nothing about
    #: a contended one. `load_average_before` is the reading on entry, which
    #: is mostly the previous point's.
    load_average: tuple[float, float, float] | None = None
    load_average_before: tuple[float, float, float] | None = None
    #: How many distinct inputs the workers reported predicting on. Anything
    #: but 1 invalidates the point: each worker builds the fixed input
    #: itself, and input drift would otherwise read as runtime drift.
    distinct_inputs: int = 1
    #: The largest absolute difference between any worker's first answer and
    #: worker 0's. Digests say *that* answers differ; #1275 asks by how much.
    max_abs_deviation: float | None = None

    @property
    def reproducible(self) -> bool:
        """One answer, one input, and something actually ran.

        A point that predicted nothing, or whose workers disagreed about
        what they predicted on, is not evidence of a stable runtime.
        """
        return (
            self.total_predictions > 0
            and self.distinct_outputs == 1
            and self.distinct_inputs == 1
        )


def summarise_run(
    processes: int,
    threads: int | None,
    worker_digests: Sequence[Sequence[str]],
    *,
    observed_intra_op_threads: Sequence[int] = (),
    observed_os_threads: Sequence[int] = (),
    load_average: tuple[float, float, float] | None = None,
    load_average_before: tuple[float, float, float] | None = None,
    input_digests: Sequence[str] = (),
    max_abs_deviation: float | None = None,
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
        load_average_before=load_average_before,
        distinct_inputs=len(set(input_digests)) if input_digests else 1,
        max_abs_deviation=max_abs_deviation,
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
    logical_cpus: int
    #: #1206 left "oversubscription degree alone, or AVX-512 kernels?" open,
    #: so which ISA a result came from is part of the result.
    avx512: bool
    load_average: tuple[float, float, float]


def read_host_info(
    cpuinfo: str,
    hostname: str,
    logical_cpus: int,
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
        logical_cpus=logical_cpus,
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
        logical_cpus=os.cpu_count() or 0,
        load_average=(load[0], load[1], load[2]),
    )


def default_process_sweep(logical_cpus: int) -> tuple[int, ...]:
    """Doubling process counts up to, and including, the core count.

    The core count is the point of the sweep: the annotation CLI's ``-j``
    defaults to it, so that is the contention degree the shipped default
    actually produces. The integration job's five xdist workers sit well
    below it.
    """
    counts = []
    k = 1
    while k < logical_cpus:
        counts.append(k)
        k *= 2
    counts.append(logical_cpus)
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

#: The ensemble is five models, and *both* shipped backends load all five per
#: process. That is not a detail: ONNX Runtime opens one thread pool per
#: session, so an ensemble costs 5 x `ONNX_INTRA_OP_THREADS` intra-op threads
#: per process, while TensorFlow's pools are process-wide and shared across
#: all five. A probe that loaded a single model would under-thread the ONNX
#: arm five-fold and land it *below* the contention degree #1206 drifted at,
#: while leaving the TensorFlow arm at full strength -- the two arms would
#: not be comparable, and a silent ONNX arm would prove nothing.
ENSEMBLE_SIZE = 5

#: The model's receptive field: outputs are `width - 10000` positions long,
#: so anything at or below this has nothing to predict.
MODEL_CONTEXT = 10000


@dataclasses.dataclass(frozen=True)
class ProbeConfig:
    """One sweep's worth of instructions."""

    backend: str
    models: int
    width: int
    batch: int
    iterations: int
    processes: tuple[int, ...]
    #: None means "leave the runtime's own default alone" -- the arm that
    #: reproduces what the shipped backend does today.
    threads: tuple[int | None, ...]
    #: Turn on the runtime's own determinism switch --
    #: `tf.config.experimental.enable_op_determinism()` for TensorFlow,
    #: `SessionOptions.use_deterministic_compute` for ONNX Runtime. Off by
    #: default, because the default arm has to be what ships.
    op_determinism: bool
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


def _width(raw: str) -> int:
    value = _positive(raw)
    if value <= MODEL_CONTEXT:
        raise argparse.ArgumentTypeError(
            f"width must exceed the model's {MODEL_CONTEXT}-position context "
            f"or there is nothing to predict: {raw!r}")
    return value


def parse_args(argv: Sequence[str] | None = None) -> ProbeConfig:
    """Read a sweep off the command line."""
    logical_cpus = os.cpu_count() or 1
    sweep_default = default_process_sweep(logical_cpus)
    parser = argparse.ArgumentParser(
        description="Does a SpliceAI model runtime answer differently under "
                    "load? (iossifovlab/gain#1275)")
    parser.add_argument(
        "--backend", choices=sorted(_LOADERS), default="tensorflow",
        help="model runtime to probe (default: the shipped one); the two "
             "'fake' backends need no model and exist to self-test this "
             "script's own plumbing")
    parser.add_argument(
        "--models", type=int, choices=range(1, ENSEMBLE_SIZE + 1),
        default=ENSEMBLE_SIZE,
        help=f"ensemble models to load per worker (default: {ENSEMBLE_SIZE}, "
             "what both shipped backends load)")
    parser.add_argument(
        "--width", type=_width, default=DEFAULT_WIDTH,
        help=f"sequence length (default: {DEFAULT_WIDTH}, distance=50)")
    parser.add_argument(
        "--batch", type=_positive, default=1,
        help="windows per prediction (default: 1); Keras chunks `predict` at "
             "32 internally, so a batch changes the reduction shape")
    parser.add_argument(
        "--iterations", type=_positive, default=3,
        help="predictions per worker after the barrier (default: 3)")
    parser.add_argument(
        "--processes", type=_positive_list, default=sweep_default,
        help="comma-separated contending process counts "
             f"(default: {','.join(str(k) for k in sweep_default)})")
    parser.add_argument(
        "--threads", type=_thread_list, default=(None, 1),
        help="comma-separated intra/inter-op settings; 'auto' leaves the "
             "runtime's own default (default: auto,1)")
    parser.add_argument(
        "--op-determinism", action="store_true",
        help="enable the runtime's determinism controls (TensorFlow's "
             "enable_op_determinism, ONNX Runtime's use_deterministic_compute)")
    parser.add_argument(
        "--seed", type=int, default=0,
        help="seed for the fixed input (default: 0)")
    parser.add_argument(
        "--json", dest="json_path", type=pathlib.Path, default=None,
        help="write the full result here as JSON")
    args = parser.parse_args(argv)
    return ProbeConfig(
        backend=args.backend,
        models=args.models,
        width=args.width,
        batch=args.batch,
        iterations=args.iterations,
        processes=tuple(args.processes),
        threads=tuple(args.threads),
        op_determinism=args.op_determinism,
        seed=args.seed,
        json_path=args.json_path,
    )


def _os_thread_count() -> int:
    try:
        return len(os.listdir("/proc/self/task"))
    except OSError:
        return 0


def _load_tensorflow(
    models: int, threads: int | None, *, op_determinism: bool,
) -> tuple[Predict, int]:
    """Load the ensemble as Keras models, pinning the pools first if asked.

    Both settings must be applied before TensorFlow initialises its runtime,
    which is why this runs inside the worker process and why the parent never
    imports TensorFlow.

    `CUDA_VISIBLE_DEVICES=-1` is set here but not by the shipped backend: the
    question is about CPU kernels, and a probe that silently landed on a GPU
    on some host would answer a different one.
    """
    # pylint: disable=import-outside-toplevel
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
    import tensorflow as tf
    if op_determinism:
        tf.config.experimental.enable_op_determinism()
    if threads is not None:
        tf.config.threading.set_intra_op_parallelism_threads(threads)
        tf.config.threading.set_inter_op_parallelism_threads(threads)
    loaded = [
        tf.keras.models.load_model(str(MODELS_DIR / f"spliceai{i}.h5"))
        for i in range(1, models + 1)
    ]
    # Echoes the request (0 = "size it to the machine"); it confirms the
    # setter ran, and is not an independent read-back. `observed_os_threads`
    # is the independent evidence.
    requested = tf.config.threading.get_intra_op_parallelism_threads()

    def predict(x: np.ndarray) -> np.ndarray:
        return cast("np.ndarray", np.mean(
            [model.predict(x, verbose=0) for model in loaded], axis=0))

    return predict, requested


def _load_onnx(
    models: int, threads: int | None, *, op_determinism: bool,
) -> tuple[Predict, int]:
    """Open the ensemble's sessions with the shipped backend's settings.

    #1275 asks for the ONNX arm as a comparison baseline, so it must be the
    configuration that actually ships -- the three settings #297 and #400
    found load-bearing, and one session per ensemble model, each with its own
    intra-op pool.
    """
    # pylint: disable=import-outside-toplevel
    import onnxruntime as ort
    options = ort.SessionOptions()
    options.add_session_config_entry("session.intra_op.allow_spinning", "0")
    options.graph_optimization_level = \
        ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED
    options.intra_op_num_threads = (
        ONNX_INTRA_OP_THREADS if threads is None else threads)
    options.use_deterministic_compute = op_determinism
    sessions = [
        ort.InferenceSession(
            str(MODELS_DIR / f"spliceai{i}.onnx"), options,
            providers=["CPUExecutionProvider"])
        for i in range(1, models + 1)
    ]
    names = [session.get_inputs()[0].name for session in sessions]

    def predict(x: np.ndarray) -> np.ndarray:
        return cast("np.ndarray", np.mean([
            session.run(None, {name: x})[0]
            for session, name in zip(sessions, names, strict=True)
        ], axis=0))

    # Echoes the request, as above.
    return predict, options.intra_op_num_threads


def _load_fake(
    models: int, threads: int | None, *, op_determinism: bool,
) -> tuple[Predict, int]:
    """A runtime that always answers the same thing. No model, no import.

    Exists so the barrier, the process pool and the fold can be exercised in
    CI: those are where a spurious "reproducible" would come from, and
    without a model-free arm they would only ever run by hand.
    """
    del models, op_determinism

    def predict(x: np.ndarray) -> np.ndarray:
        return np.full((1, 3), 0.25, dtype=np.float32) * np.float32(x.shape[1])

    return predict, 1 if threads is None else threads


def _load_fake_drift(
    models: int, threads: int | None, *, op_determinism: bool,
) -> tuple[Predict, int]:
    """A runtime that answers differently every call -- the positive control.

    The whole sweep rests on being able to tell "the runtime is stable" from
    "the instrument cannot see". This arm makes the difference observable end
    to end rather than only in a unit test.
    """
    del models, op_determinism
    calls = itertools.count()

    def predict(x: np.ndarray) -> np.ndarray:
        del x
        return np.array(
            [[0.25, 0.25, 0.25 + next(calls) * 1e-6]], dtype=np.float32)

    return predict, 1 if threads is None else threads


class _Loader(Protocol):
    """How the worker asks a runtime for something it can predict with."""

    def __call__(
        self, models: int, threads: int | None, *, op_determinism: bool,
    ) -> tuple[Predict, int]:
        ...


_LOADERS: dict[str, _Loader] = {
    "tensorflow": _load_tensorflow,
    "onnx": _load_onnx,
    "fake": _load_fake,
    "fake-drift": _load_fake_drift,
}

#: Set in each worker by the pool initializer; the workers meet here so that
#: every measured prediction happens while all K processes are running.
_BARRIER: Any = None

#: Generous: a worker that has to load an ensemble on a saturated box can
#: take a while, and a hang should fail rather than wedge the sweep.
_BARRIER_TIMEOUT_SECONDS = 900


def _init_worker(barrier: Any) -> None:
    global _BARRIER  # pylint: disable=global-statement
    _BARRIER = barrier


def _run_worker(
    spec: tuple[str, int, int | None, bool, int, int, int, int],
) -> tuple[tuple[str, ...], str, int, int, np.ndarray, float]:
    """One contending process: load, warm up, sync, then measure."""
    (backend, models, threads, op_determinism,
     width, batch, seed, iterations) = spec
    predict, requested = _LOADERS[backend](
        models, threads, op_determinism=op_determinism)
    x = np.repeat(fixed_input(width, seed), batch, axis=0)
    # Untimed: the first call builds the graph and allocates the arenas, and
    # doing that while other workers are already predicting would measure
    # start-up skew rather than contention.
    predict(x)
    if _BARRIER is None:
        # Never reached via `run_point`, which always installs one. Loud
        # rather than silent: unsynchronised workers would stagger, and a
        # staggered run reporting "reproducible" is the exact false negative
        # this probe exists to avoid.
        raise RuntimeError("worker started without a barrier; the run would "
                           "not be contended")
    _BARRIER.wait(timeout=_BARRIER_TIMEOUT_SECONDS)

    # Deviation has to come from the *measured* predictions: the warm-up call
    # happens before the barrier, when nothing is contending, so comparing
    # warm-ups would report 0.0 however badly the measured ones drifted.
    # Only the first measured answer is retained; the rest are folded into a
    # running peak, so memory stays flat in `iterations`.
    measured: list[np.ndarray] = []
    within = 0.0

    def recording(value: np.ndarray) -> np.ndarray:
        nonlocal within
        out = predict(value)
        if not measured:
            measured.append(out)
        else:
            within = max(
                within, float(np.max(np.abs(out - measured[0]))))
        return out

    digests = probe_predictions(recording, x, iterations)
    exemplar = measured[0] if measured else np.zeros(1, dtype=np.float32)
    return (
        digests, digest_output(x), requested, _os_thread_count(),
        exemplar, within)


def _max_abs_deviation(
    exemplars: Sequence[np.ndarray],
    within_worker: Sequence[float],
) -> float | None:
    """How far apart the answers actually are.

    Digests say *that* answers differ; #1275 asks by *how much* (it cites
    3e-4 in a probability and ~5e-5 in a `DS_*`). Without this a red run
    would need a second instrument to interpret.

    Divergence takes two shapes and both count: one worker's answer changing
    between iterations, and workers disagreeing with each other. Reporting
    only the second would read 0.0 for a runtime that is unstable inside
    every process but identically so across them.
    """
    if not exemplars:
        return None
    baseline = exemplars[0]
    across = max(
        float(np.max(np.abs(exemplar - baseline))) for exemplar in exemplars)
    return max([across, *within_worker])


def run_point(
    config: ProbeConfig,
    processes: int,
    threads: int | None,
) -> RunSummary:
    """Run one (process count, thread setting) point of the sweep."""
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(processes)
    spec = (
        config.backend, config.models, threads, config.op_determinism,
        config.width, config.batch, config.seed, config.iterations)
    load_before = os.getloadavg()
    with ctx.Pool(
        processes, initializer=_init_worker, initargs=(barrier,),
    ) as pool:
        results = pool.map(_run_worker, [spec] * processes, chunksize=1)
    # After, not before: the load entering a point is mostly the *previous*
    # point's, while this one is the load the point itself created.
    load_after = os.getloadavg()
    return summarise_run(
        processes=processes,
        threads=threads,
        worker_digests=[digests for digests, _, _, _, _, _ in results],
        input_digests=[x_digest for _, x_digest, _, _, _, _ in results],
        observed_intra_op_threads=[req for _, _, req, _, _, _ in results],
        observed_os_threads=[n for _, _, _, n, _, _ in results],
        load_average=(load_after[0], load_after[1], load_after[2]),
        load_average_before=(
            load_before[0], load_before[1], load_before[2]),
        max_abs_deviation=_max_abs_deviation(
            [exemplar for _, _, _, _, exemplar, _ in results],
            [within for _, _, _, _, _, within in results]),
    )


def run_sweep(config: ProbeConfig) -> SweepSummary:
    """Every thread setting crossed with every process count."""
    runs = []
    for threads in config.threads:
        for processes in config.processes:
            run = run_point(config, processes, threads)
            print(_run_line(config.backend, run), flush=True)
            runs.append(run)
    return summarise_sweep(runs)


def _label(threads: int | None) -> str:
    return "auto" if threads is None else str(threads)


def _run_line(backend: str, run: RunSummary) -> str:
    """One line per sweep point, readable while the sweep is still running."""
    deviation = (
        "-" if run.max_abs_deviation is None
        else f"{run.max_abs_deviation:.3g}")
    load = "-" if run.load_average is None else f"{run.load_average[0]:.1f}"
    return (
        f"  {backend:<11} threads={_label(run.threads):<4} "
        f"K={run.processes:<3} "
        f"distinct={run.distinct_outputs:<3} "
        f"max_abs_dev={deviation:<10} "
        f"os_threads={sorted(set(run.observed_os_threads))} "
        f"load_after={load}")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the sweep and report it. Non-zero if anything diverged."""
    config = parse_args(argv)
    host = capture_host()
    print(
        f"host={host.hostname} cpu={host.cpu_model!r} "
        f"logical_cpus={host.logical_cpus} "
        f"avx512={host.avx512} load={host.load_average}")
    print(
        f"backend={config.backend} models={config.models} "
        f"width={config.width} batch={config.batch} "
        f"iterations={config.iterations} "
        f"op_determinism={config.op_determinism} "
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
    return 0 if all(run.reproducible for run in sweep.runs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
