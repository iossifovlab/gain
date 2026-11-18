# pylint: disable=C0114,C0116
"""The #300 benchmark of the fused ONNX graph against the five sessions."""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
from types import ModuleType

from spliceai_annotator.spliceai_annotator import SpliceAIAnnotator
from tests.test_spliceai_backends import MAX_WIDTH

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
    default_window = 10000 + 2 * SpliceAIAnnotator.DEFAULT_DISTANCE + 1

    assert (default_window, MAX_WIDTH) == script.DEFAULT_WIDTHS
