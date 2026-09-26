# pylint: disable=W0621,C0114,C0116,W0212,W0613
import argparse
import gzip
import logging
import os
import pathlib
import re
import textwrap
from typing import Any

import pytest
from gain.genomic_resources import cli
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.fsspec_protocol import FsspecReadWriteProtocol
from gain.genomic_resources.implementations.genomic_scores_impl import (
    GenomicScoreImplementation,
    scan,
)
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GR_CONTENTS_FILE_NAME,
    GR_LEGACY_CONTENTS_FILE_NAME,
    GR_MANIFEST_FILE_NAME,
)
from gain.genomic_resources.score_implementation import ScoreImplementationBase
from gain.genomic_resources.testing import (
    build_filesystem_test_protocol,
    setup_directories,
    setup_tabix,
)
from gain.genomic_resources.testing.builders import (
    a_grr,
    a_position_score,
    a_reference_genome,
)

from .conftest import overlap_warnings


@pytest.fixture
def proto_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[pathlib.Path, FsspecReadWriteProtocol]:
    path = tmp_path_factory.mktemp("cli_repair_proto_fixture")
    setup_directories(path, {
        "one": {
            GR_CONF_FILE_NAME: textwrap.dedent("""
                type: position_score
                table:
                    filename: data.txt.gz
                    format: tabix
                scores:
                    - id: phastCons100way
                      type: float
                      name: s1
                      histogram:
                        type: number
                        number_of_bins: 100
                """),
        },
        "two": {
            GR_CONF_FILE_NAME: textwrap.dedent("""
                type: position_score
                table:
                    filename: data.txt.gz
                    format: tabix
                    zero_based: true
                scores:
                    - id: phastCons100way
                      type: float
                      name: s1
                      histogram:
                        type: number
                        number_of_bins: 100
                """),
        },
    })
    setup_tabix(
        path / "one" / "data.txt.gz",
        """
        #chrom  pos_begin  pos_end  s1    s2
        1       10         15       0.02  1.02
        1       17         19       0.03  1.03
        1       22         25       0.04  1.04
        2       5          80       0.01  2.01
        2       81         90       0.02  2.02
        """, seq_col=0, start_col=1, end_col=2)
    setup_tabix(
        path / "two" / "data.txt.gz",
        """
        #chrom  pos_begin  pos_end  s1    s2
        1       10         15       0.02  1.02
        1       17         19       0.03  1.03
        1       22         25       0.04  1.04
        2       5          80       0.01  2.01
        2       81         90       0.02  2.02
        """, seq_col=0, start_col=1, end_col=2)
    proto = build_filesystem_test_protocol(path)
    return path, proto


def test_resource_repair_simple(
    proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
) -> None:
    # Given
    path, proto = proto_fixture
    proto.filesystem.delete(
        os.path.join(proto.url, GR_CONTENTS_FILE_NAME))
    assert not (path / "one/statistics").exists()
    assert not (path / GR_CONTENTS_FILE_NAME).exists()

    # When
    cli_manage([
        "resource-repair", "-R", str(path), "-r", "one", "-j", "1",
    ])

    # Then
    assert (path / "one/statistics").exists()
    assert (path / "one" / GR_MANIFEST_FILE_NAME).exists()
    # The repository index is repo-scoped; a resource-scoped command
    # leaves it to `repo-index` (gain#760).
    assert not (path / GR_CONTENTS_FILE_NAME).exists()


def test_repo_repair_simple(
    proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
) -> None:
    # Given
    path, proto = proto_fixture
    proto.filesystem.delete(
        os.path.join(proto.url, GR_CONTENTS_FILE_NAME))
    assert not (path / "one/statistics").exists()
    assert not (path / "two/statistics").exists()
    assert not (path / GR_CONTENTS_FILE_NAME).exists()

    # When
    cli_manage([
        "repo-repair", "-R", str(path), "-j", "1"])

    # Then
    assert (path / "one/statistics").exists()
    assert (path / "one" / GR_MANIFEST_FILE_NAME).exists()
    assert (path / "two/statistics").exists()
    assert (path / "two" / GR_MANIFEST_FILE_NAME).exists()
    assert (path / GR_CONTENTS_FILE_NAME).exists()


def test_resource_repair_dry_run(
    proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
) -> None:
    # Given
    path, proto = proto_fixture
    proto.filesystem.delete(
        os.path.join(proto.url, GR_CONTENTS_FILE_NAME))

    proto.filesystem.delete(str(path / "one" / GR_MANIFEST_FILE_NAME))

    assert not (path / "one/statistics").exists()
    assert not (path / GR_CONTENTS_FILE_NAME).exists()

    # When
    with pytest.raises(SystemExit):
        cli_manage([
            "resource-repair", "--dry-run",
            "-R", str(path), "-r", "one",
            "-j", "1",
        ])

    # Then
    assert not (path / "one/statistics").exists()
    assert not (path / "one" / GR_MANIFEST_FILE_NAME).exists()


