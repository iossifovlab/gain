# pylint: disable=W0621,C0114,C0116,W0212,W0613
import logging
import os
import pathlib
import textwrap

import pytest
from gain.genomic_resources import cli
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.fsspec_protocol import FsspecReadWriteProtocol
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GR_CONTENTS_FILE_NAME,
    GR_MANIFEST_FILE_NAME,
)
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
    assert "1 resource(s) failed" in caplog.text
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
