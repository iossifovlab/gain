# pylint: disable=C0114,C0116
from web_annotation.loadtest import ws_notification_slo as ws


def test_percentile_basic() -> None:
    values = [1.0, 2.0, 3.0, 4.0]
    assert ws._percentile(sorted(values), 50) == 2.5
    assert ws._percentile(sorted(values), 100) == 4.0


def test_summary_splits_empty() -> None:
    summary = ws._summary([])
    assert summary["count"] == 0
    assert summary["p95_ms"] is None


def test_summary_reports_percentiles() -> None:
    summary = ws._summary([10.0, 20.0, 30.0, 40.0])
    assert summary["count"] == 4
    assert summary["p50_ms"] == 25.0
    assert summary["max_ms"] == 40.0