def test_repo_repair_dry_run(
    proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
) -> None:
    # Given
    path, proto = proto_fixture
    proto.filesystem.delete(
        os.path.join(proto.url, GR_CONTENTS_FILE_NAME))
    assert not (path / "one/statistics").exists()
    assert not (path / GR_CONTENTS_FILE_NAME).exists()

    # When
    with pytest.raises(SystemExit):
        cli_manage([
            "repo-repair", "--dry-run", "-R", str(path), "-j", "1",
        ])

    # Then
    assert not (path / "one/statistics").exists()
    assert not (path / GR_CONTENTS_FILE_NAME).exists()


@pytest.fixture
def broken_and_healthy_grr(
    tmp_path_factory: pytest.TempPathFactory,
) -> pathlib.Path:
    """A GRR of two position scores, one of which cannot open its table.

    The broken one is the gain#364 shape: a headerless tabix table whose
    config forgets ``header_mode: none``, so the backend looks for a header
    the file does not have.
    """
    path = tmp_path_factory.mktemp("cli_repair_broken_grr")
    (
        a_grr()
        .with_resource(
            "healthy",
            a_position_score()
            .with_score("phastCons", "float")
            .with_histogram({"type": "number", "number_of_bins": 10})
            .with_tabix()
            .with_data("""
                chrom  pos_begin  pos_end  phastCons
                1      10         15       0.02
                1      17         19       0.03
            """))
        .with_resource(
            "broken",
            a_position_score()
            .with_score("phastCons", "float", column_index=3)
            .with_histogram({"type": "number", "number_of_bins": 10})
            .with_tabix()
            .with_missing_header_mode()
            .with_data("""
                chrom  pos_begin  pos_end  phastCons
                1      10         15       0.02
                1      17         19       0.03
            """))
        .build_repo(path)
    )
    return path


