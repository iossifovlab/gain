# pylint: disable=C0114,C0116
"""The #2425 spike: a PyTorch port of ``spliceai1`` against ONNX and TF.

The tests that run the port skip where ``torch`` is not installed: CI does
not sync the ``torch-spike`` group. The other tests need no ``torch``, so
they run in CI.
"""
import contextlib
import importlib.util
import json
import pathlib
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from types import ModuleType, SimpleNamespace
from typing import Any

import numpy as np
import onnxruntime
import pytest
from spliceai_annotator.spliceai_backend_onnx import ONNX_POSITION_BUDGET

from .test_spliceai_backends import a_one_hot_window, load_tensorflow_backend

SCRIPT = (
    pathlib.Path(__file__).parent.parent / "scripts"
    / "measure_torch_backend.py")
UV_LOCK = pathlib.Path(__file__).parents[2] / "uv.lock"
PYTORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"


def _load_script(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "measure_torch_backend", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # The dataclasses of the script resolve their string annotations
    # through `sys.modules`. The monkeypatch removes the entry afterwards.
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def test_parity_check_reports_the_largest_difference_against_onnx() -> None:
    pytest.importorskip("torch")

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--check-parity", "--widths", "10001"],
        capture_output=True, text=True, check=False, timeout=300)

    assert completed.returncode == 0, completed.stderr
    match = re.search(
        r"largest absolute difference: (\S+)", completed.stdout)
    assert match is not None, completed.stdout
    assert float(match.group(1)) <= 1e-5


def test_timing_run_writes_every_runtime_and_the_host_fields(
    tmp_path: pathlib.Path,
) -> None:
    pytest.importorskip("torch")
    pytest.importorskip("tensorflow")
    out = tmp_path / "result.json"

    completed = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--widths", "10001", "--rows", "2", "--reps", "1",
         "--out", str(out)],
        capture_output=True, text=True, check=False, timeout=300)

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(out.read_text())
    results = {r["runtime"]: r for r in payload["results"]}
    assert set(results) == {"torch", "onnx", "tensorflow"}
    for result in results.values():
        assert result["width"] == 10001
        assert result["rows"] == 2
        assert result["ms_per_window"] > 0
        assert result["peak_rss_after_load_mb"] > 0
    assert results["torch"]["torch_import_s"] > 0
    assert payload["torch_threads"] == 4
    assert payload["torch_spike_installed_mb"] > 0
    assert payload["cpu_model"]
    assert len(payload["loadavg"]) == 3
    assert "| torch | 10001 |" in completed.stdout


def test_parity_check_fails_when_the_port_returns_nan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    torch = pytest.importorskip("torch")
    # `check_parity` sets the variable for its one-thread ONNX session. The
    # monkeypatch restores it, so later ONNX sessions get the default.
    monkeypatch.setenv("SPLICEAI_ONNX_INTRA_OP_THREADS", "1")
    mtb = _load_script(monkeypatch)

    def nan_predict(_port: Any, x: np.ndarray) -> np.ndarray:
        return np.full((x.shape[0], x.shape[1] - 10000, 3), np.nan,
                       dtype=np.float32)

    monkeypatch.setattr(mtb, "build_torch_port", lambda _path: None)
    monkeypatch.setattr(mtb, "torch_predict", nan_predict)

    # The current thread count leaves the torch state of the session as is.
    assert mtb.check_parity(
        [10001], torch_threads=torch.get_num_threads()) == 1


