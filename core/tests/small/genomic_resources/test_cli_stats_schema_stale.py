# pylint: disable=W0621,C0114,C0116
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
import shutil
from collections.abc import Callable

import pytest
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.repository_factory import (
    build_resource_implementation,
)
from gain.genomic_resources.statistics.coverage import (
    COVERAGE_STATISTICS_FILE,
)
from gain.genomic_resources.statistics.fragments import (
    FRAGMENT_STATISTICS_FILE,
)
from gain.genomic_resources.testing.builders import (
    AlleleScoreBuilder,
    FragmentScoreBuilder,
    GRRBuilder,
    PositionScoreBuilder,
    ResourceBuilder,
    a_fragment_score,
    a_grr,
    a_position_score,
    a_reference_genome,
    an_allele_score,
)

from .conftest import captured_warnings


def _a_position_score() -> PositionScoreBuilder:
    return (
        a_position_score()
        .with_score("phastCons", "float")
        .with_histogram({"type": "number", "number_of_bins": 10})
        .with_tabix()
        .with_data("""
            chrom  pos_begin  pos_end  phastCons
            1      10         15       0.02
            1      17         19       0.03
        """))


def _a_fragment_score() -> FragmentScoreBuilder:
    return (
        a_fragment_score()
        .with_score("v", "float")
        .with_tabix()
        .with_data("""
            chrom  pos_begin  pos_end  v
            1      10         30       0.5
            1      12         40       0.7
        """))


def _an_allele_score() -> AlleleScoreBuilder:
    return (
        an_allele_score()
        .with_score("s", "float")
        .with_tabix()
        .with_data("""
            chrom  pos_begin  reference  alternative  s
            1      10         A          G            0.1
        """))


@pytest.mark.parametrize("kind, builder", [
    ("position", _a_position_score),
    ("fragment", _a_fragment_score),
    ("allele", _an_allele_score),
])
def test_each_kind_declares_exactly_the_statistics_files_its_build_writes(
    tmp_path: pathlib.Path,
    kind: str,
    builder: Callable[[], ResourceBuilder],
) -> None:
    # The drift guard: the declaration the check reads and the files the
    # scan writes are stated in two places, and this is what holds them
    # together.  Both directions -- a declared file must be written at
    # the declared version, and every statistics document written must
    # be declared, at a version -- or a new statistic would roll out
    # unreported.  Only the per-score histogram files are exempt: they
    # are addressed by score id, not by kind.
    repo = a_grr().with_resource(kind, builder()).build_repo(tmp_path)
    cli_manage(["repo-repair", "-R", str(tmp_path), "-j", "1"])
    resource = repo.get_resource(kind)
    assert resource is not None
    impl = build_resource_implementation(resource)

    declared = {
        stored.file: stored.format_version
        for stored in impl.stored_statistics()
    }
    written = {
        f"statistics/{statistics_file.name}":
            json.loads(statistics_file.read_text()).get("format_version")
        for statistics_file in (tmp_path / kind / "statistics").glob("*.json")
        if not statistics_file.name.startswith("histogram_")
    }

    assert declared
    assert declared == written


def _repaired(path: pathlib.Path, grr: GRRBuilder) -> pathlib.Path:
    """Realize ``grr`` into ``path`` and build its statistics."""
    grr.build_repo(path)
    cli_manage(["repo-repair", "-R", str(path), "-j", "1"])
    return path


def _refresh_manifest(path: pathlib.Path) -> None:
    """Make a tampered resource consistent again, as an older GAIn left it.

    The manifest lists the statistics files, so after a tamper it is
    refreshed: the resource is then what an upgrade finds -- manifest
    consistent, statistics hash current, stored file behind the schema.
    """
    cli_manage(["repo-manifest", "-R", str(path)])


def _downgrade_format_version(
    path: pathlib.Path, resource_id: str,
) -> None:
    """Rewrite a coverage file as an older GAIn would have left it."""
    statistics_file = path / resource_id / COVERAGE_STATISTICS_FILE
    data = json.loads(statistics_file.read_text())
    data["format_version"] = 1
    statistics_file.write_text(json.dumps(data, indent=2))


@pytest.fixture(scope="module")
def _position_score_at_v1(
    tmp_path_factory: pytest.TempPathFactory,
) -> pathlib.Path:
    path = _repaired(
        tmp_path_factory.mktemp("schema_stale_grr"),
        a_grr().with_resource("pos", _a_position_score()))
    _downgrade_format_version(path, "pos")
    _refresh_manifest(path)
    return path


@pytest.fixture
def position_score_at_v1(
    _position_score_at_v1: pathlib.Path, tmp_path: pathlib.Path,
) -> pathlib.Path:
    """A repaired GRR whose position score's coverage file is at v1.

    A fresh copy per test: several of its consumers mutate it.
    """
    path = tmp_path / "grr"
    shutil.copytree(_position_score_at_v1, path)
    return path


