# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""The repair flow's schema-stale report (gain#1586).

``calc_statistics_hash`` is an input hash: a release that adds a statistic
or bumps a stored ``format_version`` leaves every built resource's hash
current, so nothing rebuilds and the page reads "not computed" under a
heading the code knows how to fill.  These tests pin the report that makes
that gap visible -- logged only, never acted on, never counted.

Each fixture stands in for a resource built by an older GAIn: its
statistics are built, one file is perturbed, and the manifest is brought
current again, so the statistics hash AND the manifest are current and
the schema is the only thing out of date.
"""
import json
import logging
import pathlib

import pytest
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.testing.builders import (
    PositionScoreBuilder,
    a_grr,
    a_position_score,
)


def _a_position_score() -> PositionScoreBuilder:
    return (
        a_position_score()
        .with_tabix()
        .with_score("value", "float")
        .with_histogram({
            "type": "number", "number_of_bins": 4,
            "view_range": {"min": 0.0, "max": 1.0}})
        .with_data("""
            chrom  pos_begin  pos_end  value
            chr1   10         15       0.2
            chr1   17         19       0.4
        """)
    )


def _rewrite_format_version(path: pathlib.Path, version: int) -> None:
    document = json.loads(path.read_text())
    document["format_version"] = version
    path.write_text(json.dumps(document, indent=2))


def _bring_manifest_current(repo: pathlib.Path, resource_id: str) -> None:
    cli_manage([
        "resource-manifest", "-r", resource_id, "-R", str(repo)])


@pytest.fixture
def position_score_at_coverage_v1(tmp_path: pathlib.Path) -> pathlib.Path:
    """A repo of one position score whose ``coverage.json`` is at version 1."""
    a_grr().with_resource("one", _a_position_score()).build_repo(tmp_path)
    cli_manage(["resource-stats", "-r", "one", "-R", str(tmp_path), "-j", "1"])
    _rewrite_format_version(
        tmp_path / "one" / "statistics" / "coverage.json", 1)
    _bring_manifest_current(tmp_path, "one")
    return tmp_path


def _schema_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage() for record in caplog.records
        if "predate the current schema" in record.getMessage()
    ]


def test_dry_run_reports_a_coverage_file_behind_the_writer(
    position_score_at_coverage_v1: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repo = position_score_at_coverage_v1

    # A dry run with nothing to update exits normally: the schema-stale
    # resource does not join the count the exit status carries
    with caplog.at_level(logging.INFO, logger="grr_manage"):
        cli_manage(["repo-repair", "--dry-run", "-R", str(repo), "-j", "1"])

    [line] = [
        line for line in _schema_lines(caplog) if line.startswith("Statistics")
    ]
    assert "<one>" in line
    assert "statistics/coverage.json" in line
    assert "1 -> 2" in line
    assert "grr_manage resource-stats -r one -f" in line
    assert "Statistics of <one> needs update" not in caplog.text
