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
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
    build_resource_implementation,
)
from gain.genomic_resources.testing.builders import (
    PositionScoreBuilder,
    a_fragment_score,
    a_grr,
    a_position_score,
    an_allele_score,
)
from gain.genomic_resources.testing.gene_models_builder import a_gene_models


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
    """A fragment score with no ``fragments.json``, an allele score as built."""
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


def test_a_resource_the_hash_gate_rebuilds_is_not_also_schema_stale(
    position_score_at_coverage_v1: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One resource, one verdict: the hash gate's, when it fires."""
    repo = position_score_at_coverage_v1
    # ... whose hash is gone as well -- and the manifest brought current
    # again, so the hash is the ONE reason the gate fires
    (repo / "one" / "statistics" / "stats_hash").unlink()
    _bring_manifest_current(repo, "one")

    with caplog.at_level(logging.INFO, logger="grr_manage"), \
            pytest.raises(SystemExit) as excinfo:
        cli_manage(["repo-repair", "--dry-run", "-R", str(repo), "-j", "1"])

    assert excinfo.value.code == 1
    assert "Statistics of <one> needs update" in caplog.text
    assert _schema_lines(caplog) == []


@pytest.fixture
def two_stale_scores_and_gene_models(tmp_path: pathlib.Path) -> pathlib.Path:
    """Two position scores at coverage version 1, and a gene models resource."""
    (
        a_grr()
        .with_resource("one", _a_position_score())
        .with_resource("two", _a_position_score())
        .with_resource("genes", a_gene_models())
        .build_repo(tmp_path)
    )
    cli_manage(["repo-repair", "-R", str(tmp_path), "-j", "1"])
    for resource_id in ("one", "two"):
        _rewrite_format_version(
            tmp_path / resource_id / "statistics" / "coverage.json", 1)
        _bring_manifest_current(tmp_path, resource_id)
    return tmp_path


@pytest.mark.parametrize("mode", [["--dry-run"], []])
def test_a_repository_run_ends_with_one_summary_naming_the_count(
    two_stale_scores_and_gene_models: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    mode: list[str],
) -> None:
    repo = two_stale_scores_and_gene_models

    with caplog.at_level(logging.INFO, logger="grr_manage"):
        cli_manage(["repo-repair", *mode, "-R", str(repo), "-j", "1"])

    per_resource = [
        line for line in _schema_lines(caplog) if line.startswith("Statistics")
    ]
    reported = [
        line[len("Statistics of <"):].split(">")[0] for line in per_resource
    ]
    assert reported == ["one", "two"]
    # A gene models resource declares no statistics files: never reported
    assert not any("genes" in line for line in per_resource)
    # ... and ONE summary, at WARNING, after the loop -- where an operator
    # running over a whole repository reads
    [summary] = [
        record for record in caplog.records
        if record.levelno == logging.WARNING
        and "predate the current schema" in record.getMessage()
    ]
    assert "2 resource(s)" in summary.getMessage()
    assert "resource-stats -f" in summary.getMessage()
    assert caplog.records.index(summary) > max(
        caplog.records.index(record) for record in caplog.records
        if record.getMessage() in per_resource)


def _versioned_files_written(resource_dir: pathlib.Path) -> dict[str, int]:
    """Every JSON under ``statistics/`` that stamps a ``format_version``."""
    written = {}
    for path in sorted((resource_dir / "statistics").glob("*.json")):
        document = json.loads(path.read_text())
        if "format_version" in document:
            written[f"statistics/{path.name}"] = document["format_version"]
    return written


def test_each_kind_declares_exactly_the_versioned_files_its_build_writes(
    tmp_path: pathlib.Path,
) -> None:
    """The drift guard: the declaration IS what the build wrote, per kind.

    A statistic added without being declared, a declaration naming a file
    the build does not write for the kind, or a writer whose stamp and
    declared constant part ways all fail here.
    """
    (
        a_grr()
        .with_resource("position", _a_position_score())
        .with_resource(
            "fragment",
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
            "allele",
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
        .with_resource("genes", a_gene_models())
        .build_repo(tmp_path)
    )
    cli_manage(["repo-repair", "-R", str(tmp_path), "-j", "1"])
    repo = build_genomic_resource_repository({
        "id": "drift", "type": "directory", "directory": str(tmp_path)})

    declared = {
        resource_id: {
            file.path: file.format_version
            for file in build_resource_implementation(
                repo.get_resource(resource_id)).statistics_files()
        }
        for resource_id in ("position", "fragment", "allele", "genes")
    }

    assert declared == {
        resource_id: _versioned_files_written(tmp_path / resource_id)
        for resource_id in declared
    }
    # Guards the comparison above against a build that wrote nothing
    # versioned at all, which would make it vacuously true
    assert declared["position"] == {"statistics/coverage.json": 2}
    assert declared["fragment"] == {"statistics/fragments.json": 2}
    assert declared["allele"] == {"statistics/alleles.json": 1}
    assert declared["genes"] == {}