def test_the_port_splits_a_batch_as_the_shipped_backends_do(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mtb = _load_script(monkeypatch)
    tensorflow_backend = load_tensorflow_backend()

    assert mtb.TORCH_POSITION_BUDGET == ONNX_POSITION_BUDGET
    assert mtb.TORCH_POSITION_BUDGET == (
        tensorflow_backend.TENSORFLOW_POSITION_BUDGET)


def test_the_port_runs_a_batch_in_chunks_of_the_position_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    torch = pytest.importorskip("torch")
    mtb = _load_script(monkeypatch)
    calls: list[int] = []

    def port(chunk: Any) -> Any:
        calls.append(chunk.shape[0])
        return torch.zeros((chunk.shape[0], chunk.shape[1] - 10000, 3))

    actual = mtb.torch_predict(port, np.zeros((8, 10301, 4), dtype=np.int8))

    assert calls == [6, 2]
    assert actual.shape == (8, 301, 3)


def test_the_port_does_not_import_tensorflow() -> None:
    pytest.importorskip("torch")
    probe = (
        "import importlib.util, sys\n"
        "spec = importlib.util.spec_from_file_location('mtb', sys.argv[1])\n"
        "mtb = importlib.util.module_from_spec(spec)\n"
        "sys.modules['mtb'] = mtb\n"
        "spec.loader.exec_module(mtb)\n"
        "with mtb.model_path('.h5') as path:\n"
        "    mtb.build_torch_port(str(path))\n"
        "print(sorted(m for m in sys.modules if m.startswith('tensorflow')))\n"
    )

    completed = subprocess.run(
        [sys.executable, "-c", probe, str(SCRIPT)],
        capture_output=True, text=True, check=False, timeout=300)

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "[]"


def test_parity_runs_onnx_on_one_thread_at_the_default_widths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    torch = pytest.importorskip("torch")
    # Not `delenv`: on an unset variable it records nothing, and the "1"
    # that `check_parity` sets then leaks into later ONNX sessions.
    monkeypatch.setenv("SPLICEAI_ONNX_INTRA_OP_THREADS", "4")
    mtb = _load_script(monkeypatch)
    threads_at_session: list[str | None] = []
    widths: list[int] = []

    def onnx_session() -> None:
        threads_at_session.append(
            mtb.os.environ.get("SPLICEAI_ONNX_INTRA_OP_THREADS"))

    def onnx_predict(_session: Any, x: np.ndarray) -> np.ndarray:
        widths.append(x.shape[1])
        return np.zeros((x.shape[0], x.shape[1] - 10000, 3), np.float32)

    monkeypatch.setattr(mtb, "onnx_session", onnx_session)
    monkeypatch.setattr(mtb, "onnx_predict", onnx_predict)
    monkeypatch.setattr(mtb, "build_torch_port", lambda _path: None)
    monkeypatch.setattr(
        mtb, "torch_predict", lambda _port, x: onnx_predict(None, x))

    exit_code = mtb.main([
        "--check-parity", "--torch-threads", str(torch.get_num_threads())])

    assert exit_code == 0
    assert threads_at_session == ["1"]
    assert set(widths) == {10301, 20201}


@pytest.mark.parametrize("flag", ["--rows", "--reps"])
def test_rows_and_reps_must_be_positive(
    monkeypatch: pytest.MonkeyPatch, flag: str,
) -> None:
    mtb = _load_script(monkeypatch)

    with pytest.raises(SystemExit) as error:
        mtb.main([flag, "0"])

    assert error.value.code == 2


def test_chunks_split_a_batch_into_rows_within_the_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mtb = _load_script(monkeypatch)
    x = np.zeros((8, 10301, 4), dtype=np.int8)

    rows = [chunk.shape[0] for chunk in mtb.chunks(x, 65536)]

    assert rows == [6, 2]


def test_synthetic_input_is_the_window_of_the_backend_tests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mtb = _load_script(monkeypatch)

    actual = mtb.synthetic_input(3, 10301)

    np.testing.assert_array_equal(actual, a_one_hot_window(10301, batch=3))


def test_installed_size_counts_only_the_packages_the_group_adds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("torch")
    mtb = _load_script(monkeypatch)

    sizes = mtb.installed_size("torch-spike")

    # `torch` requires `typing-extensions`, and the runtime dependencies
    # already reach it, so the group does not add it.
    assert sizes["torch"] > 0
    assert "typing-extensions" not in sizes


def _locked_packages() -> list[dict[str, Any]]:
    with UV_LOCK.open("rb") as stream:
        packages: list[dict[str, Any]] = tomllib.load(stream)["package"]
    return packages


def test_lock_holds_no_nvidia_package() -> None:
    names = [package["name"] for package in _locked_packages()]

    assert "torch" in names
    assert [name for name in names if name.startswith("nvidia")] == []


def test_lock_takes_only_torch_from_the_pytorch_cpu_index() -> None:
    from_index = {
        package["name"] for package in _locked_packages()
        if package.get("source", {}).get("registry") == PYTORCH_CPU_INDEX}

    assert from_index == {"torch"}


# The GPU mode (#2471). The tests below fake the GPU and the three runtimes:
# they need no CUDA device and no `torch`, so they run in CI too.

DEFAULT_LADDER = [1, 2, 4, 8, 16, 32, 64]
FAKE_GPU = "Fake RTX 9999"


class FakeOutOfMemory(RuntimeError):
    """The out-of-memory error of the fake `torch.cuda`."""


class FakeResourceExhausted(Exception):
    """The out-of-memory error of the fake `tf.errors`."""


class FakeTensor:
    """A tensor of the fake `torch`: an array and a device."""

    def __init__(self, array: np.ndarray, device: str = "cpu") -> None:
        self.array = array
        self.shape = array.shape
        self.device = SimpleNamespace(type=device)

    def to(self, device: str) -> "FakeTensor":
        return FakeTensor(self.array, device)

    def cpu(self) -> "FakeTensor":
        return FakeTensor(self.array, "cpu")

    def numpy(self) -> np.ndarray:
        return self.array


def _output_of(x: Any) -> np.ndarray:
    return np.zeros((x.shape[0], x.shape[1] - 10000, 3), np.float32)


@dataclass
class FakeGpu:
    """The fake runtimes of one test, and what each one saw.

    ``calls`` holds the rows of each call of each runtime, ``batch_sizes``
    the ``batch_size`` of each TensorFlow ``predict()``, and ``providers``
    the ``providers`` list of each ONNX session.
    """

    oom_from_rows: dict[str, int] = field(default_factory=dict)
    failing_check: str | None = None
    calls: dict[str, list[int]] = field(
        default_factory=lambda: {"torch": [], "onnx": [], "tensorflow": []})
    batch_sizes: list[int] = field(default_factory=list)
    providers: list[list[str]] = field(default_factory=list)
    provider_options: list[list[dict[str, str]]] = field(
        default_factory=list)
    tensorflow_tf32: list[bool] = field(default_factory=list)
    torch_module: Any = None

    def run(self, runtime: str, x: Any) -> np.ndarray:
        self.calls[runtime].append(x.shape[0])
        if x.shape[0] >= self.oom_from_rows.get(runtime, 10**9):
            raise {
                "torch": FakeOutOfMemory("CUDA out of memory"),
                "onnx": RuntimeError(
                    "[ONNXRuntimeError] : 6 : RUNTIME_EXCEPTION : "
                    "Failed to allocate memory for requested buffer"),
                "tensorflow": FakeResourceExhausted("OOM when allocating"),
            }[runtime]
        return _output_of(x)

    def torch(self) -> SimpleNamespace:
        self.torch_module = SimpleNamespace(
            __version__="2.9.0+cu126",
            cuda=SimpleNamespace(
                is_available=lambda: self.failing_check != "cuda_available",
                get_device_name=lambda _index=0: FAKE_GPU,
                OutOfMemoryError=FakeOutOfMemory),
            version=SimpleNamespace(cuda="12.6"),
            backends=SimpleNamespace(
                cudnn=SimpleNamespace(version=lambda: 91000, allow_tf32=True),
                cuda=SimpleNamespace(
                    matmul=SimpleNamespace(allow_tf32=True))),
            inference_mode=contextlib.nullcontext,
            from_numpy=FakeTensor,
            set_num_threads=lambda _threads: None,
            get_num_threads=lambda: 4,
        )
        return self.torch_module

    def port(self) -> Any:
        fake = self

        class FakePort:
            """The port: it records the rows and keeps the device."""

            def to(self, _device: str) -> "FakePort":
                return self

            def __call__(self, x: FakeTensor) -> FakeTensor:
                device = "cpu" if fake.failing_check == "torch" \
                    else x.device.type
                return FakeTensor(fake.run("torch", x), device)

        return FakePort()

    def session(self, _path: str, sess_options: Any = None,
                providers: list[str] | None = None,
                provider_options: list[dict[str, str]] | None = None) -> Any:
        assert sess_options is not None
        self.providers.append(list(providers or []))
        self.provider_options.append(list(provider_options or []))
        fake = self

        class FakeSession:
            """An ONNX session: it records the rows of each run."""

            def get_providers(self) -> list[str]:
                if fake.failing_check == "onnx":
                    return ["CPUExecutionProvider"]
                return list(providers or [])

            def get_inputs(self) -> list[SimpleNamespace]:
                return [SimpleNamespace(name="input_1")]

            def run(self, _names: Any, feed: dict[str, Any]) -> list[Any]:
                return [fake.run("onnx", feed["input_1"])]

        return FakeSession()

    def tensorflow(self) -> SimpleNamespace:
        fake = self

        class FakeModel:
            """A Keras model: it records the ``batch_size`` of each call."""

            def predict(self, x: Any, batch_size: int, verbose: int) -> Any:
                assert verbose == 0
                fake.batch_sizes.append(batch_size)
                return fake.run("tensorflow", x)

        gpus = [] if self.failing_check == "tensorflow" else ["GPU:0"]
        return SimpleNamespace(
            __version__="2.21.0",
            config=SimpleNamespace(
                list_physical_devices=lambda _kind: gpus,
                experimental=SimpleNamespace(
                    enable_tensor_float_32_execution=(
                        lambda enabled: self.tensorflow_tf32.append(enabled)),
                    get_device_details=lambda _device: {
                        "device_name": FAKE_GPU})),
            sysconfig=SimpleNamespace(get_build_info=lambda: {
                "cuda_version": "12.5.1", "cudnn_version": "9"}),
            errors=SimpleNamespace(
                ResourceExhaustedError=FakeResourceExhausted),
            keras=SimpleNamespace(models=SimpleNamespace(
                load_model=lambda _path: FakeModel())),
        )

    def install(self, monkeypatch: pytest.MonkeyPatch, mtb: ModuleType) -> None:
        """Put the fakes in place of the GPU and the three runtimes.

        The children run in this process, so that they see the fakes.
        """
        monkeypatch.setitem(sys.modules, "torch", self.torch())
        monkeypatch.setitem(sys.modules, "tensorflow", self.tensorflow())
        monkeypatch.setattr(onnxruntime, "InferenceSession", self.session)
        monkeypatch.setattr(mtb, "build_torch_port", lambda _path: self.port())
        monkeypatch.setattr(
            mtb, "run_in_child", lambda function, *args: function(*args))
        monkeypatch.setattr(mtb, "gpu_facts", lambda: {
            "name": FAKE_GPU, "memory": "11264 MiB",
            "driver_version": "580.95"})


def _gpu_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, fake: FakeGpu,
    *flags: str,
) -> tuple[int, dict[str, Any]]:
    mtb = _load_script(monkeypatch)
    fake.install(monkeypatch, mtb)
    out = tmp_path / "gpu.json"

    exit_code = mtb.main(
        ["--device", "cuda", "--widths", "10301", "--out", str(out), *flags])

    payload = json.loads(out.read_text()) if out.exists() else {}
    return exit_code, payload


