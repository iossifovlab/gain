# pylint: disable=C0114,C0116
"""The #2425 spike: a PyTorch port of ``spliceai1`` against ONNX and TF.

The tests that run the port skip where ``torch`` is not installed: CI does
not sync the ``torch-spike`` group.
"""
import json
import pathlib
import re
import subprocess
import sys

import pytest

SCRIPT = (
    pathlib.Path(__file__).parent.parent / "scripts"
    / "measure_torch_backend.py")


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
