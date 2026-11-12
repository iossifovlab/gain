"""A remote tabix or VCF open reads the index of its own resource.

htslib, handed an index url, downloads the index into the current working
directory under its basename and reuses a file it finds there. Two remote
resources whose data files share a name then share one index, and the second
open reads its data through the index of the first (gain#2283).

Each test here opens remote resources from an empty working directory, so
any file the open leaves behind shows up in it.
"""
# pylint: disable=C0116  # each test name states its behaviour
import contextlib
import os
import pathlib
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from typing import Any

import pysam
import pytest
import pytest_mock
from gain.genomic_resources.fsspec_protocol import build_fsspec_protocol
from gain.genomic_resources.repository import ReadOnlyRepositoryProtocol
from gain.genomic_resources.testing import (
    build_filesystem_test_protocol,
    build_http_test_protocol,
    build_s3_test_protocol,
)
from gain.genomic_resources.testing.builders import a_grr
from gain.genomic_resources.testing.table_score_builder import (
    PositionScoreBuilder,
    a_position_score,
)
from gain.genomic_resources.testing.vcf_info_score_builder import (
    VcfInfoScoreBuilder,
    a_vcf_info_score,
)

from .conftest import serving_http


@contextlib.contextmanager
def _serving_http_out_of_process(serve_dir: pathlib.Path) -> Iterator[str]:
    """Serve ``serve_dir`` over localhost http from a child process.

    ``serving_http`` serves from a thread of this process, and
    ``pysam.VariantFile`` holds the GIL across the reads of its open: the
    server thread then cannot answer the second request, and the open
    hangs. A VCF open needs a server that does not share the GIL.
    """
    with subprocess.Popen(
        [sys.executable, "-u", "-m", "http.server",
         "--bind", "127.0.0.1", "--directory", str(serve_dir), "0"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    ) as server:
        try:
            assert server.stdout is not None
            banner = server.stdout.readline()
            port = re.search(r"port (\d+)", banner)
            assert port is not None, banner
            yield f"http://127.0.0.1:{port.group(1)}"
        finally:
            server.terminate()


def _a_tabix_score(
    *rows: tuple[str, int, int, float],
) -> PositionScoreBuilder:
    data = "chrom  pos_begin  pos_end  s1\n" + "".join(
        f"{chrom}  {begin}  {end}  {value}\n"
        for chrom, begin, end, value in rows)
    return (
        a_position_score()
        .with_score("s1", "float")
        .with_data(data)
        .with_tabix()
    )


def _a_vcf_score(*rows: tuple[str, int, float]) -> VcfInfoScoreBuilder:
    # No ``##contig`` lines: the contigs of the open come from its index.
    data = (
        "##fileformat=VCFv4.2\n"
        '##INFO=<ID=SC,Number=1,Type=Float,Description="score">\n'
        "#CHROM POS ID REF ALT QUAL FILTER INFO\n"
        + "".join(
            f"{chrom} {pos} . A T . . SC={value}\n"
            for chrom, pos, value in rows))
    return a_vcf_info_score().with_data(data)


#: A one-row resource of each indexed kind, its data file, the opener of
#: the protocol and the pysam class that opener constructs.
_ONE_ROW_OPENS = pytest.mark.parametrize(
    ("builder", "filename", "opener", "pysam_class"),
    [(_a_tabix_score(("1", 1, 10, 1.5)), "data.txt.gz",
      "open_tabix_file", "TabixFile"),
     (_a_vcf_score(("1", 5, 1.5)), "data.vcf.gz",
      "open_vcf_file", "VariantFile")],
    ids=["tabix", "vcf"])


@pytest.fixture
def workdir(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> pathlib.Path:
    """An empty working directory, entered for the test."""
    path = tmp_path / "workdir"
    path.mkdir()
    monkeypatch.chdir(path)
    return path


def test_second_tabix_open_reads_its_own_index(
    tmp_path: pathlib.Path, workdir: pathlib.Path,
) -> None:
    root = tmp_path / "grr"
    (
        a_grr()
        .with_resource("first", _a_tabix_score(("1", 1, 10, 1.5)))
        .with_resource("second", _a_tabix_score(
            ("2", 1, 10, 2.5), ("3", 1, 10, 3.5)))
        .build_repo(root)
    )

    with serving_http(root) as base_url:
        proto = build_fsspec_protocol("i2283-tabix-two", base_url)
        first = proto.get_resource("first")
        second = proto.get_resource("second")

        with proto.open_tabix_file(first, "data.txt.gz") as tabix:
            first_contigs = list(tabix.contigs)
        with proto.open_tabix_file(second, "data.txt.gz") as tabix:
            second_contigs = list(tabix.contigs)
            second_rows = [tuple(row) for row in tabix.fetch("3")]

    assert first_contigs == ["1"]
    assert second_contigs == ["2", "3"]
    assert second_rows == [("3", "1", "10", "3.5")]
    assert list(workdir.iterdir()) == []


def test_second_vcf_open_reads_its_own_index(
    tmp_path: pathlib.Path, workdir: pathlib.Path,
) -> None:
    root = tmp_path / "grr"
    (
        a_grr()
        .with_resource("first", _a_vcf_score(("1", 5, 1.5)))
        .with_resource("second", _a_vcf_score(
            ("2", 5, 2.5), ("3", 7, 3.5)))
        .build_repo(root)
    )

    with _serving_http_out_of_process(root) as base_url:
        proto = build_fsspec_protocol("i2283-vcf-two", base_url)
        first = proto.get_resource("first")
        second = proto.get_resource("second")

        with proto.open_vcf_file(first, "data.vcf.gz") as vcf:
            first_contigs = list(vcf.header.contigs)
        with proto.open_vcf_file(second, "data.vcf.gz") as vcf:
            second_contigs = list(vcf.header.contigs)
            second_rows = [
                (rec.chrom, rec.pos, rec.info["SC"])
                for rec in vcf.fetch("3")]

    assert first_contigs == ["1"]
    assert second_contigs == ["2", "3"]
    assert second_rows == [("3", 7, 3.5)]
    assert list(workdir.iterdir()) == []


@_ONE_ROW_OPENS
def test_remote_open_removes_its_temporary_index(
    builder: PositionScoreBuilder | VcfInfoScoreBuilder, filename: str,
    opener: str, pysam_class: str,
    tmp_path: pathlib.Path, workdir: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "grr"
    a_grr().with_resource("res", builder).build_repo(root)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    made: list[pathlib.Path] = []
    real_mkdtemp = tempfile.mkdtemp

    def recording_mkdtemp(*args: Any, **kwargs: Any) -> str:
        path = str(real_mkdtemp(*args, **kwargs))
        made.append(pathlib.Path(path))
        return path

    monkeypatch.setattr(tempfile, "mkdtemp", recording_mkdtemp)

    with _serving_http_out_of_process(root) as base_url:
        proto = build_fsspec_protocol(f"i2283-tmp-{filename}", base_url)
        resource = proto.get_resource("res")
        with getattr(proto, opener)(resource, filename) as opened:
            assert opened is not None
            assert list(scratch.iterdir()) == []

    assert made
    assert all(path.parent == scratch for path in made)
    assert list(workdir.iterdir()) == []


@contextlib.contextmanager
def _a_protocol_over(
    scheme: str, root: pathlib.Path, mocker: pytest_mock.MockerFixture,
) -> Iterator[ReadOnlyRepositoryProtocol]:
    if scheme == "file":
        yield build_filesystem_test_protocol(root)
        return
    if scheme == "http":
        with build_http_test_protocol(root) as proto:
            yield proto
        return
    if scheme == "s3":
        mocker.patch.dict(os.environ, {
            "AWS_SECRET_ACCESS_KEY": "minioadmin",
            "AWS_ACCESS_KEY_ID": "minioadmin",
        })
        with build_s3_test_protocol(root) as proto:
            yield proto
        return
    raise ValueError(f"unexpected protocol scheme: <{scheme}>")


@pytest.mark.grr_tabix
def test_tabix_open_leaves_the_working_directory_empty(
    grr_scheme: str, tmp_path: pathlib.Path, workdir: pathlib.Path,
    mocker: pytest_mock.MockerFixture,
) -> None:
    root = tmp_path / "grr"
    (
        a_grr()
        .with_resource("res", _a_tabix_score(
            ("1", 1, 10, 1.5), ("2", 1, 10, 2.5)))
        .build_repo(root)
    )

    with _a_protocol_over(grr_scheme, root, mocker) as proto:
        resource = proto.get_resource("res")
        with proto.open_tabix_file(resource, "data.txt.gz") as tabix:
            rows = [tuple(row) for row in tabix.fetch("2")]

    assert rows == [("2", "1", "10", "2.5")]
    assert list(workdir.iterdir()) == []


@_ONE_ROW_OPENS
def test_remote_open_leaves_the_process_cwd_alone(
    builder: PositionScoreBuilder | VcfInfoScoreBuilder, filename: str,
    opener: str, pysam_class: str,
    tmp_path: pathlib.Path, workdir: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # gainweb opens resources from a thread pool of one process, so an open
    # must not contain the htslib download with a chdir: two concurrent
    # opens would race on the one working directory of the process.
    root = tmp_path / "grr"
    a_grr().with_resource("res", builder).build_repo(root)
    chdirs: list[str] = []
    real_chdir = os.chdir

    def recording_chdir(path: Any) -> None:
        chdirs.append(str(path))
        real_chdir(path)

    monkeypatch.setattr(os, "chdir", recording_chdir)
    cwds_at_open: list[str] = []
    real_class = getattr(pysam, pysam_class)

    def recording_class(*args: Any, **kwargs: Any) -> Any:
        cwds_at_open.append(os.getcwd())
        return real_class(*args, **kwargs)

    monkeypatch.setattr(pysam, pysam_class, recording_class)

    with _serving_http_out_of_process(root) as base_url:
        proto = build_fsspec_protocol(f"i2283-cwd-{filename}", base_url)
        resource = proto.get_resource("res")
        with getattr(proto, opener)(resource, filename) as opened:
            assert opened is not None

    assert chdirs == []
    assert cwds_at_open == [str(workdir)]
