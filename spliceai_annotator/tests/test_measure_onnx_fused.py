# pylint: disable=C0114,C0116
"""The #300 benchmark of the fused ONNX graph against the five sessions."""
import importlib.util
import json
import os
import pathlib
import queue
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import pytest

SCRIPT = (
    pathlib.Path(__file__).parent.parent / "scripts" / "measure_onnx_fused.py")


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("measure_onnx_fused", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_benchmark_reports_time_and_peak_rss_of_both_paths(
    tmp_path: pathlib.Path,
) -> None:
    out = tmp_path / "result.json"

    completed = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--widths", "10001", "--rows", "2", "--reps", "1",
         "--out", str(out)],
        capture_output=True, text=True, check=True, timeout=300)

    payload = json.loads(out.read_text())
    assert payload["cpu_count"] >= 1
    assert len(payload["loadavg"]) == 3
    results = {r["backend"]: r for r in payload["results"]}
    assert set(results) == {"onnx", "onnx-fused"}
    for result in results.values():
        assert result["width"] == 10001
        assert result["median_ms_per_pass"] > 0
        assert result["peak_rss_mb"] > 0
    # Each configuration runs in its own process: `ru_maxrss` never falls,
    # so a shared process would report the first one's peak for both.
    assert results["onnx"]["pid"] != results["onnx-fused"]["pid"]
    assert "| onnx-fused | 10001 |" in completed.stdout
    assert "load average" in completed.stdout


def test_benchmark_fails_fast_when_a_child_raises(
    tmp_path: pathlib.Path,
) -> None:
    # `0` is refused by the backend when the child loads its sessions, so
    # the child raises before it reports a result.
    env = {**os.environ, "SPLICEAI_ONNX_INTRA_OP_THREADS": "0"}

    completed = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--widths", "10001", "--rows", "2", "--reps", "1",
         "--out", str(tmp_path / "result.json")],
        capture_output=True, text=True, check=False, timeout=60, env=env)

    assert completed.returncode != 0
    assert "onnx @ 10001" in completed.stderr
    assert not (tmp_path / "result.json").exists()


def test_default_widths_are_the_default_and_the_widest_window() -> None:
    script = _load_script()
    # The annotator pads every batch to `10000 + 2 * distance + 1` plus the
    # default `max_insertion_length` of 200 (`SpliceAIAnnotator._batch_width`).
    # The default `distance` is 50: 10000 + 100 + 1 + 200 = 10301.
    # The largest `distance` the annotator accepts is 5000:
    # 10000 + 10000 + 1 + 200 = 20201.
    expected = (10301, 20201)

    assert expected == script.DEFAULT_WIDTHS


class _LateResultQueue:
    """A queue whose result arrives just after the first poll timed out."""

    def __init__(self, result: dict) -> None:
        self._result = result
        self._polls = 0

    def get(self, timeout: float | None = None) -> dict:
        del timeout
        self._polls += 1
        if self._polls == 1:
            raise queue.Empty
        return self._result

    def get_nowait(self) -> dict:
        return self.get()


class _ExitedChild:
    """A child that reported and exited with code 0 during the first poll."""

    exitcode = 0

    def __init__(self, target: object, args: tuple) -> None:
        del target, args

    def start(self) -> None:
        pass

    def is_alive(self) -> bool:
        return False

    def join(self, timeout: float | None = None) -> None:
        del timeout


def test_benchmark_keeps_a_result_that_lands_after_a_poll_timed_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _load_script()
    result = {"backend": "onnx", "width": 10001}
    context = SimpleNamespace(
        Queue=lambda: _LateResultQueue(result), Process=_ExitedChild)
    monkeypatch.setattr(
        script.multiprocessing, "get_context", lambda _method: context)

    reported = script.run_in_child("onnx", 10001, 2, 1)

    assert reported == result