def test_repo_repair_reports_a_broken_resource(
    broken_and_healthy_grr: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # gain#364: repair used to skip the broken resource, blame the wrong
    # phase, drop the exception, log "GRR is consistent" and exit 0.
    path = broken_and_healthy_grr

    with caplog.at_level(logging.INFO, logger="grr_manage"), \
            pytest.raises(SystemExit) as excinfo:
        cli_manage(["repo-repair", "-R", str(path), "-j", "1"])

    assert excinfo.value.code != 0
    assert "broken" in caplog.text
    assert "header_mode: none" in caplog.text
    # The summary must name the broken resource AS a failure; how it words
    # the count is not what this test is about.
    assert any(
        record.levelno == logging.ERROR
        and "failed" in record.getMessage()
        and "broken" in record.getMessage()
        for record in caplog.records)
    assert "is consistent" not in caplog.text
    # The cause is reported on one line; the traceback is demoted to DEBUG,
    # so nothing at default verbosity carries one.
    assert [
        record.name for record in caplog.records
        if record.exc_info is not None
    ] == []


def test_repo_repair_repairs_the_healthy_resource_beside_the_broken_one(
    broken_and_healthy_grr: pathlib.Path,
) -> None:
    # One broken resource must not stop the rest of the repository from
    # being repaired -- failures are collected, not raised.
    path = broken_and_healthy_grr

    with pytest.raises(SystemExit):
        cli_manage(["repo-repair", "-R", str(path), "-j", "1"])

    assert (path / "healthy" / "statistics"
            / "histogram_phastCons.json").is_file()
    assert (path / "healthy" / "index.html").is_file()


def test_repo_repair_leaves_the_broken_resource_info_page_alone(
    broken_and_healthy_grr: pathlib.Path,
) -> None:
    # gain#364: the info page of a resource whose statistics could not be
    # built used to be regenerated anyway -- replacing a good page with one
    # rendered from NullHistogram placeholders.
    path = broken_and_healthy_grr
    good_page = "<html>the good page</html>"
    (path / "broken" / "index.html").write_text(good_page)

    with pytest.raises(SystemExit):
        cli_manage(["repo-repair", "-R", str(path), "-j", "1"])

    assert (path / "broken" / "index.html").read_text() == good_page


def test_repo_repair_keeps_the_traceback_of_an_unexpected_error(
    broken_and_healthy_grr: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The second tier: anything that is not a configuration error is a
    # defect in GAIn, so it keeps its traceback at ERROR -- someone has to
    # see it.
    def boom(_resource: object) -> None:
        raise RuntimeError("something GAIn did not expect")

    monkeypatch.setattr(cli, "build_resource_implementation", boom)

    with caplog.at_level(logging.INFO, logger="grr_manage"), \
            pytest.raises(SystemExit) as excinfo:
        cli_manage([
            "repo-repair", "-R", str(broken_and_healthy_grr), "-j", "1"])

    assert excinfo.value.code != 0
    unexpected = [
        record for record in caplog.records
        if "unexpected internal error" in record.getMessage()
    ]
    assert unexpected
    assert all(record.levelno == logging.ERROR for record in unexpected)
    assert all(record.exc_info is not None for record in unexpected)


def test_repo_repair_keeps_the_traceback_recoverable_at_debug(
    broken_and_healthy_grr: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The traceback of a configuration error is DEMOTED, not dropped: `-vv`
    # has to be able to get it back, or the one-line report would be the
    # only thing anyone could ever see (gain#364).
    path = broken_and_healthy_grr

    with caplog.at_level(logging.DEBUG, logger="grr_manage"), \
            pytest.raises(SystemExit):
        cli_manage(["repo-repair", "-R", str(path), "-j", "1"])

    with_traceback = [
        record for record in caplog.records
        if record.levelno == logging.DEBUG and record.exc_info is not None
    ]
    assert with_traceback
    assert any(
        isinstance(record.exc_info[1], ValueError)  # type: ignore[index]
        and "header_mode: none" in str(record.exc_info[1])  # type: ignore
        for record in with_traceback
    )


def test_a_message_less_failure_still_names_its_cause(
    proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # An exception raised with no message -- a bare `raise ValueError()`, or
    # an assert `python -O` stripped -- used to be reported as a line that
    # trailed off after the colon.  The class name is what is left of the
    # cause, so it is carried (gain#364).
    path, _proto = proto_fixture

    def boom(_resource: object) -> None:
        raise ValueError

    monkeypatch.setattr(cli, "build_resource_implementation", boom)

    with caplog.at_level(logging.INFO, logger="grr_manage"), \
            pytest.raises(SystemExit) as excinfo:
        cli_manage(["repo-repair", "-R", str(path), "-j", "1"])

    assert excinfo.value.code != 0
    assert "ValueError" in caplog.text
    assert "is consistent" not in caplog.text


# ---------------------------------------------------------------------------
# gain#364: a statistics task that fails during EXECUTION
# ---------------------------------------------------------------------------

def _histogram_that_raises(resource_id: str) -> Any:
    real = scan.do_histogram

    def patched(
        resource: Any, *args: Any, **kwargs: Any,
    ) -> Any:
        if resource.resource_id == resource_id:
            raise ValueError("histogram task boom")
        return scan.RegionScanResult(
            real(resource, *args, **kwargs),
            coverage=None, fragments=None, alleles=None)

    return patched


def test_repo_repair_reports_a_statistics_task_that_fails_while_running(
    proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # gain#364: collecting the tasks succeeded, so nothing was added to the
    # failure set; the task graph runs with `keep_going=True`, so nothing
    # raised either, and `process_graph`'s return value -- the ONLY report
    # of the failure -- was discarded.  The run said "is consistent" and
    # exited 0 with no statistics built.  Every resource here is HEALTHY
    # bar the one the task explodes on, so nothing else can carry the
    # non-zero status.
    path, _proto = proto_fixture
    # The histogram task enters through ``scan.do_histogram_task``
    # (which routes eligible resources to the bulk scan); that is the
    # seam a failing task must be injected at.
    monkeypatch.setattr(
        scan, "do_histogram_task", _histogram_that_raises("one"))

    with caplog.at_level(logging.INFO, logger="grr_manage"), \
            pytest.raises(SystemExit) as excinfo:
        cli_manage(["repo-repair", "-R", str(path), "-j", "1"])

    assert excinfo.value.code != 0
    assert "is consistent" not in caplog.text
    assert any(
        record.levelno == logging.ERROR and "<one>" in record.getMessage()
        for record in caplog.records)
    # No statistics, so no info page rendered from placeholder histograms.
    assert not (path / "one" / "statistics"
                / "histogram_phastCons100way.json").exists()
    assert not (path / "one" / "index.html").exists()
    # ... and the resource beside it is repaired as usual, not blamed.
    assert (path / "two" / "statistics"
            / "histogram_phastCons100way.json").is_file()
    assert (path / "two" / "index.html").is_file()
    assert not any(
        record.levelno == logging.ERROR and "<two>" in record.getMessage()
        for record in caplog.records)


def test_a_failing_statistics_task_leaves_the_info_page_alone(
    proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, _proto = proto_fixture
    good_page = "<html>the good page</html>"
    (path / "one" / "index.html").write_text(good_page)
    monkeypatch.setattr(
        scan, "do_histogram_task", _histogram_that_raises("one"))

    with pytest.raises(SystemExit):
        cli_manage(["repo-repair", "-R", str(path), "-j", "1"])

    assert (path / "one" / "index.html").read_text() == good_page


# ---------------------------------------------------------------------------
# gain#364: the FTS index and the statistics hash used to log and shrug
# ---------------------------------------------------------------------------

def test_repo_repair_fails_when_the_fts_index_cannot_be_built(
    proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # gain#364: an ERROR line naming a resource, immediately followed by
    # "GRR is consistent" and exit 0.
    path, _proto = proto_fixture

    def boom(_self: object) -> None:
        raise ValueError("cannot index this")

    monkeypatch.setattr(ScoreImplementationBase, "collect_index_info", boom)

    with caplog.at_level(logging.INFO, logger="grr_manage"), \
            pytest.raises(SystemExit) as excinfo:
        cli_manage(["repo-repair", "-R", str(path), "-j", "1"])

    assert excinfo.value.code != 0
    assert "skipping FTS index for" in caplog.text
    assert "cannot index this" in caplog.text
    assert "is consistent" not in caplog.text


def test_repo_repair_fails_when_the_statistics_hash_is_not_stored(
    proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # gain#364, the worse half: with no `stats_hash` written the resource is
    # permanently "needs update", while the run called the GRR consistent
    # and exited 0.
    path, _proto = proto_fixture

    def never_stores(_proto: object, _resource: object) -> bool:
        return False

    monkeypatch.setattr(cli, "_store_stats_hash", never_stores)

    with caplog.at_level(logging.INFO, logger="grr_manage"), \
            pytest.raises(SystemExit) as excinfo:
        cli_manage(["repo-repair", "-R", str(path), "-j", "1"])

    assert excinfo.value.code != 0
    assert "were not built" in caplog.text
    assert "is consistent" not in caplog.text
    assert not (path / "one" / "statistics" / "stats_hash").exists()


@pytest.fixture
def repaired_path(
    proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
) -> pathlib.Path:
    """The fixture repository after one healthy repair: every hash current."""
    path, _proto = proto_fixture
    cli_manage(["repo-repair", "-R", str(path), "-j", "1"])
    assert (path / "one" / "statistics" / "stats_hash").is_file()
    return path


# The run's summary line, naming `one` and nothing else.
_BLAMES_ONLY_ONE = re.compile(
    r"^failed resources in GRR <[^>]*>: one$", re.MULTILINE)


def _error_messages(caplog: pytest.LogCaptureFixture) -> str:
    return "\n".join(
        record.getMessage() for record in caplog.records
        if record.levelno >= logging.ERROR)


def test_a_forced_repair_names_the_resource_whose_task_failed(
    repaired_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Every hash is current going in, so only the forced run's own clearing
    # of it lets the failure be pinned on `one`.
    monkeypatch.setattr(
        scan, "do_histogram_task", _histogram_that_raises("one"))

    with caplog.at_level(logging.INFO, logger="grr_manage"), \
            pytest.raises(SystemExit) as excinfo:
        cli_manage(["repo-repair", "-f", "-R", str(repaired_path), "-j", "1"])

    assert excinfo.value.code != 0
    errors = _error_messages(caplog)
    assert _BLAMES_ONLY_ONE.search(errors)
    assert "statistics of <one> were not built" in errors
    assert "no single resource can be blamed" not in errors
    assert "<two>" not in errors
    assert (repaired_path / "two" / "statistics" / "stats_hash").is_file()


@pytest.mark.parametrize("command", ["resource-repair", "resource-stats"])
def test_a_forced_resource_command_names_the_resource_whose_task_failed(
    repaired_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    command: str,
) -> None:
    monkeypatch.setattr(
        scan, "do_histogram_task", _histogram_that_raises("one"))

    with caplog.at_level(logging.INFO, logger="grr_manage"), \
            pytest.raises(SystemExit) as excinfo:
        cli_manage([
            command, "-f", "-R", str(repaired_path), "-r", "one", "-j", "1"])

    assert excinfo.value.code != 0
    errors = _error_messages(caplog)
    assert _BLAMES_ONLY_ONE.search(errors)
    assert "statistics of <one> were not built" in errors
    assert "no single resource can be blamed" not in errors


def _failed_forced_repair(
    path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as patch:
        patch.setattr(
            scan, "do_histogram_task", _histogram_that_raises("one"))
        with pytest.raises(SystemExit):
            cli_manage(["repo-repair", "-f", "-R", str(path), "-j", "1"])


def test_a_failed_forced_rebuild_is_retried_by_the_next_plain_repair(
    repaired_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _failed_forced_repair(repaired_path, monkeypatch)

    with pytest.raises(SystemExit) as dry_run:
        cli_manage(["repo-repair", "--dry-run", "-R", str(repaired_path)])
    assert dry_run.value.code == 1

    with caplog.at_level(logging.INFO, logger="grr_manage"):
        cli_manage(["repo-repair", "-R", str(repaired_path), "-j", "1"])

    assert "is consistent" in caplog.text
    assert (repaired_path / "one" / "statistics" / "stats_hash").is_file()


def _fingerprint(path: pathlib.Path) -> tuple[bytes, int]:
    """What a rewrite of ``path`` would change: its content and mtime."""
    return path.read_bytes(), path.stat().st_mtime_ns


def test_a_repair_that_rebuilds_nothing_keeps_the_stored_hash(
    repaired_path: pathlib.Path,
) -> None:
    stats_hash = repaired_path / "one" / "statistics" / "stats_hash"
    before = _fingerprint(stats_hash)

    cli_manage(["repo-repair", "-R", str(repaired_path)])

    assert _fingerprint(stats_hash) == before


def test_a_dry_run_keeps_the_stored_hash_of_a_stale_resource(
    repaired_path: pathlib.Path,
) -> None:
    # `one` IS a rebuild candidate here -- its config changed -- so only
    # the dry run itself stands between it and a cleared hash.
    config = repaired_path / "one" / GR_CONF_FILE_NAME
    config.write_text(config.read_text() + "# edited\n")
    stats_hash = repaired_path / "one" / "statistics" / "stats_hash"
    before = _fingerprint(stats_hash)

    with pytest.raises(SystemExit) as excinfo:
        cli_manage(["repo-repair", "--dry-run", "-R", str(repaired_path)])

    assert excinfo.value.code == 1
    assert _fingerprint(stats_hash) == before


def test_a_hash_that_cannot_be_cleared_fails_the_resource_before_its_build(
    repaired_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    real_delete = FsspecReadWriteProtocol.delete_resource_file

    def delete(self: Any, resource: Any, filename: str) -> None:
        if resource.resource_id == "one" and filename.endswith("stats_hash"):
            raise PermissionError("stats_hash is read-only")
        real_delete(self, resource, filename)

    monkeypatch.setattr(FsspecReadWriteProtocol, "delete_resource_file", delete)
    histogram = (
        repaired_path / "one" / "statistics" / "histogram_phastCons100way.json")
    before = _fingerprint(histogram)

    with caplog.at_level(logging.INFO, logger="grr_manage"), \
            pytest.raises(SystemExit):
        cli_manage(["repo-repair", "-f", "-R", str(repaired_path), "-j", "1"])

    assert "<one>" in _error_messages(caplog)
    assert _fingerprint(histogram) == before


def test_a_failed_forced_rebuild_leaves_no_stats_hash_in_the_manifest(
    repaired_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _failed_forced_repair(repaired_path, monkeypatch)

    manifest = (repaired_path / "one" / GR_MANIFEST_FILE_NAME).read_text()
    assert not (repaired_path / "one" / "statistics" / "stats_hash").exists()
    assert "statistics/stats_hash" not in manifest


def test_the_info_page_is_not_written_before_the_statistics_page_renders(
    proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # gain#364: index.html was written first and the statistics page
    # rendered only afterwards, so a failure in the second half left the
    # rewritten page behind while the run reported it had been protected.
    path, _proto = proto_fixture
    good_page = "<html>the good page</html>"
    (path / "one" / "index.html").write_text(good_page)

    def boom(_self: object, **_kwargs: Any) -> str:
        raise ValueError("statistics info boom")

    monkeypatch.setattr(
        GenomicScoreImplementation, "get_statistics_info", boom)

    with pytest.raises(SystemExit):
        cli_manage([
            "resource-repair", "-R", str(path), "-r", "one", "-j", "1"])

    assert (path / "one" / "index.html").read_text() == good_page


# ---------------------------------------------------------------------------
# gain#364: the dispatcher must not fall through into a destructive repair
# ---------------------------------------------------------------------------

def test_an_unrecognised_management_command_does_not_repair(
    proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A name added to _REPO_COMMANDS but not handled in the dispatch used
    # to land on an unconditional repair tail and silently run it.
    path, proto = proto_fixture

    def must_not_run(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("repair must not run for an unknown command")

    monkeypatch.setattr(cli, "_run_repo_info_command", must_not_run)

    with caplog.at_level(logging.INFO, logger="grr_manage"), \
            pytest.raises(SystemExit) as excinfo:
        cli._run_management_command(
            cli._create_grr_repo(
                argparse.Namespace(grr=None), str(path)),
            proto, list(proto.get_all_resources()), str(path),
            command="repo-bogus")

    assert excinfo.value.code == 1
    assert "Unknown command repo-bogus" in caplog.text


# ---------------------------------------------------------------------------
# resource-stats / resource-info: the paths the dispatcher rewrite re-routed
# ---------------------------------------------------------------------------

def test_resource_stats_builds_only_the_selected_resource(
    proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
) -> None:
    path, _proto = proto_fixture

    cli_manage([
        "resource-stats", "-R", str(path), "-r", "one", "-j", "1"])

    assert (path / "one" / "statistics" / "stats_hash").is_file()
    assert (path / "one" / "statistics"
            / "histogram_phastCons100way.json").is_file()
    assert not (path / "two" / "statistics").exists()
    # stats does not render the info pages -- that is what -info adds.
    assert not (path / "one" / "index.html").exists()


def test_resource_info_renders_only_the_selected_resource(
    proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
) -> None:
    path, _proto = proto_fixture

    cli_manage([
        "resource-info", "-R", str(path), "-r", "one", "-j", "1"])

    assert (path / "one" / "index.html").is_file()
    assert (path / "one" / "statistics" / "index.html").is_file()
    assert not (path / "two" / "index.html").exists()


# ---------------------------------------------------------------------------
# gain#587: a resource the scan refuses is reported WITH its reason
# ---------------------------------------------------------------------------

def _healthy_position_score() -> Any:
    return (
        a_position_score()
        .with_score("phastCons", "float")
        .with_histogram({"type": "number", "number_of_bins": 10})
        .with_tabix()
        .with_data("""
            chrom  pos_begin  pos_end  phastCons
            chr1   10         15       0.02
            chr1   17         19       0.03
        """)
    )


@pytest.fixture
def malformed_between_healthy_grr(
    tmp_path_factory: pytest.TempPathFactory,
) -> pathlib.Path:
    """A GRR whose middle resource overlaps its own records.

    Resources are repaired in id order, so ``a_healthy`` is discovered
    before the malformed one and ``z_healthy`` after it: a layout that puts
    the malformed resource last would prove nothing about the resources the
    sweep had yet to reach.
    """
    path = tmp_path_factory.mktemp("cli_repair_malformed_grr")
    (
        a_grr()
        .with_resource("a_healthy", _healthy_position_score())
        .with_resource(
            "m_malformed",
            a_position_score()
            .with_score("phastCons", "float")
            .with_histogram({"type": "number", "number_of_bins": 10})
            .with_tabix()
            .with_data("""
                chrom  pos_begin  pos_end  phastCons
                chr1   10         15       0.02
                chr1   13         19       0.03
            """))
        .with_resource("z_healthy", _healthy_position_score())
        .build_repo(path)
    )
    return path


def test_repo_repair_reports_a_malformed_resource_with_its_reason(
    malformed_between_healthy_grr: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The reason has to travel WITH the id, on one record.  Asserting that
    # some ERROR record mentions the resource is vacuous: the summary line
    # "statistics of <m_malformed> were not built" says that much for any
    # failure whatsoever, including one that reported nothing at all.
    path = malformed_between_healthy_grr

    with caplog.at_level(logging.INFO, logger="grr_manage"), \
            pytest.raises(SystemExit) as excinfo:
        cli_manage(["repo-repair", "-R", str(path), "-j", "1"])

    assert excinfo.value.code != 0
    assert any(
        record.name == "grr_manage"
        and record.levelno == logging.ERROR
        and "<m_malformed>" in record.getMessage()
        and "at most one record per position" in record.getMessage()
        for record in caplog.records)
    # ... and the refusal is attributed as a failure, with no fresh hash.
    assert any(
        record.levelno == logging.ERROR
        and "failed" in record.getMessage()
        and "m_malformed" in record.getMessage()
        for record in caplog.records)
    assert not (path / "m_malformed" / "statistics" / "stats_hash").exists()
    assert "is consistent" not in caplog.text


def test_a_malformed_resource_is_reported_under_region_size_zero(
    malformed_between_healthy_grr: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # ``--region-size 0`` swaps the per-region tasks for a single
    # whole-resource one.  It reads the same records through the same scan,
    # so it must refuse the resource the same way -- a reader who picked the
    # option must not be left with the reason-less summary line alone.
    path = malformed_between_healthy_grr

    with caplog.at_level(logging.INFO, logger="grr_manage"), \
            pytest.raises(SystemExit) as excinfo:
        cli_manage([
            "repo-repair", "-R", str(path), "-j", "1", "--region-size", "0"])

    assert excinfo.value.code != 0
    assert any(
        record.name == "grr_manage"
        and record.levelno == logging.ERROR
        and "<m_malformed>" in record.getMessage()
        and "at most one record per position" in record.getMessage()
        for record in caplog.records)
    assert "unexpected internal error" not in caplog.text


@pytest.mark.parametrize("region_size", ["0", "5", "3000000000"])
def test_the_verdict_on_a_malformed_resource_survives_any_region_size(
    malformed_between_healthy_grr: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    region_size: str,
) -> None:
    # gain#588 moved the position score's rule off the clipped span onto the
    # raw one.  Whether the two offending records land in one region, in two
    # adjacent ones or in a single unsplit contig, and whether the region is
    # served by the vectorized scan or the per-record one, the resource is
    # refused for the same reason.
    #
    # Note which code each parameter reaches: this resource's score is a
    # float, so it is bulk-eligible, and only ``0`` -- which has no bounded
    # region for the vectorized scan to serve -- takes the per-record path
    # this slice changed.  The other two are answered by the vectorized
    # door, through the rule PositionScore is registered under in
    # ``statistics/record_validation.py``.
    # That is the point of the parametrisation (one verdict whichever path
    # answers), not an oversight; the per-record rule is exercised across a
    # split by test_a_region_split_between_two_touching_records_still_
    # refuses_them.
    path = malformed_between_healthy_grr

    with caplog.at_level(logging.INFO, logger="grr_manage"), \
            pytest.raises(SystemExit) as excinfo:
        cli_manage([
            "repo-repair", "-R", str(path), "-j", "1",
            "--region-size", region_size])

    assert excinfo.value.code != 0
    assert any(
        record.name == "grr_manage"
        and record.levelno == logging.ERROR
        and "<m_malformed>" in record.getMessage()
        and "at most one record per position" in record.getMessage()
        for record in caplog.records)
    assert not (path / "m_malformed" / "statistics" / "stats_hash").exists()


def test_a_malformed_resource_is_never_an_unexpected_internal_error(
    malformed_between_healthy_grr: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A resource whose own records break its kind's rule is the RESOURCE's
    # fault; reporting it as a GAIn defect sends the reader to the wrong
    # repository.
    path = malformed_between_healthy_grr

    with caplog.at_level(logging.INFO, logger="grr_manage"), \
            pytest.raises(SystemExit):
        cli_manage(["repo-repair", "-R", str(path), "-j", "1"])

    assert "unexpected internal error" not in caplog.text


def test_repo_repair_repairs_the_healthy_resources_around_a_malformed_one(
    malformed_between_healthy_grr: pathlib.Path,
) -> None:
    path = malformed_between_healthy_grr

    with pytest.raises(SystemExit):
        cli_manage(["repo-repair", "-R", str(path), "-j", "1"])

    for healthy in ("a_healthy", "z_healthy"):
        assert (path / healthy / "statistics"
                / "histogram_phastCons.json").is_file()
        assert (path / healthy / "statistics" / "stats_hash").is_file()
        assert (path / healthy / "index.html").is_file()


# ---------------------------------------------------------------------------
# The repository index is published gzipped only (#758)
# ---------------------------------------------------------------------------


OLDER_RELEASE_INDEX = b"written by an older release"


@pytest.fixture
def healthy_grr(
    tmp_path_factory: pytest.TempPathFactory,
) -> pathlib.Path:
    """A GRR of one healthy position score -- enough to publish an index."""
    path = tmp_path_factory.mktemp("cli_repair_contents_grr")
    a_grr().with_resource("one", _healthy_position_score()).build_repo(path)
    return path


@pytest.fixture
def grr_with_an_abandoned_index(healthy_grr: pathlib.Path) -> pathlib.Path:
    """The same GRR, carrying an index an older release left behind."""
    (healthy_grr / GR_LEGACY_CONTENTS_FILE_NAME).write_bytes(
        OLDER_RELEASE_INDEX)
    return healthy_grr


def test_a_publish_writes_only_the_compressed_repository_index(
    healthy_grr: pathlib.Path,
) -> None:
    """The uncompressed twin of the index is not written at all.

    It held a second copy of the same JSON, and it grows without bound on
    large GRRs -- 2.1MB against the 298KB of the gzipped one on the
    production `grr` (#758).
    """
    cli_manage(["repo-repair", "-R", str(healthy_grr), "-j", "1"])

    # Paired on purpose: the presence assertion is what stops the absence
    # one from passing vacuously on a repository that published no index
    # at all.
    assert (healthy_grr / GR_CONTENTS_FILE_NAME).is_file()
    assert not (healthy_grr / GR_LEGACY_CONTENTS_FILE_NAME).exists()


def test_a_publish_leaves_an_abandoned_index_alone(
    grr_with_an_abandoned_index: pathlib.Path,
) -> None:
    """One an older release wrote is abandoned, not deleted.

    In the GRRs that carry one it is a tracked file, so deleting it would
    have the publish author a deletion commit in someone else's git tree
    as a side effect (#758).
    """
    path = grr_with_an_abandoned_index

    cli_manage(["repo-repair", "-R", str(path), "-j", "1"])

    assert (path / GR_LEGACY_CONTENTS_FILE_NAME).read_bytes() \
        == OLDER_RELEASE_INDEX


def test_a_publish_reports_an_abandoned_index(
    grr_with_an_abandoned_index: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Abandoning it quietly would leave a trap; the publish names it.

    From this release on it answers with whatever the repository looked
    like the last time an older release wrote it, and nothing refreshes
    it -- so the run says where it is, once (#758).
    """
    path = grr_with_an_abandoned_index

    with caplog.at_level(
            logging.WARNING,
            logger="gain.genomic_resources.fsspec_protocol"):
        cli_manage(["repo-repair", "-R", str(path), "-j", "1"])

    # Matched on the full path, not the bare name: `.CONTENTS.json` is a
    # substring of `.CONTENTS.json.gz`, so a message naming only the file
    # that IS maintained would otherwise satisfy this.
    reported = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
        and str(path / GR_LEGACY_CONTENTS_FILE_NAME) in record.getMessage()
    ]

    # Once -- the index is a repository-wide artefact, so a per-resource
    # nag would scale the warning with the size of the GRR.
    assert len(reported) == 1, reported
    assert "stale" in reported[0]


def test_a_repository_with_only_a_legacy_index_is_still_readable(
    healthy_grr: pathlib.Path,
) -> None:
    """Not writing the uncompressed index is not refusing to read one.

    Every GRR an older release published carries one and nothing has
    replaced it, so the fallback in ``load_contents`` is what keeps those
    repositories readable at all. Pinned here rather than left to ride on
    the format of a checked-in fixture, which a regeneration would
    silently change (#758).
    """
    path = healthy_grr
    cli_manage(["repo-repair", "-R", str(path), "-j", "1"])
    published = (path / GR_CONTENTS_FILE_NAME).read_bytes()

    # Given a repository as an older release left it: uncompressed only
    (path / GR_LEGACY_CONTENTS_FILE_NAME).write_bytes(
        gzip.decompress(published))
    (path / GR_CONTENTS_FILE_NAME).unlink()

    contents = build_filesystem_test_protocol(path).load_contents()

    assert [entry["id"] for entry in contents] == ["one"]


# -- a labelled score against the genome its label names (gain#1575) ------


def _a_genome_listing(*chroms: str) -> Any:
    genome = a_reference_genome()
    for chrom in chroms:
        genome = genome.with_chromosome(chrom, "A" * 100)
    return genome


def test_repo_repair_fails_a_score_whose_genome_lists_none_of_its_contigs(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Zero overlap between a score and its labelled genome -- the shape
    a forgotten ``chrom_mapping`` leaves -- is reported the way any
    failed resource is, one line naming the genome and the usual cause,
    while the rest of the repository still repairs."""
    (
        a_grr()
        .with_resource("genome", _a_genome_listing("chr1"))
        .with_resource(
            "mislabelled",
            _healthy_position_score()
            .with_labels(reference_genome="genome")
            .with_data("""
                chrom  pos_begin  pos_end  phastCons
                1      10         15       0.02
            """))
        .with_resource("healthy", _healthy_position_score())
        .build_repo(tmp_path)
    )

    with caplog.at_level(logging.INFO, logger="grr_manage"), \
            pytest.raises(SystemExit) as excinfo:
        cli_manage(["repo-repair", "-R", str(tmp_path), "-j", "1"])

    assert excinfo.value.code != 0
    failures = [
        record.getMessage() for record in caplog.records
        if record.levelno == logging.ERROR
        and "skipping statistics for <mislabelled>" in record.getMessage()
    ]
    assert len(failures) == 1
    assert "reference_genome genome" in failures[0]
    assert "chrom_mapping" in failures[0]
    assert (tmp_path / "healthy" / "statistics"
            / "histogram_phastCons.json").is_file()
    assert not (tmp_path / "mislabelled" / "statistics"
                / "histogram_phastCons.json").exists()


def test_repo_repair_warns_once_about_the_contigs_a_genome_does_not_list(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Partial overlap is a warning, not a failure: the score repairs,
    and the operator is told once per resource -- not again for every
    page the repair renders -- which contigs the genome does not list."""
    (
        a_grr()
        .with_resource("genome", _a_genome_listing("chr1", "chr2"))
        .with_resource(
            "score",
            _healthy_position_score()
            .with_labels(reference_genome="genome")
            .with_data("""
                chrom    pos_begin  pos_end  phastCons
                chr1     10         15       0.02
                chr2     10         15       0.03
                chrUn_x  10         15       0.04
            """))
        .build_repo(tmp_path)
    )

    with caplog.at_level(logging.WARNING):
        cli_manage(["repo-repair", "-R", str(tmp_path), "-j", "1"])

    assert (tmp_path / "score" / "statistics"
            / "histogram_phastCons.json").is_file()
    warnings = overlap_warnings(caplog)
    assert len(warnings) == 1
    assert "does not list 1 of the score's 3 contigs (chrUn_x)" in warnings[0]
