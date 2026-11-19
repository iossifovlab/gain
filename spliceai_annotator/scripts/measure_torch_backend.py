#!/usr/bin/env python
"""Port one SpliceAI model to PyTorch and time it against ONNX and TF.

A spike (#2425): the shipped package has no PyTorch backend. The script
needs the ``torch-spike`` dependency group, which installs a CPU-only
``torch`` from the PyTorch CPU index::

    uv sync --package gain-spliceai-annotator --extra tensorflow \\
        --group dev --group torch-spike --frozen

The port
--------
``build_torch_port`` reads the functional Keras graph of
``models/spliceai1.h5`` and its weights with ``h5py``, and evaluates the
graph with ``torch.nn.Conv1d`` and ``torch.nn.BatchNorm1d`` layers in
inference mode. The port never imports TensorFlow.

The unit of comparison is the one model ``spliceai1`` on every runtime. The
shipped ``spliceai_predict()`` of each backend averages five models, so its
time is about five times the time of one model.

* PyTorch: the port, in ``torch.inference_mode()``, with
  ``torch.set_num_threads(--torch-threads)``, 4 by default.
* ONNX Runtime: one session over ``models/spliceai1.onnx`` with the shipped
  ``spliceai_session_options()``. The intra-op thread count is the default
  of ``SPLICEAI_ONNX_INTRA_OP_THREADS``: 4 when the variable is unset.
* TensorFlow: the Keras model of ``models/spliceai1.h5``, called with
  ``predict()``. TensorFlow keeps its own default thread pools.

Every runtime splits a batch into calls of at most 65536 positions (rows x
width), the value of ``ONNX_POSITION_BUDGET`` and
``TENSORFLOW_POSITION_BUDGET``: 6 rows at width 10301 and 3 rows at width
20201. An unchunked batch of 500 rows needs tens of GB.

The input
---------
Synthetic one-hot windows: ``numpy.random.default_rng(seed=width)`` picks the
bases and ``utils.one_hot_encode`` encodes them. The recipe is a copy of
``a_one_hot_window()`` in ``tests/test_spliceai_backends.py``. The script
does not import that module, because the module loads five models at import
and inflates the peak RSS.

The widths are 10301 and 20201. The annotator pads the window of the default
``distance`` of 50 to 10301 positions, and the widest default window
(``distance`` 5000) to 20201 positions.

Parity (``--check-parity``)
---------------------------
Compares the port with ``spliceai1.onnx`` for one window at width 10301, one
window at width 20201, and a batch of 8 windows at width 10301. The batch
spans two chunks. The ONNX session runs with
``SPLICEAI_ONNX_INTRA_OP_THREADS=1``, because a multi-thread ONNX pool is not
deterministic to about 3e-4 on a busy host. The script prints the largest
absolute difference and exits 1 when it is above 1e-5.

Timing (the default)
--------------------
Each runtime runs at each width in its own ``spawn`` process, because
``ru_maxrss`` is a high-water mark that never falls. A child imports its
runtime, loads the model, and records the peak RSS after the model load.
Then it builds the input batch, so the reading does not count the input.
Then it runs one untimed warm-up over 7 rows at width 10301 or 4 rows at
width 20201: one full chunk and one partial chunk. Then it times ``--reps``
passes over a batch of ``--rows`` windows, 1 pass of 500 rows by default,
and reports the median in ms per window. One pass of the three runtimes at
both widths takes about 6 minutes on a 16-core host.

The peak RSS is ``resource.getrusage(RUSAGE_SELF).ru_maxrss`` of the child,
read once after the model load and once after the timed passes.

The output also holds the import time of ``torch`` (the first ``import
torch`` in the PyTorch child), the installed size of the packages that the
``torch-spike`` group adds to the runtime dependencies, the ``tensorflow``
extra and the ``dev`` group (the files that ``importlib.metadata`` lists for
each package), the thread counts, the host name, the CPU model and the load
average before and after the run. A busy host measures its other work too,
so run the script on a quiet host.

Usage
-----
    python spliceai_annotator/scripts/measure_torch_backend.py --check-parity
    python spliceai_annotator/scripts/measure_torch_backend.py --out t.json
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import multiprocessing
import os
import pathlib
import platform
import resource
import statistics
import time
import tomllib
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from importlib.resources import as_file, files
from typing import Any

import h5py
import numpy as np
from packaging.requirements import Requirement
from spliceai_annotator.utils import one_hot_encode

#: The one model that every runtime runs.
MODEL = "spliceai1"
RUNTIMES = ("torch", "onnx", "tensorflow")
DEFAULT_WIDTHS = (10301, 20201)
DEFAULT_ROWS = 500
DEFAULT_REPS = 1
DEFAULT_TORCH_THREADS = 4
#: The rows of the batched parity case.
PARITY_BATCH = 8
PARITY_TOLERANCE = 1e-5
#: The positions (rows x width) in one call of the PyTorch port. The same
#: value as `ONNX_POSITION_BUDGET` and `TENSORFLOW_POSITION_BUDGET`. The
#: PyTorch child does not import those modules, because they import their
#: runtimes and inflate the peak RSS of the child.
TORCH_POSITION_BUDGET = 65536


def synthetic_input(rows: int, width: int) -> np.ndarray:
    """A one-hot window batch, built as ``a_one_hot_window()`` builds it.

    The recipe is copied from ``tests/test_spliceai_backends.py``. Importing
    that module loads the five models of the default backend.
    """
    rng = np.random.default_rng(seed=width)
    return np.concatenate([
        one_hot_encode("".join(rng.choice(list("ACGT"), size=width)))[None, :]
        for _ in range(rows)
    ], axis=0)


def chunks(x: np.ndarray, budget: int) -> Iterator[np.ndarray]:
    """The float32 row chunks of ``x``, each of at most ``budget`` positions.

    The same split as ``spliceai_backend_onnx.predict_in_chunks``: at least
    one row per chunk.
    """
    rows_per_run = max(1, budget // x.shape[1])
    for start in range(0, len(x), rows_per_run):
        yield x[start:start + rows_per_run].astype(np.float32, copy=False)


def model_path(suffix: str) -> Any:
    """A context manager that gives the path of ``MODEL`` + ``suffix``."""
    return as_file(
        files("spliceai_annotator") / "models" / f"{MODEL}{suffix}")


@dataclass
class KerasLayer:
    """One layer of the functional Keras graph in the ``.h5`` file."""

    name: str
    kind: str
    config: dict[str, Any]
    inputs: list[str]
    weights: dict[str, np.ndarray] = field(default_factory=dict)


def read_keras_graph(path: str) -> tuple[list[KerasLayer], str]:
    """The layers of the ``.h5`` model in graph order, and the output name.

    Reads the model config and the weights with ``h5py``, so that the port
    does not import TensorFlow.
    """
    with h5py.File(path, "r") as h5:
        config = json.loads(h5.attrs["model_config"])["config"]
        model_weights = h5["model_weights"]
        layers = []
        for layer in config["layers"]:
            name = layer["config"]["name"]
            weights: dict[str, np.ndarray] = {}
            if name in model_weights:
                model_weights[name].visititems(
                    lambda key, item, weights=weights: weights.__setitem__(
                        key.rsplit("/", 1)[-1].split(":")[0], item[()])
                    if isinstance(item, h5py.Dataset) else None)
            inbound = layer.get("inbound_nodes") or [[]]
            layers.append(KerasLayer(
                name=name,
                kind=layer["class_name"],
                config=layer["config"],
                inputs=[node[0] for node in inbound[0]],
                weights=weights,
            ))
        output = config["output_layers"][0][0]
    return layers, output


def build_torch_port(path: str) -> Any:
    """A ``torch.nn.Module`` that evaluates the Keras graph of ``path``.

    The module takes the annotator's ``(rows, width, 4)`` layout and
    returns ``(rows, width - 10000, 3)``, as the Keras model does. Inside,
    it runs the ``(rows, channels, positions)`` layout of ``torch.nn``.
    """
    # pylint: disable=import-outside-toplevel
    import torch
    from torch import nn

    layers, output = read_keras_graph(path)

    def conv(layer: KerasLayer) -> nn.Conv1d:
        kernel = layer.weights["kernel"]  # (width, in, out)
        width, channels_in, channels_out = kernel.shape
        dilation = layer.config["dilation_rate"][0]
        padding = (
            dilation * (width - 1) // 2
            if layer.config["padding"] == "same" else 0)
        module = nn.Conv1d(
            channels_in, channels_out, width,
            dilation=dilation, padding=padding)
        module.load_state_dict({
            "weight": torch.from_numpy(
                np.ascontiguousarray(kernel.transpose(2, 1, 0))),
            "bias": torch.from_numpy(layer.weights["bias"]),
        })
        return module

    def batch_norm(layer: KerasLayer) -> nn.BatchNorm1d:
        weights = layer.weights
        module = nn.BatchNorm1d(
            len(weights["gamma"]), eps=layer.config["epsilon"])
        module.load_state_dict({
            "weight": torch.from_numpy(weights["gamma"]),
            "bias": torch.from_numpy(weights["beta"]),
            "running_mean": torch.from_numpy(weights["moving_mean"]),
            "running_var": torch.from_numpy(weights["moving_variance"]),
            "num_batches_tracked": torch.tensor(0),
        })
        return module

    def activation(name: str, x: torch.Tensor) -> torch.Tensor:
        if name == "relu":
            return torch.relu(x)
        if name == "softmax":
            return torch.softmax(x, dim=1)
        if name == "linear":
            return x
        raise ValueError(f"no port of the activation {name!r}")

    # The last layer that reads each value. The forward pass drops a value
    # after that layer, as a compiled graph frees its buffers.
    last_reader = {
        name: index
        for index, layer in enumerate(layers) for name in layer.inputs}

    class KerasGraphPort(nn.Module):
        """The Keras graph, one ``torch.nn`` module per weighted layer."""

        def __init__(self) -> None:
            super().__init__()
            builders = {"Conv1D": conv, "BatchNormalization": batch_norm}
            self.layers = layers
            self.modules_by_name = nn.ModuleDict({
                layer.name: builders[layer.kind](layer)
                for layer in layers if layer.kind in builders})
            self.eval()

        def evaluate(
            self, layer: KerasLayer, inputs: list[torch.Tensor],
        ) -> torch.Tensor:
            """The output of one layer for its ``inputs``."""
            if layer.kind in ("Conv1D", "BatchNormalization"):
                return activation(
                    layer.config.get("activation", "linear"),
                    self.modules_by_name[layer.name](inputs[0]))
            if layer.kind == "Activation":
                return activation(layer.config["activation"], inputs[0])
            if layer.kind == "Add":
                return sum(inputs[1:], inputs[0])
            if layer.kind == "Cropping1D":
                left, right = layer.config["cropping"]
                return inputs[0][:, :, left:inputs[0].shape[2] - right]
            raise ValueError(f"no port of the layer {layer.kind!r}")

        def forward(self, x: torch.Tensor) -> torch.Tensor:
            """The ``(rows, width - 10000, 3)`` output for ``x``."""
            values: dict[str, torch.Tensor] = {}
            for index, layer in enumerate(self.layers):
                if layer.kind == "InputLayer":
                    values[layer.name] = x.transpose(1, 2)
                    continue
                values[layer.name] = self.evaluate(
                    layer, [values[name] for name in layer.inputs])
                for name in layer.inputs:
                    if last_reader[name] == index:
                        del values[name]
            return values[output].transpose(1, 2)

    return KerasGraphPort()


def torch_predict(port: Any, x: np.ndarray) -> np.ndarray:
    """Run the port over ``x`` in chunks of `TORCH_POSITION_BUDGET`."""
    # pylint: disable=import-outside-toplevel
    import torch

    with torch.inference_mode():
        return np.concatenate([
            port(torch.from_numpy(chunk)).numpy()
            for chunk in chunks(x, TORCH_POSITION_BUDGET)
        ], axis=0)


def onnx_session() -> Any:
    """The ONNX Runtime session of ``MODEL``, with the shipped options."""
    # pylint: disable=import-outside-toplevel
    import onnxruntime as ort
    from spliceai_annotator.spliceai_backend_onnx import (
        spliceai_session_options,
    )

    with model_path(".onnx") as path:
        return ort.InferenceSession(
            str(path), sess_options=spliceai_session_options())


def onnx_predict(session: Any, x: np.ndarray) -> np.ndarray:
    """Run ``session`` over ``x`` as the shipped ONNX backend does."""
    # pylint: disable=import-outside-toplevel
    from spliceai_annotator.spliceai_backend_onnx import (
        ONNX_POSITION_BUDGET,
        predict_in_chunks,
    )

    name = session.get_inputs()[0].name
    return predict_in_chunks(
        x, lambda chunk: session.run(None, {name: chunk})[0],
        ONNX_POSITION_BUDGET)


def parity_cases(widths: Sequence[int]) -> list[tuple[int, int]]:
    """The ``(width, rows)`` cases: one window per width, one batch."""
    return [(width, 1) for width in widths] + [(widths[0], PARITY_BATCH)]


def check_parity(widths: Sequence[int], torch_threads: int) -> int:
    """Compare the port with ``spliceai1.onnx``. Return the exit code."""
    # pylint: disable=import-outside-toplevel
    import torch

    os.environ["SPLICEAI_ONNX_INTRA_OP_THREADS"] = "1"
    torch.set_num_threads(torch_threads)
    session = onnx_session()
    with model_path(".h5") as path:
        port = build_torch_port(str(path))
    largest = 0.0
    for width, rows in parity_cases(widths):
        x = synthetic_input(rows, width)
        expected = onnx_predict(session, x)
        actual = torch_predict(port, x)
        if actual.shape != expected.shape:
            print(f"width {width}, rows {rows}: shape {actual.shape} "
                  f"against {expected.shape}")
            return 1
        difference = float(np.max(np.abs(actual - expected)))
        print(f"width {width}, rows {rows}: {difference:.3e}")
        # `np.maximum` keeps a NaN. The builtin `max` drops it, and a NaN
        # port would then pass the tolerance check.
        largest = float(np.maximum(largest, difference))
    print(f"largest absolute difference: {largest:.3e} "
          f"(tolerance {PARITY_TOLERANCE:.0e})")
    return 0 if largest <= PARITY_TOLERANCE else 1


def peak_rss_mb() -> float:
    """The peak RSS of this process so far. ``ru_maxrss`` is KB on Linux."""
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)


def load_runtime(
    runtime: str, width: int, torch_threads: int,
) -> tuple[Callable[[np.ndarray], np.ndarray], dict[str, Any]]:
    """Import and load ``runtime``. Return its predict call and the facts."""
    # pylint: disable=import-outside-toplevel
    facts: dict[str, Any] = {}
    if runtime == "torch":
        start = time.perf_counter()
        import torch
        facts["torch_import_s"] = round(time.perf_counter() - start, 3)
        torch.set_num_threads(torch_threads)
        facts["threads"] = torch.get_num_threads()
        with model_path(".h5") as path:
            port = build_torch_port(str(path))
        return lambda x: torch_predict(port, x), facts
    if runtime == "onnx":
        from spliceai_annotator.spliceai_backend_onnx import (
            onnx_intra_op_threads,
        )
        facts["threads"] = onnx_intra_op_threads()
        session = onnx_session()
        return lambda x: onnx_predict(session, x), facts
    if runtime == "tensorflow":
        from spliceai_annotator.spliceai_backend_tensorflow import (
            TENSORFLOW_POSITION_BUDGET,
            tf,
        )
        facts["threads"] = "default"
        with model_path(".h5") as path:
            model = tf.keras.models.load_model(str(path))
        rows_per_run = max(1, TENSORFLOW_POSITION_BUDGET // width)
        return (
            lambda x: model.predict(x, batch_size=rows_per_run, verbose=0),
            facts)
    raise ValueError(f"unknown runtime {runtime!r}")


def measure_runtime(
    runtime: str, width: int, rows: int, reps: int, torch_threads: int,
) -> dict[str, Any]:
    """Load ``runtime`` in this process, then time ``reps`` passes."""
    predict, facts = load_runtime(runtime, width, torch_threads)
    # The input is built after the reading, so that the peak RSS after the
    # model load does not count it.
    peak_after_load = peak_rss_mb()
    x = synthetic_input(rows, width)
    # The warm-up covers one full chunk and one partial chunk, so that
    # every runtime has seen both batch shapes of the timed pass.
    rows_per_run = max(1, TORCH_POSITION_BUDGET // width)
    predict(x[:rows_per_run + 1])
    times = []
    for _ in range(reps):
        start = time.perf_counter()
        predict(x)
        times.append((time.perf_counter() - start) * 1000.0)
    return {
        "runtime": runtime,
        "width": width,
        "rows": rows,
        "reps": reps,
        "ms_per_window": round(statistics.median(times) / rows, 2),
        "median_ms_per_pass": round(statistics.median(times), 1),
        "peak_rss_after_load_mb": peak_after_load,
        "peak_rss_mb": peak_rss_mb(),
        "pid": os.getpid(),
        **facts,
    }


def run_in_child(
    runtime: str, width: int, rows: int, reps: int, torch_threads: int,
) -> dict[str, Any]:
    """Run `measure_runtime` in a fresh ``spawn`` process."""
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=1, mp_context=context) as pool:
        return pool.submit(
            measure_runtime, runtime, width, rows, reps, torch_threads,
        ).result()


def pyproject_requirements(group: str) -> list[Requirement]:
    """The requirements of the dependency group ``group`` of this plugin.

    The name ``baseline`` gives what the timing run installs without the
    ``torch-spike`` group: the runtime dependencies, the ``tensorflow``
    extra and the ``dev`` group.
    """
    pyproject = pathlib.Path(__file__).parent.parent / "pyproject.toml"
    with pyproject.open("rb") as stream:
        config = tomllib.load(stream)
    if group == "baseline":
        specs = (
            config["project"]["dependencies"]
            + config["project"]["optional-dependencies"]["tensorflow"]
            + config["dependency-groups"]["dev"])
    else:
        specs = config["dependency-groups"][group]
    return [Requirement(spec) for spec in specs]


def installed_mb(distribution: importlib.metadata.Distribution) -> float:
    """The size of the files that ``distribution`` installed, in MB."""
    total = 0
    for path in distribution.files or []:
        located = pathlib.Path(str(distribution.locate_file(path)))
        if located.is_file():
            total += located.stat().st_size
    return total / 2**20


def installed_closure(
    requirements: list[Requirement],
) -> dict[str, importlib.metadata.Distribution]:
    """The installed packages that ``requirements`` reach, recursively.

    The markers are evaluated for this interpreter. A requirement that is
    not installed ends the walk on that branch.
    """
    reached: dict[str, importlib.metadata.Distribution] = {}
    pending = list(requirements)
    while pending:
        requirement = pending.pop()
        if requirement.marker is not None and \
                not requirement.marker.evaluate({"extra": ""}):
            continue
        name = requirement.name.lower().replace("_", "-")
        if name in reached:
            continue
        try:
            distribution = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            continue
        reached[name] = distribution
        pending.extend(
            Requirement(spec) for spec in distribution.requires or [])
    return reached


def installed_size(group: str) -> dict[str, float]:
    """The installed MB of each package that ``group`` adds.

    A package that the runtime dependencies, the ``tensorflow`` extra or the
    ``dev`` group already reach does not count: the group does not add it.
    """
    present = installed_closure(pyproject_requirements("baseline"))
    added = installed_closure(pyproject_requirements(group))
    return {
        name: round(installed_mb(distribution), 1)
        for name, distribution in sorted(added.items())
        if name not in present
    }


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
        ("| runtime | width | rows | ms per window | threads "
         "| peak RSS after load MB | peak RSS MB |"),
        "|---|---|---|---|---|---|---|",
    ]
    lines.extend(
        f"| {r['runtime']} | {r['width']} | {r['rows']} "
        f"| {r['ms_per_window']} | {r['threads']} "
        f"| {r['peak_rss_after_load_mb']} | {r['peak_rss_mb']} |"
        for r in results)
    return "\n".join(lines)


def time_runtimes(args: argparse.Namespace) -> int:
    """Time every runtime at every width, print and write the results."""
    loadavg_before = os.getloadavg()
    results = [
        run_in_child(
            runtime, width, args.rows, args.reps, args.torch_threads)
        for width in args.widths
        for runtime in RUNTIMES
    ]
    loadavg = os.getloadavg()
    sizes = installed_size("torch-spike")
    torch_result = next(r for r in results if r["runtime"] == "torch")
    report = {
        "host": platform.node(),
        "cpu_model": cpu_model(),
        "cpu_count": os.cpu_count(),
        "loadavg_before": list(loadavg_before),
        "loadavg": list(loadavg),
        "model": MODEL,
        "rows": args.rows,
        "reps": args.reps,
        "position_budget": TORCH_POSITION_BUDGET,
        "torch_threads": torch_result["threads"],
        "onnx_intra_op_threads": next(
            r["threads"] for r in results if r["runtime"] == "onnx"),
        "torch_import_s": torch_result["torch_import_s"],
        "torch_spike_installed_mb": round(sum(sizes.values()), 1),
        "torch_spike_packages_mb": sizes,
        "results": results,
    }
    print(f"host: {report['host']}, cpu: {report['cpu_model']}, "
          f"cpu count {report['cpu_count']}")
    print(
        f"load average before: {' '.join(f'{v:.2f}' for v in loadavg_before)}"
        f", after: {' '.join(f'{v:.2f}' for v in loadavg)}")
    print(
        f"model {MODEL}, rows {args.rows}, reps {args.reps}, "
        f"torch import {report['torch_import_s']} s, "
        f"torch-spike installed {report['torch_spike_installed_mb']} MB\n")
    print(format_table(results))
    if args.out is not None:
        args.out.write_text(json.dumps(report, indent=2))
        print(f"\nwrote {args.out}")
    return 0


def positive_int(text: str) -> int:
    """An ``argparse`` type: an integer of at least 1."""
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"{text} is not a positive integer")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    """Check the parity of the port, or time the three runtimes."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--widths", type=int, nargs="+", default=list(DEFAULT_WIDTHS))
    parser.add_argument("--rows", type=positive_int, default=DEFAULT_ROWS)
    parser.add_argument("--reps", type=positive_int, default=DEFAULT_REPS)
    parser.add_argument(
        "--torch-threads", type=int, default=DEFAULT_TORCH_THREADS)
    parser.add_argument(
        "--check-parity", action="store_true",
        help="compare the port with spliceai1.onnx instead of timing")
    parser.add_argument(
        "--out", type=pathlib.Path, default=None,
        help="also write the timing results as JSON to this path")
    args = parser.parse_args(argv)
    if args.check_parity:
        return check_parity(args.widths, args.torch_threads)
    return time_runtimes(args)


if __name__ == "__main__":
    raise SystemExit(main())
