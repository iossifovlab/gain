#!/usr/bin/env python
"""Port one SpliceAI model to PyTorch and time it against ONNX and TF.

A spike (#2425): the shipped package has no PyTorch backend. The script
times on the CPU by default, and on the GPU with ``--device cuda`` (see
"The GPU mode" below). On the CPU, the script needs the ``torch-spike``
dependency group, which installs a CPU-only ``torch`` from the PyTorch CPU
index::

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

On the CPU, every runtime splits a batch into calls of at most 65536
positions (rows x width), the value of ``ONNX_POSITION_BUDGET`` and
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

The GPU mode (``--device cuda``)
-------------------------------
The same three runtimes, on the GPU, in float32 only: a lower precision
changes the scores. The script turns off TF32 in each runtime, because an
Ampere or newer GPU uses TF32 for float32 convolutions by default:
``torch.backends.cudnn.allow_tf32`` and
``torch.backends.cuda.matmul.allow_tf32`` in PyTorch, the ``use_tf32``
option of ``CUDAExecutionProvider`` in ONNX Runtime and
``tf.config.experimental.enable_tensor_float_32_execution(False)`` in
TensorFlow.

* PyTorch: the port moved to the CUDA device, in
  ``torch.inference_mode()``.
* ONNX Runtime: a session over ``models/spliceai1.onnx`` with the provider
  list ``["CUDAExecutionProvider"]`` and the shipped
  ``spliceai_session_options()``. Every ONNX session of the script passes an
  explicit provider list: ``["CPUExecutionProvider"]`` on the CPU and for
  the parity reference.
* TensorFlow: the Keras model of ``models/spliceai1.h5``, called with
  ``predict()`` with the whole batch as ``batch_size``. Otherwise Keras
  splits the batch at 32 rows.

The device check. Each child proves that its runtime runs on the GPU before
it times anything:

* PyTorch: ``torch.cuda.is_available()`` is true, and the output tensor of a
  one-window probe call is on a CUDA device.
* ONNX Runtime: ``get_providers()`` of the session starts with
  ``CUDAExecutionProvider``. ONNX Runtime falls back to the CPU without an
  error when it cannot load CUDA.
* TensorFlow: ``tf.config.list_physical_devices("GPU")`` is not empty.

A runtime that fails the check gives an ``error`` and no timing, and the
script exits 1. A CPU number labelled GPU looks valid, so the script never
gives one.

The batch ladder. The widths stay 10301 and 20201. ``--batches`` gives the
batch sizes, 1, 2, 4, 8, 16, 32 and 64 by default. The script sorts the
ladder and drops repeated sizes. On the GPU, the faster runtime changes with
the batch size. ``--rows`` applies only to the CPU, and ``--batches`` applies
only to the GPU.

The GPU path does no chunking: each call gets the whole batch. The CPU
budget of 65536 positions caps a call at 6 windows, and that flattens the
ladder.

Each runtime at each width runs in its own ``spawn`` process. For each batch
size, the child makes one untimed warm-up call, then times ``--reps`` calls,
5 by default on the GPU. The result is the median in ms per window. A timed
call ends when the output is a NumPy array in host memory, so the time
includes the copy back from the GPU on every runtime.

The ``oom`` rule. When a call runs out of GPU memory, the child records
``oom`` for that batch size and ends the ladder at that width. The script
goes on with the next width or runtime. The out-of-memory errors are
``torch.cuda.OutOfMemoryError``, ``tf.errors.ResourceExhaustedError`` and an
ONNX Runtime error with ``Failed to allocate memory`` in its message. An
``oom`` is a result, not a failure: the script exits 0 when the only
failures are ``oom`` results.

Parity on the GPU. ``--check-parity --device cuda`` compares the port on the
GPU with ``spliceai1.onnx`` on the CPU, with
``SPLICEAI_ONNX_INTRA_OP_THREADS=1``, over the cases of the CPU parity
check. The limit is 1e-4, not 1e-5: cuDNN picks convolution algorithms that
sum in a different order than the CPU. The script prints the largest
absolute difference and exits 1 when it is above the limit.

The ``--out`` JSON of a GPU run holds the ms per window of each runtime,
width and batch size, or ``oom``. It also holds the GPU name, the GPU memory
and the driver version from ``nvidia-smi``, the version, the CUDA version,
the cuDNN version and the device name that each runtime reports, the host
name, and the load average before and after the run. Run it on a quiet host:
nothing else on the GPU, and a load average below 2.

The throwaway CUDA venv. The CUDA builds stay out of ``uv.lock`` and out of
the pinned venv: they add several GB of ``nvidia-*`` packages, and one lock
cannot hold the CPU and the CUDA builds of ``torch``. ``onnxruntime-gpu``
needs the CUDA 12 wheels of its ``cuda`` and ``cudnn`` extras. A system
CUDA 13 does not satisfy it. TensorFlow finds no GPU until the
``nvidia/*/lib`` directories of the venv are on ``LD_LIBRARY_PATH``. From
the root of the gain checkout::

    python3.12 -m venv ~/spliceai-cuda
    . ~/spliceai-cuda/bin/activate
    pip install -e ./core -e "./spliceai_annotator[tensorflow]"
    pip uninstall -y onnxruntime
    pip install torch --index-url https://download.pytorch.org/whl/cu126
    pip install "onnxruntime-gpu[cuda,cudnn]" "tensorflow[and-cuda]"
    export LD_LIBRARY_PATH="$(python -c 'import glob, sysconfig; print(":".join(
        glob.glob(sysconfig.get_paths()["purelib"] + "/nvidia/*/lib")))')"
    cd spliceai_annotator
    python scripts/measure_torch_backend.py --device cuda --check-parity
    python scripts/measure_torch_backend.py --device cuda --out gpu.json

Usage
-----
    python spliceai_annotator/scripts/measure_torch_backend.py --check-parity
    python spliceai_annotator/scripts/measure_torch_backend.py --out t.json
    python spliceai_annotator/scripts/measure_torch_backend.py \\
        --device cuda --out gpu.json
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
import shutil
import statistics
import subprocess
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
DEVICES = ("cpu", "cuda")
#: The batch sizes of a GPU run, and its timed calls per batch size.
DEFAULT_GPU_BATCHES = (1, 2, 4, 8, 16, 32, 64)
DEFAULT_GPU_REPS = 5
#: The result of a batch size that ran out of GPU memory.
OOM = "oom"
#: The ``providers`` list of each ONNX session of the script.
CPU_PROVIDERS = ("CPUExecutionProvider",)
CUDA_PROVIDERS = ("CUDAExecutionProvider",)
#: The options of the CUDA provider: no TF32, so the GPU runs in float32.
CUDA_PROVIDER_OPTIONS = {"use_tf32": "0"}
#: The rows of the batched parity case.
PARITY_BATCH = 8
PARITY_TOLERANCE = 1e-5
#: The parity limit of the port on the GPU. cuDNN picks convolution
#: algorithms that sum in a different order than the CPU does.
GPU_PARITY_TOLERANCE = 1e-4
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


def onnx_session(providers: Sequence[str] = CPU_PROVIDERS) -> Any:
    """The ONNX Runtime session of ``MODEL``, with the shipped options.

    The ``providers`` list is explicit: ``onnxruntime-gpu`` refuses a
    session without one, because its build has more than one provider.
    """
    # pylint: disable=import-outside-toplevel
    import onnxruntime as ort
    from spliceai_annotator.spliceai_backend_onnx import (
        spliceai_session_options,
    )

    options = [CUDA_PROVIDER_OPTIONS if name == CUDA_PROVIDERS[0] else {}
               for name in providers]
    with model_path(".onnx") as path:
        return ort.InferenceSession(
            str(path), sess_options=spliceai_session_options(),
            providers=list(providers), provider_options=options)


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


def check_parity(
    widths: Sequence[int], torch_threads: int, device: str = "cpu",
) -> int:
    """Compare the port with ``spliceai1.onnx``. Return the exit code.

    The port runs on ``device``. The ONNX reference runs on the CPU.
    """
    # pylint: disable=import-outside-toplevel
    import torch

    if device == "cuda" and not torch.cuda.is_available():
        print("torch.cuda.is_available() is false: no parity on the GPU")
        return 1
    os.environ["SPLICEAI_ONNX_INTRA_OP_THREADS"] = "1"
    torch.set_num_threads(torch_threads)
    session = onnx_session()
    with model_path(".h5") as path:
        port = build_torch_port(str(path))
    if device == "cuda":
        disable_torch_tf32()
        port = port.to("cuda")
        tolerance, predict = GPU_PARITY_TOLERANCE, torch_cuda_predict
    else:
        tolerance, predict = PARITY_TOLERANCE, torch_predict
    largest = 0.0
    for width, rows in parity_cases(widths):
        x = synthetic_input(rows, width)
        expected = onnx_predict(session, x)
        actual = predict(port, x)
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
          f"(tolerance {tolerance:.0e})")
    return 0 if largest <= tolerance else 1


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
    function: Callable[..., dict[str, Any]], *args: Any,
) -> dict[str, Any]:
    """Run ``function(*args)`` in a fresh ``spawn`` process."""
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=1, mp_context=context) as pool:
        return pool.submit(function, *args).result()


#: The facts of GPU 0 that `gpu_facts` reads from ``nvidia-smi``.
GPU_FACTS = ("name", "memory", "driver_version")


class DeviceCheckError(RuntimeError):
    """A runtime does not run on the GPU."""


@dataclass
class GpuRuntime:
    """One runtime, loaded on the GPU and checked.

    ``predict`` takes a float32 batch and returns a NumPy array in host
    memory. ``is_oom`` tells an out-of-GPU-memory error of the runtime.
    """

    predict: Callable[[np.ndarray], np.ndarray]
    is_oom: Callable[[BaseException], bool]
    facts: dict[str, Any]


def gpu_facts() -> dict[str, str]:
    """The name, the memory and the driver version of GPU 0.

    ``nvidia-smi`` reports them. Without it, each value is ``unknown``.
    """
    unknown = dict.fromkeys(GPU_FACTS, "unknown")
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi is None:
        return unknown
    try:
        completed = subprocess.run(
            [nvidia_smi, "--query-gpu=name,memory.total,driver_version",
             "--format=csv,noheader"],
            capture_output=True, text=True, check=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return unknown
    lines = completed.stdout.splitlines()
    values = [v.strip() for v in lines[0].split(",")] if lines else []
    if len(values) != len(GPU_FACTS):
        return unknown
    return dict(zip(GPU_FACTS, values, strict=True))


def torch_cuda_predict(port: Any, x: np.ndarray) -> np.ndarray:
    """Run the port on the GPU over the whole batch ``x``, in one call."""
    return np.asarray(torch_cuda_forward(port, x).cpu().numpy())


def torch_cuda_forward(port: Any, x: np.ndarray) -> Any:
    """The output tensor of the port for ``x``, on the device of the port."""
    # pylint: disable=import-outside-toplevel
    import torch

    with torch.inference_mode():
        return port(torch.from_numpy(
            x.astype(np.float32, copy=False)).to("cuda"))


def disable_torch_tf32() -> None:
    """Turn off TF32 in cuDNN and in matmul, so the port runs in float32.

    PyTorch allows TF32 in cuDNN convolutions by default. On an Ampere or
    newer GPU, TF32 changes the scores.
    """
    # pylint: disable=import-outside-toplevel
    import torch

    torch.backends.cudnn.allow_tf32 = False
    torch.backends.cuda.matmul.allow_tf32 = False


def load_torch_gpu() -> GpuRuntime:
    """The port on the CUDA device, after the device check."""
    # pylint: disable=import-outside-toplevel
    import torch

    if not torch.cuda.is_available():
        raise DeviceCheckError("torch.cuda.is_available() is false")
    disable_torch_tf32()
    with model_path(".h5") as path:
        port = build_torch_port(str(path)).to("cuda")
    probe = torch_cuda_forward(port, synthetic_input(1, 10001))
    if probe.device.type != "cuda":
        raise DeviceCheckError(
            f"the output tensor is on the device {probe.device.type!r}")
    return GpuRuntime(
        predict=lambda x: torch_cuda_predict(port, x),
        is_oom=lambda error: isinstance(
            error, torch.cuda.OutOfMemoryError),
        facts={
            "runtime_version": torch.__version__,
            "cuda_version": torch.version.cuda,
            "cudnn_version": torch.backends.cudnn.version(),
            "device_name": torch.cuda.get_device_name(0),
        })


def load_onnx_gpu() -> GpuRuntime:
    """The ``CUDAExecutionProvider`` session, after the device check."""
    # pylint: disable=import-outside-toplevel
    import onnxruntime as ort
    from onnxruntime.capi import build_and_package_info

    session = onnx_session(CUDA_PROVIDERS)
    active = session.get_providers()
    if not active or active[0] != CUDA_PROVIDERS[0]:
        raise DeviceCheckError(f"the session providers are {active}")
    name = session.get_inputs()[0].name
    return GpuRuntime(
        predict=lambda x: session.run(
            None, {name: x.astype(np.float32, copy=False)})[0],
        is_oom=lambda error: "Failed to allocate memory" in str(error),
        facts={
            "runtime_version": ort.__version__,
            "cuda_version": getattr(
                build_and_package_info, "cuda_version", "unknown"),
            "cudnn_version": getattr(
                build_and_package_info, "cudnn_version", "unknown"),
            # ONNX Runtime does not name its device. The session runs on
            # device 0, the GPU that `nvidia-smi` lists first.
            "device_name": gpu_facts()["name"],
        })


def load_tensorflow_gpu() -> GpuRuntime:
    """The Keras model with a visible GPU, after the device check."""
    # pylint: disable=import-outside-toplevel
    import tensorflow as tf

    gpus = tf.config.list_physical_devices("GPU")
    if not gpus:
        raise DeviceCheckError(
            'tf.config.list_physical_devices("GPU") is empty')
    # TensorFlow allows TF32 by default. On an Ampere or newer GPU, TF32
    # changes the scores.
    tf.config.experimental.enable_tensor_float_32_execution(enabled=False)
    with model_path(".h5") as path:
        model = tf.keras.models.load_model(str(path))
    build = tf.sysconfig.get_build_info()
    return GpuRuntime(
        # The whole batch as `batch_size`: Keras splits it at 32 otherwise.
        predict=lambda x: model.predict(
            x.astype(np.float32, copy=False), batch_size=len(x), verbose=0),
        is_oom=lambda error: isinstance(
            error, tf.errors.ResourceExhaustedError),
        facts={
            "runtime_version": tf.__version__,
            "cuda_version": build.get("cuda_version", "unknown"),
            "cudnn_version": build.get("cudnn_version", "unknown"),
            "device_name": tf.config.experimental.get_device_details(
                gpus[0]).get("device_name", "unknown"),
        })


#: The facts that each runtime reports about itself on the GPU.
RUNTIME_FACTS = (
    "runtime_version", "cuda_version", "cudnn_version", "device_name")

GPU_LOADERS: dict[str, Callable[[], GpuRuntime]] = {
    "torch": load_torch_gpu,
    "onnx": load_onnx_gpu,
    "tensorflow": load_tensorflow_gpu,
}


def measure_gpu_runtime(
    runtime: str, width: int, batches: Sequence[int], reps: int,
) -> dict[str, Any]:
    """Load ``runtime`` on the GPU in this process, then time the ladder.

    A failed device check gives an ``error`` and no timing. An out-of-memory
    error gives `OOM` for its batch size and ends the ladder.
    """
    result: dict[str, Any] = {"runtime": runtime, "width": width}
    try:
        gpu = GPU_LOADERS[runtime]()
    except DeviceCheckError as error:
        result["error"] = f"device check failed: {error}"
        return result
    x = synthetic_input(max(batches), width).astype(np.float32)
    ms_per_window: dict[str, float | str] = {}
    for batch in batches:
        try:
            times = time_calls(gpu.predict, x[:batch], reps)
        # The catch is broad on purpose: ONNX Runtime has no OOM type, so
        # is_oom() matches its error by message. Each other error re-raises.
        except Exception as error:  # pylint: disable=broad-exception-caught
            if not gpu.is_oom(error):
                raise
            ms_per_window[str(batch)] = OOM
            break
        ms_per_window[str(batch)] = round(statistics.median(times) / batch, 3)
    result.update(reps=reps, ms_per_window=ms_per_window, **gpu.facts)
    return result


def time_calls(
    predict: Callable[[np.ndarray], np.ndarray], x: np.ndarray, reps: int,
) -> list[float]:
    """One untimed warm-up call, then the ms of each of ``reps`` calls."""
    predict(x)
    times = []
    for _ in range(reps):
        start = time.perf_counter()
        output = predict(x)
        times.append((time.perf_counter() - start) * 1000.0)
        if not isinstance(output, np.ndarray):
            raise TypeError(f"the output is {type(output)}, not an array")
    return times


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
            measure_runtime,
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


def format_gpu_table(
    results: list[dict[str, Any]], batches: Sequence[int],
) -> str:
    """Render the GPU results as a markdown table, one column per batch."""
    lines = [
        "| runtime | width | " + " | ".join(map(str, batches)) + " |",
        "|---|---|" + "---|" * len(batches),
    ]
    for r in results:
        if "error" in r:
            cells = [r["error"]]
        else:
            cells = [
                str(r["ms_per_window"].get(str(batch), ""))
                for batch in batches]
        lines.append(
            f"| {r['runtime']} | {r['width']} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def time_gpu_runtimes(args: argparse.Namespace) -> int:
    """Time every runtime on the GPU at every width over the ladder.

    Exit 1 when a runtime fails its device check. An `OOM` is a result.
    """
    loadavg_before = os.getloadavg()
    results = [
        run_in_child(
            measure_gpu_runtime, runtime, width, args.batches, args.reps)
        for width in args.widths
        for runtime in RUNTIMES
    ]
    loadavg = os.getloadavg()
    report = {
        "device": "cuda",
        "host": platform.node(),
        "loadavg_before": list(loadavg_before),
        "loadavg": list(loadavg),
        "model": MODEL,
        "batches": list(args.batches),
        "reps": args.reps,
        "gpu": gpu_facts(),
        "runtimes": {
            r["runtime"]: {key: r[key] for key in RUNTIME_FACTS}
            for r in results if "error" not in r},
        "results": results,
    }
    gpu = report["gpu"]
    print(f"host: {report['host']}, gpu: {gpu['name']}, "
          f"memory {gpu['memory']}, driver {gpu['driver_version']}")
    print(
        f"load average before: {' '.join(f'{v:.2f}' for v in loadavg_before)}"
        f", after: {' '.join(f'{v:.2f}' for v in loadavg)}")
    print(f"model {MODEL}, reps {args.reps}, ms per window\n")
    print(format_gpu_table(results, args.batches))
    if args.out is not None:
        args.out.write_text(json.dumps(report, indent=2))
        print(f"\nwrote {args.out}")
    return 1 if any("error" in r for r in results) else 0


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
    parser.add_argument(
        "--device", choices=DEVICES, default="cpu",
        help="time on the CPU (the default) or on the GPU")
    parser.add_argument(
        "--rows", type=positive_int, default=None,
        help=f"the batch of a CPU run, {DEFAULT_ROWS} by default")
    parser.add_argument(
        "--batches", type=positive_int, nargs="+", default=None,
        help="the batch ladder of a GPU run, "
        f"{' '.join(map(str, DEFAULT_GPU_BATCHES))} by default")
    parser.add_argument(
        "--reps", type=positive_int, default=None,
        help=f"the timed passes, {DEFAULT_REPS} on CPU and "
        f"{DEFAULT_GPU_REPS} on GPU by default")
    parser.add_argument(
        "--torch-threads", type=int, default=DEFAULT_TORCH_THREADS)
    parser.add_argument(
        "--check-parity", action="store_true",
        help="compare the port with spliceai1.onnx instead of timing")
    parser.add_argument(
        "--out", type=pathlib.Path, default=None,
        help="also write the timing results as JSON to this path")
    args = parser.parse_args(argv)
    if args.device == "cuda" and args.rows is not None:
        parser.error("--rows applies only to --device cpu: use --batches")
    if args.device == "cpu" and args.batches is not None:
        parser.error("--batches applies only to --device cuda: use --rows")
    if args.device == "cuda":
        # An `oom` ends the ladder, so the ladder runs from small to large.
        args.batches = sorted(set(args.batches or DEFAULT_GPU_BATCHES))
        args.reps = args.reps or DEFAULT_GPU_REPS
    else:
        args.rows = args.rows or DEFAULT_ROWS
        args.reps = args.reps or DEFAULT_REPS
    if args.check_parity:
        return check_parity(args.widths, args.torch_threads, args.device)
    if args.device == "cuda":
        return time_gpu_runtimes(args)
    return time_runtimes(args)


if __name__ == "__main__":
    raise SystemExit(main())