def test_gpu_run_times_each_runtime_over_the_default_batch_ladder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path,
) -> None:
    exit_code, payload = _gpu_run(monkeypatch, tmp_path, FakeGpu())

    assert exit_code == 0
    assert {
        (r["runtime"], r["width"]): list(r["ms_per_window"])
        for r in payload["results"]
    } == {
        (runtime, 10301): [str(batch) for batch in DEFAULT_LADDER]
        for runtime in ("torch", "onnx", "tensorflow")
    }


def test_batches_takes_a_ladder_of_batch_sizes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path,
) -> None:
    _, payload = _gpu_run(
        monkeypatch, tmp_path, FakeGpu(), "--batches", "3", "5")

    assert payload["batches"] == [3, 5]
    assert {tuple(r["ms_per_window"]) for r in payload["results"]} == {
        ("3", "5")}


def test_the_ladder_runs_sorted_and_without_repeats(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path,
) -> None:
    fake = FakeGpu(oom_from_rows={"onnx": 64})

    _, payload = _gpu_run(
        monkeypatch, tmp_path, fake, "--batches", "64", "1", "4", "4")

    assert payload["batches"] == [1, 4, 64]
    ladder = {r["runtime"]: r["ms_per_window"] for r in payload["results"]}
    assert list(ladder["onnx"]) == ["1", "4", "64"]
    assert ladder["onnx"]["64"] == "oom"


