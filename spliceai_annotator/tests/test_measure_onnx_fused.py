# pylint: disable=C0114,C0116
"""The #300 benchmark of the fused ONNX graph against the five sessions."""
import json
import pathlib
import subprocess
import sys

SCRIPT = (
    pathlib.Path(__file__).parent.parent / "scripts" / "measure_onnx_fused.py")


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
