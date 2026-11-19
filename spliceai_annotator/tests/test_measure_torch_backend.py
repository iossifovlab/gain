# pylint: disable=C0114,C0116
"""The #2425 spike: a PyTorch port of ``spliceai1`` against ONNX and TF.

The tests that run the port skip where ``torch`` is not installed: CI does
not sync the ``torch-spike`` group. The other tests need no ``torch``, so
they run in CI.
"""
import importlib.util
import json
import pathlib
import re
import subprocess
import sys
import tomllib
from types import ModuleType
from typing import Any

import numpy as np
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