@pytest.mark.parametrize(("flags", "message"), [
    (["--device", "cuda", "--rows", "8"], "--rows applies only to"),
    (["--device", "cpu", "--batches", "8"], "--batches applies only to"),
    (["--batches", "8"], "--batches applies only to"),
])
def test_a_size_flag_of_the_other_device_is_an_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    flags: list[str], message: str,
) -> None:
    mtb = _load_script(monkeypatch)

    with pytest.raises(SystemExit) as error:
        mtb.main(flags)

    assert error.value.code == 2
    assert message in capsys.readouterr().err


def test_each_gpu_call_gets_the_whole_batch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path,
) -> None:
    fake = FakeGpu()

    _gpu_run(monkeypatch, tmp_path, fake, "--batches", "64", "--reps", "1")

    # The PyTorch device check runs one row first.
    assert fake.calls == {
        "torch": [1, 64, 64], "onnx": [64, 64], "tensorflow": [64, 64]}
    assert fake.batch_sizes == [64, 64]


def test_each_batch_size_gets_one_warm_up_and_five_timed_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path,
) -> None:
    fake = FakeGpu()

    _, payload = _gpu_run(monkeypatch, tmp_path, fake, "--batches", "2", "3")

    assert fake.calls["onnx"] == [2] * 6 + [3] * 6
    assert fake.calls["tensorflow"] == [2] * 6 + [3] * 6
    assert fake.calls["torch"] == [1] + [2] * 6 + [3] * 6
    assert payload["reps"] == 5


