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
import dataclasses
import hashlib
import os
import pathlib
import platform
from collections.abc import Callable, Sequence

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

    @property
    def reproducible(self) -> bool:
        return self.distinct_outputs <= 1


def summarise_run(
    processes: int,
    threads: int | None,
    worker_digests: Sequence[Sequence[str]],
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
