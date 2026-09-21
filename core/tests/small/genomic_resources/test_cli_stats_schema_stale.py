# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""The repair flow REPORTS statistics that predate the current schema.

``calc_statistics_hash`` is an input hash and stays one (gain#706, ADR
0020), so a release that adds a statistic or bumps a stored format
leaves every existing resource's hash current and its old file in
place.  gain#1586 (decided on gain#1545): an unforced repair and a
``--dry-run`` say so -- naming the file, the versions and the ``-f``
remedy -- and rebuild nothing.
"""
import json
import logging
import pathlib

import pytest
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.statistics.coverage import (
    COVERAGE_STATISTICS_FILE,
)
from gain.genomic_resources.statistics.fragments import (
    FRAGMENT_STATISTICS_FILE,
)
from gain.genomic_resources.testing.builders import (
    a_fragment_score,
    a_grr,
    a_position_score,
    an_allele_score,
)


def _downgrade_format_version(statistics_file: pathlib.Path) -> None:
    """Rewrite a stored statistic as an older GAIn would have left it."""
    data = json.loads(statistics_file.read_text())
    data["format_version"] = 1
    statistics_file.write_text(json.dumps(data, indent=2))


@pytest.fixture
def position_score_at_v1(
    tmp_path_factory: pytest.TempPathFactory,
) -> pathlib.Path:
    """A repaired GRR whose position score's coverage file is at v1.

    The manifest is refreshed after the downgrade, so the resource is
    what an older GAIn leaves behind: manifest consistent, statistics
    hash current, stored file behind the schema.
    """
    path = tmp_path_factory.mktemp("schema_stale_grr")
    (
        a_grr()
        .with_resource(
            "pos",
            a_position_score()
            .with_score("phastCons", "float")
            .with_histogram({"type": "number", "number_of_bins": 10})
            .with_tabix()
            .with_data("""
                chrom  pos_begin  pos_end  phastCons
                1      10         15       0.02
                1      17         19       0.03
            """))
        .build_repo(path)
    )
    cli_manage(["repo-repair", "-R", str(path), "-j", "1"])
    _downgrade_format_version(path / "pos" / COVERAGE_STATISTICS_FILE)
    cli_manage(["repo-manifest", "-R", str(path)])
    return path


def test_dry_run_reports_a_coverage_file_behind_the_schema_and_exits_zero(
    position_score_at_v1: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = position_score_at_v1

    with caplog.at_level(logging.INFO, logger="grr_manage"):
        # Logged only, never counted: the hash is current, so the dry
        # run's exit status -- the needs-update COUNT (gain#364) -- stays
        # zero, which ``cli_manage`` expresses by returning rather than
        # raising ``SystemExit``.
        cli_manage(["repo-repair", "--dry-run", "-R", str(path), "-j", "1"])

    assert "is consistent" in caplog.text
    stale_lines = [
        record.getMessage() for record in caplog.records
        if "predate" in record.getMessage() and "<pos>" in record.getMessage()
    ]
    assert len(stale_lines) == 1
    line = stale_lines[0]
    assert COVERAGE_STATISTICS_FILE in line
    assert "format version 1" in line
    assert "current is 2" in line
    assert "resource-stats -r pos -f" in line


def test_an_unforced_repair_reports_the_file_and_leaves_it_alone(
    position_score_at_v1: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = position_score_at_v1
    coverage_file = path / "pos" / COVERAGE_STATISTICS_FILE
    before = coverage_file.read_bytes()

    with caplog.at_level(logging.INFO, logger="grr_manage"):
        cli_manage(["repo-repair", "-R", str(path), "-j", "1"])

    assert "is consistent" in caplog.text
    assert "statistics of <pos> predate the current schema" in caplog.text
    # Reported, not rebuilt: the rollout lever stays the forced rebuild.
    assert coverage_file.read_bytes() == before


def test_a_forced_repair_rebuilds_the_file_and_reports_nothing(
    position_score_at_v1: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = position_score_at_v1
    coverage_file = path / "pos" / COVERAGE_STATISTICS_FILE

    with caplog.at_level(logging.INFO, logger="grr_manage"):
        cli_manage(["repo-repair", "--force", "-R", str(path), "-j", "1"])

    assert "is consistent" in caplog.text
    assert "predate the current schema" not in caplog.text
    assert json.loads(coverage_file.read_text())["format_version"] == 2


@pytest.fixture
def fragment_missing_and_allele_current(
    tmp_path_factory: pytest.TempPathFactory,
) -> pathlib.Path:
    """A repaired GRR: a fragment score whose fragments file was never
    written -- the shape of a resource built before the statistic existed
    -- beside an allele score at the current version."""
    path = tmp_path_factory.mktemp("schema_stale_missing_grr")
    (
        a_grr()
        .with_resource(
            "frag",
            a_fragment_score()
            .with_score("v", "float")
            .with_tabix()
            .with_data("""
                chrom  pos_begin  pos_end  v
                1      10         30       0.5
                1      12         40       0.7
            """))
        .with_resource(
            "alle",
            an_allele_score()
            .with_score("s", "float")
            .with_tabix()
            .with_data("""
                chrom  pos_begin  reference  alternative  s
                1      10         A          G            0.1
            """))
        .build_repo(path)
    )
    cli_manage(["repo-repair", "-R", str(path), "-j", "1"])
    (path / "frag" / FRAGMENT_STATISTICS_FILE).unlink()
    cli_manage(["repo-manifest", "-R", str(path)])
    return path


def test_a_missing_file_is_reported_and_a_current_one_is_not(
    fragment_missing_and_allele_current: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = fragment_missing_and_allele_current

    with caplog.at_level(logging.INFO, logger="grr_manage"):
        cli_manage(["repo-repair", "--dry-run", "-R", str(path), "-j", "1"])

    stale_lines = [
        record.getMessage() for record in caplog.records
        if "predate the current schema" in record.getMessage()
    ]
    assert len(stale_lines) == 1
    assert "<frag>" in stale_lines[0]
    assert f"{FRAGMENT_STATISTICS_FILE} is missing" in stale_lines[0]
    assert "resource-stats -r frag -f" in stale_lines[0]