@pytest.fixture(scope="module")
def two_stale_scores_and_a_genome(
    tmp_path_factory: pytest.TempPathFactory,
) -> pathlib.Path:
    """Two position scores at v1 beside a resource of another kind.

    Shared by its consumers: every one of them is a dry run.
    """
    path = _repaired(
        tmp_path_factory.mktemp("schema_stale_two_grr"),
        a_grr()
        .with_resource("pos_a", _a_position_score())
        .with_resource("pos_b", _a_position_score())
        .with_resource(
            "genome", a_reference_genome().with_chromosome("1", "A" * 30)))
    for resource_id in ("pos_a", "pos_b"):
        _downgrade_format_version(path, resource_id)
    _refresh_manifest(path)
    return path


def test_a_resource_of_another_kind_is_never_reported(
    two_stale_scores_and_a_genome: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = two_stale_scores_and_a_genome

    with caplog.at_level(logging.INFO, logger="grr_manage"):
        cli_manage(["repo-repair", "--dry-run", "-R", str(path), "-j", "1"])

    named = {
        resource_id
        for resource_id in ("pos_a", "pos_b", "genome")
        for record in caplog.records
        if "predate the current schema" in record.getMessage()
        and f"<{resource_id}>" in record.getMessage()
    }
    assert named == {"pos_a", "pos_b"}


def test_a_run_ends_with_one_warning_naming_how_many_predate_the_schema(
    two_stale_scores_and_a_genome: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = two_stale_scores_and_a_genome

    with caplog.at_level(logging.INFO, logger="grr_manage"):
        cli_manage(["repo-repair", "--dry-run", "-R", str(path), "-j", "1"])

    summaries = [
        message for message in captured_warnings(caplog)
        if "predate the current schema" in message
    ]
    assert len(summaries) == 1
    assert summaries[0].startswith("2 resource")
    assert "resource-stats" in summaries[0]
    assert "-f" in summaries[0]
    # The summary is the WARNING; the per-resource lines are not.
    assert all(
        record.levelno < logging.WARNING
        for record in caplog.records
        if "statistics of <" in record.getMessage()
        and "predate" in record.getMessage())


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
    tmp_path: pathlib.Path,
) -> pathlib.Path:
    """A repaired GRR: a fragment score whose fragments file was never
    written -- the shape of a resource built before the statistic existed
    -- beside an allele score at the current version."""
    path = _repaired(
        tmp_path,
        a_grr()
        .with_resource("frag", _a_fragment_score())
        .with_resource("alle", _an_allele_score()))
    (path / "frag" / FRAGMENT_STATISTICS_FILE).unlink()
    _refresh_manifest(path)
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
        if record.getMessage().startswith("statistics of <")
        and "predate the current schema" in record.getMessage()
    ]
    assert len(stale_lines) == 1
    assert "<frag>" in stale_lines[0]
    assert f"{FRAGMENT_STATISTICS_FILE} is missing" in stale_lines[0]
    assert "resource-stats -r frag -f" in stale_lines[0]


def test_a_file_whose_version_is_not_a_number_is_reported_not_failed(
    position_score_at_v1: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A report must never be the reason a resource FAILS: an unreadable
    # version counts as predating every schema, and the dry run still
    # exits zero.
    path = position_score_at_v1
    coverage_file = path / "pos" / COVERAGE_STATISTICS_FILE
    data = json.loads(coverage_file.read_text())
    data["format_version"] = None
    coverage_file.write_text(json.dumps(data))
    _refresh_manifest(path)

    with caplog.at_level(logging.INFO, logger="grr_manage"):
        cli_manage(["repo-repair", "--dry-run", "-R", str(path), "-j", "1"])

    assert "is consistent" in caplog.text
    assert "skipping statistics for" not in caplog.text
    assert "statistics of <pos> predate the current schema" in caplog.text


def test_a_resource_whose_hash_is_stale_is_only_reported_as_needing_update(
    position_score_at_v1: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = position_score_at_v1
    (path / "pos" / "statistics" / "stats_hash").unlink()
    _refresh_manifest(path)

    with caplog.at_level(logging.INFO, logger="grr_manage"), \
            pytest.raises(SystemExit) as excinfo:
        cli_manage(["repo-repair", "--dry-run", "-R", str(path), "-j", "1"])

    # A resource that is being rebuilt anyway is not ALSO behind the
    # schema: one finding per resource, and the count is the hash's.
    assert excinfo.value.code == 1
    assert "Statistics of <pos> needs update" in caplog.text
    assert "predate the current schema" not in caplog.text
