# pylint: disable=W0621,C0114,C0116,W0212,W0613
import argparse
import logging
import os
import pathlib
import textwrap
from typing import Any

import pytest
from gain.genomic_resources import cli
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.fsspec_protocol import FsspecReadWriteProtocol
from gain.genomic_resources.implementations.genomic_scores_impl import (
    GenomicScoreImplementation,
)
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GR_CONTENTS_FILE_NAME,
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
)


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
    assert (path / GR_CONTENTS_FILE_NAME).exists()


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
    real = GenomicScoreImplementation._do_histogram

    def patched(
        resource: Any, *args: Any, **kwargs: Any,
    ) -> Any:
        if resource.resource_id == resource_id:
            raise ValueError("histogram task boom")
        return real(resource, *args, **kwargs)

    return staticmethod(patched)


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
    # The histogram task enters through ``_do_histogram_task`` (which routes
    # eligible resources to the bulk scan); that is the seam a failing task
    # must be injected at.
    monkeypatch.setattr(
        GenomicScoreImplementation, "_do_histogram_task",
        _histogram_that_raises("one"))

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
        GenomicScoreImplementation, "_do_histogram_task",
        _histogram_that_raises("one"))

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


def test_the_fts_index_blames_the_resource_it_failed_on_not_the_selected_one(
    proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The FTS index is repository-wide, so it walks resources the user did
    # NOT select.  Those failures are real and must fail the run, but they
    # are reported under their own id.
    path, _proto = proto_fixture
    real = ScoreImplementationBase.collect_index_info

    def boom(self: Any) -> Any:
        if self.resource.resource_id == "two":
            raise ValueError("cannot index this")
        return real(self)

    monkeypatch.setattr(ScoreImplementationBase, "collect_index_info", boom)

    with caplog.at_level(logging.INFO, logger="grr_manage"), \
            pytest.raises(SystemExit) as excinfo:
        cli_manage([
            "resource-repair", "-R", str(path), "-r", "one", "-j", "1"])

    assert excinfo.value.code != 0
    failures = [
        record.getMessage() for record in caplog.records
        if record.levelno == logging.ERROR
    ]
    assert any("<two>" in message for message in failures)
    assert not any("<one>" in message for message in failures)
    # The selected resource was still repaired.
    assert (path / "one" / "index.html").is_file()


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
    # Dispatch is by suffix; a name added to _REPO_COMMANDS but not handled
    # here used to land on the unconditional `return _run_repo_repair...`
    # tail and silently run a full repair.
    path, proto = proto_fixture

    def must_not_run(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("repair must not run for an unknown command")

    monkeypatch.setattr(cli, "_run_repo_repair_command", must_not_run)

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
