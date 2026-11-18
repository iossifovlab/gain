# pylint: disable=C0114,C0116,W0212
"""The #300 benchmark of the fused ONNX graph against the five sessions."""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
from types import ModuleType

from spliceai_annotator.spliceai_annotator import SpliceAIAnnotator

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


def _batch_width(distance: int) -> int:
    """The width the annotator pads every batch to before it predicts.

    Read off the annotator's own `_batch_width`, at the default
    `max_insertion_length`, so the test follows a change of the padding.
    """
    annotator = SpliceAIAnnotator.__new__(SpliceAIAnnotator)
    annotator._distance = distance
    annotator._max_insertion_length = (
        SpliceAIAnnotator.DEFAULT_MAX_INSERTION_LENGTH)
    return annotator._batch_width


def test_default_widths_are_the_default_and_the_widest_window() -> None:
    script = _load_script()
    # 5000 is the largest `distance` the annotator accepts.
    expected = (
        _batch_width(SpliceAIAnnotator.DEFAULT_DISTANCE), _batch_width(5000))

    assert expected == script.DEFAULT_WIDTHS