class FakeClock:
    """A `time.perf_counter` that only the fake runtimes advance.

    The n-th call of a runtime over ``rows`` windows takes
    ``call_ms[n]`` ms.
    """

    def __init__(self, fake: FakeGpu, call_ms: list[float]) -> None:
        self.now_ms = 0.0
        self.call_ms = call_ms
        self.fake = fake
        self.fake_run = fake.run

    def run(self, runtime: str, x: Any) -> np.ndarray:
        output = self.fake_run(runtime, x)
        nth = self.fake.calls[runtime].count(x.shape[0]) - 1
        self.now_ms += self.call_ms[nth]
        return output

    def perf_counter(self) -> float:
        return self.now_ms / 1000.0


@pytest.mark.parametrize("runtime", ["torch", "onnx", "tensorflow"])
def test_the_result_is_the_median_of_the_timed_calls_per_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, runtime: str,
) -> None:
    # The warm-up takes 1000 ms. The median of the timed calls is 20 ms,
    # and their mean is 21 ms. Batch 2 makes 20 ms a 10 ms window. A run
    # that times the warm-up and drops the last call gets 30 ms instead.
    fake = FakeGpu()
    clock = FakeClock(fake, [1000.0, 10.0, 20.0, 30.0, 40.0, 5.0])
    monkeypatch.setattr(fake, "run", clock.run)
    mtb = _load_script(monkeypatch)
    fake.install(monkeypatch, mtb)
    monkeypatch.setattr(
        mtb, "time", SimpleNamespace(perf_counter=clock.perf_counter))
    out = tmp_path / "gpu.json"

    mtb.main(["--device", "cuda", "--widths", "10301", "--batches", "2",
              "--out", str(out)])

    results = {r["runtime"]: r for r in json.loads(out.read_text())["results"]}
    assert results[runtime]["ms_per_window"] == {"2": 10.0}


