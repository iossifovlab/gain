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
    a_fragment_score,
    a_grr,
    a_position_score,
    an_allele_score,
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
    cli_manage(["resource-repair", "-r", "one", "-R", str(tmp_path), "-j", "1"])
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


def _mtimes(statistics: pathlib.Path) -> dict[pathlib.Path, int]:
    """Every file the statistics BUILD writes, by modification time.

    The statistics page is left out: a repair re-renders every page
    whether or not anything changed, and a render is not a rebuild.
    """
    return {
        path: path.stat().st_mtime_ns for path in statistics.iterdir()
        if path.suffix != ".html"
    }


def test_unforced_repair_reports_the_same_line_and_rebuilds_nothing(
    position_score_at_coverage_v1: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repo = position_score_at_coverage_v1
    statistics = repo / "one" / "statistics"
    before = _mtimes(statistics)

    with caplog.at_level(logging.INFO, logger="grr_manage"):
        cli_manage(["repo-repair", "-R", str(repo), "-j", "1"])

    [line] = [
        line for line in _schema_lines(caplog) if line.startswith("Statistics")
    ]
    assert "statistics/coverage.json 1 -> 2" in line
    # Reported, not acted on: the file is still at version 1, and not one
    # statistics file was rewritten, added or removed
    assert json.loads(
        (statistics / "coverage.json").read_text())["format_version"] == 1
    assert _mtimes(statistics) == before


def test_forced_repair_reports_nothing_and_rebuilds(
    position_score_at_coverage_v1: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repo = position_score_at_coverage_v1
    coverage = repo / "one" / "statistics" / "coverage.json"

    with caplog.at_level(logging.INFO, logger="grr_manage"):
        cli_manage(["repo-repair", "-f", "-R", str(repo), "-j", "1"])

    assert _schema_lines(caplog) == []
    # Forcing IS the remedy: the writer stamped its current version
    assert json.loads(coverage.read_text())["format_version"] == 2


@pytest.fixture
def fragment_without_its_file_beside_a_current_allele_score(
    tmp_path: pathlib.Path,
) -> pathlib.Path:
    """A fragment score with no ``fragments.json`` and an allele score as built."""
    (
        a_grr()
        .with_resource(
            "fragments",
            a_fragment_score()
            .with_tabix()
            .with_score("value", "float")
            .with_histogram({
                "type": "number", "number_of_bins": 4,
                "view_range": {"min": 0.0, "max": 1.0}})
            .with_data("""
                chrom  pos_begin  pos_end  value
                chr1   10         20       0.2
                chr1   15         25       0.4
            """))
        .with_resource(
            "alleles",
            an_allele_score()
            .with_score("freq", "float")
            .with_histogram({
                "type": "number", "number_of_bins": 4,
                "view_range": {"min": 0.0, "max": 1.0}})
            .with_data("""
                chrom  pos_begin  reference  alternative  freq
                chr1   10         A          G            0.1
                chr1   10         A          C            0.2
            """))
        .build_repo(tmp_path)
    )
    cli_manage(["repo-repair", "-R", str(tmp_path), "-j", "1"])
    fragments = tmp_path / "fragments" / "statistics" / "fragments.json"
    assert fragments.exists()
    fragments.unlink()
    _bring_manifest_current(tmp_path, "fragments")
    return tmp_path


def test_a_missing_declared_file_is_reported_and_a_current_one_is_not(
    fragment_without_its_file_beside_a_current_allele_score: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repo = fragment_without_its_file_beside_a_current_allele_score

    with caplog.at_level(logging.INFO, logger="grr_manage"):
        cli_manage(["repo-repair", "--dry-run", "-R", str(repo), "-j", "1"])

    [line] = [
        line for line in _schema_lines(caplog) if line.startswith("Statistics")
    ]
    assert "<fragments>" in line
    assert "statistics/fragments.json missing -> 2" in line
    assert not any("<alleles>" in line for line in _schema_lines(caplog))