def test_the_gpu_timing_session_takes_only_the_cuda_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path,
) -> None:
    fake = FakeGpu()

    _gpu_run(monkeypatch, tmp_path, fake, "--batches", "1")

    assert fake.providers == [["CUDAExecutionProvider"]]


def test_the_cpu_timing_session_takes_only_the_cpu_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mtb = _load_script(monkeypatch)
    fake = FakeGpu()
    fake.install(monkeypatch, mtb)

    predict, _ = mtb.load_runtime("onnx", 10301, 4)
    predict(np.zeros((1, 10301, 4), np.float32))

    assert fake.providers == [["CPUExecutionProvider"]]
    assert fake.calls["onnx"] == [1]


def test_every_gpu_runtime_turns_off_tf32(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path,
) -> None:
    fake = FakeGpu()

    _gpu_run(monkeypatch, tmp_path, fake, "--batches", "1")

    backends = fake.torch_module.backends
    assert backends.cudnn.allow_tf32 is False
    assert backends.cuda.matmul.allow_tf32 is False
    assert fake.provider_options == [[{"use_tf32": "0"}]]
    assert fake.tensorflow_tf32 == [False]


def test_gpu_parity_turns_off_tf32_in_the_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SPLICEAI_ONNX_INTRA_OP_THREADS", "4")
    mtb = _load_script(monkeypatch)
    fake = FakeGpu()
    fake.install(monkeypatch, mtb)

    mtb.main(["--check-parity", "--device", "cuda", "--widths", "10001"])

    backends = fake.torch_module.backends
    assert backends.cudnn.allow_tf32 is False
    assert backends.cuda.matmul.allow_tf32 is False
    assert fake.provider_options == [[{}]]


@pytest.mark.parametrize("device", ["cpu", "cuda"])
def test_the_parity_reference_session_takes_only_the_cpu_provider(
    monkeypatch: pytest.MonkeyPatch, device: str,
) -> None:
    monkeypatch.setenv("SPLICEAI_ONNX_INTRA_OP_THREADS", "4")
    mtb = _load_script(monkeypatch)
    fake = FakeGpu()
    fake.install(monkeypatch, mtb)
    monkeypatch.setattr(mtb, "torch_predict", lambda _port, x: _output_of(x))

    exit_code = mtb.main(
        ["--check-parity", "--device", device, "--widths", "10001"])

    assert exit_code == 0
    assert fake.providers == [["CPUExecutionProvider"]]


@pytest.mark.parametrize(
    "failing_check", ["cuda_available", "torch", "onnx", "tensorflow"])
def test_a_failed_device_check_gives_no_timing_and_a_failed_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path,
    failing_check: str,
) -> None:
    runtime = "torch" if failing_check == "cuda_available" else failing_check
    fake = FakeGpu(failing_check=failing_check)

    exit_code, payload = _gpu_run(
        monkeypatch, tmp_path, fake, "--batches", "1", "2")

    results = {r["runtime"]: r for r in payload["results"]}
    assert exit_code == 1
    assert "ms_per_window" not in results[runtime]
    assert results[runtime]["error"].startswith("device check failed")
    assert len(fake.calls[runtime]) <= 1
    others = set(results) - {runtime}
    assert all(
        list(results[other]["ms_per_window"]) == ["1", "2"]
        for other in others)


@pytest.mark.parametrize("runtime", ["torch", "onnx", "tensorflow"])
def test_out_of_gpu_memory_gives_oom_and_ends_the_ladder_at_that_width(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, runtime: str,
) -> None:
    fake = FakeGpu(oom_from_rows={runtime: 8})

    exit_code, payload = _gpu_run(
        monkeypatch, tmp_path, fake, "--widths", "10301", "20201",
        "--batches", "2", "4", "8", "16")

    assert exit_code == 0
    by_key = {(r["runtime"], r["width"]): r["ms_per_window"]
              for r in payload["results"]}
    for width in (10301, 20201):
        ladder = by_key[runtime, width]
        assert list(ladder) == ["2", "4", "8"]
        assert ladder["8"] == "oom"
        assert all(isinstance(ladder[b], float) for b in ("2", "4"))
    assert 16 not in fake.calls[runtime]


@pytest.mark.parametrize(("runtime", "error"), [
    ("torch", RuntimeError("cuDNN error: CUDNN_STATUS_EXECUTION_FAILED")),
    ("onnx", RuntimeError(
        "[ONNXRuntimeError] : 6 : RUNTIME_EXCEPTION : CUDNN failure 5000")),
    ("tensorflow", RuntimeError("Failed to call ThenRnnForward")),
])
def test_an_error_other_than_out_of_memory_stops_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path,
    runtime: str, error: RuntimeError,
) -> None:
    # Each error is a RuntimeError without the out-of-memory marker of its
    # runtime, as the real errors of a broken GPU call are.
    fake = FakeGpu()
    fake_run = fake.run

    def run(called: str, x: Any) -> np.ndarray:
        if called == runtime and x.shape[0] == 2:
            raise error
        return fake_run(called, x)

    monkeypatch.setattr(fake, "run", run)

    with pytest.raises(RuntimeError, match=re.escape(str(error))):
        _gpu_run(monkeypatch, tmp_path, fake, "--batches", "2")


@pytest.mark.parametrize(("offset", "expected_exit"), [
    (2e-4, 1), (1e-4, 0), (5e-6, 0)])
def test_gpu_parity_fails_only_above_the_gpu_tolerance(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    offset: float, expected_exit: int,
) -> None:
    monkeypatch.setenv("SPLICEAI_ONNX_INTRA_OP_THREADS", "4")
    mtb = _load_script(monkeypatch)
    FakeGpu().install(monkeypatch, mtb)
    monkeypatch.setattr(
        mtb, "onnx_predict", lambda _session, x: _output_of(x))
    monkeypatch.setattr(
        mtb, "torch_cuda_predict",
        lambda _port, x: _output_of(x) + np.float64(offset))

    exit_code = mtb.main(["--check-parity", "--device", "cuda"])

    assert exit_code == expected_exit
    match = re.search(
        r"largest absolute difference: (\S+)", capsys.readouterr().out)
    assert match is not None
    assert float(match.group(1)) == pytest.approx(offset, rel=1e-3)


def test_gpu_parity_fails_without_a_cuda_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SPLICEAI_ONNX_INTRA_OP_THREADS", "4")
    mtb = _load_script(monkeypatch)
    FakeGpu(failing_check="cuda_available").install(monkeypatch, mtb)

    assert mtb.main(["--check-parity", "--device", "cuda"]) == 1


def test_the_gpu_json_holds_the_gpu_the_versions_and_the_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path,
) -> None:
    fake = FakeGpu(oom_from_rows={"tensorflow": 2})

    _, payload = _gpu_run(monkeypatch, tmp_path, fake, "--batches", "1", "2")

    assert payload["device"] == "cuda"
    assert payload["gpu"] == {
        "name": FAKE_GPU, "memory": "11264 MiB", "driver_version": "580.95"}
    assert payload["runtimes"] == {
        "torch": {"runtime_version": "2.9.0+cu126", "cuda_version": "12.6",
                  "cudnn_version": 91000, "device_name": FAKE_GPU},
        "onnx": {"runtime_version": onnxruntime.__version__,
                 "cuda_version": "unknown", "cudnn_version": "unknown",
                 "device_name": FAKE_GPU},
        "tensorflow": {"runtime_version": "2.21.0", "cuda_version": "12.5.1",
                       "cudnn_version": "9", "device_name": FAKE_GPU},
    }
    assert payload["host"]
    assert len(payload["loadavg_before"]) == 3
    assert len(payload["loadavg"]) == 3
    results = {r["runtime"]: r["ms_per_window"] for r in payload["results"]}
    assert results["tensorflow"] == {"1": pytest.approx(
        results["tensorflow"]["1"]), "2": "oom"}
    assert all(isinstance(results[r]["2"], float) for r in ("torch", "onnx"))


@pytest.mark.parametrize(("stdout", "expected"), [
    ("NVIDIA GeForce RTX 2080 Ti, 11264 MiB, 580.95\n",
     {"name": "NVIDIA GeForce RTX 2080 Ti", "memory": "11264 MiB",
      "driver_version": "580.95"}),
    ("", dict.fromkeys(("name", "memory", "driver_version"), "unknown")),
])
def test_gpu_facts_reads_the_name_the_memory_and_the_driver_of_nvidia_smi(
    monkeypatch: pytest.MonkeyPatch, stdout: str, expected: dict[str, str],
) -> None:
    mtb = _load_script(monkeypatch)
    monkeypatch.setattr(
        mtb.shutil, "which", lambda _name: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(
        mtb.subprocess, "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=stdout))

    assert mtb.gpu_facts() == expected


def test_gpu_facts_is_unknown_without_nvidia_smi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mtb = _load_script(monkeypatch)
    monkeypatch.setattr(mtb.shutil, "which", lambda _name: None)

    assert mtb.gpu_facts() == dict.fromkeys(
        ("name", "memory", "driver_version"), "unknown")


def test_the_default_device_times_on_cpu_as_before(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path,
) -> None:
    mtb = _load_script(monkeypatch)
    calls: list[tuple[Any, ...]] = []

    def run_in_child(function: Any, *args: Any) -> dict[str, Any]:
        calls.append((function.__name__, *args))
        runtime, width, rows, reps, threads = args
        return {"runtime": runtime, "width": width, "rows": rows,
                "ms_per_window": 1.0, "threads": threads,
                "torch_import_s": 0.5, "peak_rss_after_load_mb": 1.0,
                "peak_rss_mb": 2.0, "reps": reps}

    monkeypatch.setattr(mtb, "run_in_child", run_in_child)
    monkeypatch.setattr(mtb, "installed_size", lambda _group: {"torch": 1.0})
    out = tmp_path / "cpu.json"

    exit_code = mtb.main(["--out", str(out)])

    assert exit_code == 0
    assert calls == [
        ("measure_runtime", runtime, width, 500, 1, 4)
        for width in (10301, 20201)
        for runtime in ("torch", "onnx", "tensorflow")]
    payload = json.loads(out.read_text())
    assert payload["rows"] == 500
    assert payload["position_budget"] == 65536


def test_the_gpu_run_prints_a_row_per_runtime_and_width(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeGpu(oom_from_rows={"onnx": 2}, failing_check="tensorflow")

    _gpu_run(monkeypatch, tmp_path, fake, "--batches", "1", "2")

    out = capsys.readouterr().out
    assert "| runtime | width | 1 | 2 |" in out
    assert re.search(r"\| torch \| 10301 \| [0-9.]+ \| [0-9.]+ \|", out)
    assert re.search(r"\| onnx \| 10301 \| [0-9.]+ \| oom \|", out)
    assert "| tensorflow | 10301 | device check failed" in out
