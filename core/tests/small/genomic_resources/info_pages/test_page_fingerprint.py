# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Unit tests of the page fingerprint (gain#2451).

``page_fingerprint(repo)`` digests what the info pages of a resource are
rendered from. A repair compares it with the page fingerprint stored in
``index.html`` to decide whether the pages render again.
"""
import pathlib
import shutil
from collections.abc import Callable
from importlib import metadata
from typing import Any

import gain
import gain.templates
import pytest
from gain.genomic_resources import cli_info
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.implementations.data_frame_resource_impl import (
    DataFrameResourceImplementation,
)
from gain.genomic_resources.page_fingerprint import (
    PAGE_FINGERPRINT_READ_SIZE,
    insert_page_fingerprint,
    read_page_fingerprint,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.resource_implementation import (
    GenomicResourceImplementation,
)
from gain.genomic_resources.testing import (
    build_filesystem_test_protocol,
    build_filesystem_test_repository,
)
from gain.genomic_resources.testing.builders import a_grr
from gain.genomic_resources.testing.data_frame_builder import a_data_frame
from gain.task_graph.graph import TaskDesc


@pytest.fixture
def data_frame_repo(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    return a_grr().with_resource("df", a_data_frame()).build_repo(tmp_path)


def _implementation(
    repo: GenomicResourceRepo,
) -> DataFrameResourceImplementation:
    return DataFrameResourceImplementation(repo.get_resource("df"))


def test_a_new_page_template_changes_the_fingerprint(
    data_frame_repo: GenomicResourceRepo,
    provided_template: Callable[[str], None],
) -> None:
    provided_template("first source")
    before = _implementation(data_frame_repo).page_fingerprint(
        data_frame_repo)
    provided_template("second source")

    after = _implementation(data_frame_repo).page_fingerprint(
        data_frame_repo)

    assert before is not None
    assert after != before


def test_a_new_gain_version_leaves_the_fingerprint_unchanged(
    data_frame_repo: GenomicResourceRepo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _implementation(data_frame_repo).page_fingerprint(
        data_frame_repo)
    real_version = metadata.version

    def version(name: str) -> str:
        if name == "gain-core":
            return "9999.0.0"
        return real_version(name)

    monkeypatch.setattr(metadata, "version", version)
    monkeypatch.setattr(gain, "__version__", "9999.0.0")

    after = _implementation(data_frame_repo).page_fingerprint(
        data_frame_repo)

    assert before is not None
    assert after == before


def test_a_new_static_file_changes_the_fingerprint(
    tmp_path: pathlib.Path,
    data_frame_repo: GenomicResourceRepo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    static = tmp_path / "static"
    shutil.copytree(
        str(gain.templates._PAGE_INPUT_DIRS["static"]), static)
    monkeypatch.setitem(gain.templates._PAGE_INPUT_DIRS, "static", static)
    gain.templates.reset_caches()
    before = _implementation(data_frame_repo).page_fingerprint(
        data_frame_repo)
    font = static / "fonts" / "roboto-v51-latin.woff2"
    font.write_bytes(font.read_bytes() + b"\0")
    gain.templates.reset_caches()

    after = _implementation(data_frame_repo).page_fingerprint(
        data_frame_repo)

    gain.templates.reset_caches()
    assert before is not None
    assert after != before


def test_a_new_manifest_changes_the_fingerprint(
    tmp_path: pathlib.Path,
    data_frame_repo: GenomicResourceRepo,
) -> None:
    before_impl = _implementation(data_frame_repo)
    before = before_impl.page_fingerprint(data_frame_repo)
    (tmp_path / "df" / "notes.txt").write_text("a file no input reads\n")
    cli_manage(["resource-manifest", "-R", str(tmp_path), "-r", "df"])
    repo = build_filesystem_test_repository(tmp_path)

    after_impl = _implementation(repo)
    after = after_impl.page_fingerprint(repo)

    assert after_impl.calc_statistics_hash() == \
        before_impl.calc_statistics_hash()
    assert after != before


def test_a_new_statistics_hash_changes_the_fingerprint(
    data_frame_repo: GenomicResourceRepo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    implementation = _implementation(data_frame_repo)
    before = implementation.page_fingerprint(data_frame_repo)
    monkeypatch.setattr(
        implementation, "calc_statistics_hash", lambda: b"other inputs")

    after = implementation.page_fingerprint(data_frame_repo)

    assert after != before


class _OutsideKind(GenomicResourceImplementation):
    """A kind defined outside the ``gain`` package, as gpf defines kinds."""

    info_digest = b"first"

    def calc_info_hash(self) -> bytes:
        return self.info_digest

    def calc_statistics_hash(self) -> bytes:
        return b""

    def create_statistics_build_tasks(
        self, *,
        region_size: int = 0,
        grr: GenomicResourceRepo | None = None,
    ) -> list[TaskDesc]:
        return []

    def get_info(self, **kwargs: Any) -> str:
        return ""

    def get_statistics_info(self, **kwargs: Any) -> str:
        return ""


def test_a_kind_outside_gain_gets_a_fingerprint_from_its_info_hash(
    data_frame_repo: GenomicResourceRepo,
) -> None:
    implementation = _OutsideKind(data_frame_repo.get_resource("df"))
    before = implementation.page_fingerprint(data_frame_repo)
    implementation.info_digest = b"second"

    after = implementation.page_fingerprint(data_frame_repo)

    assert before is not None
    assert after is not None
    assert after != before


@pytest.mark.parametrize("render", ["get_info", "get_statistics_info"])
def test_the_tag_in_a_real_page_reads_back_from_its_head(
    data_frame_repo: GenomicResourceRepo,
    render: str,
) -> None:
    page = getattr(_implementation(data_frame_repo), render)()

    tagged = insert_page_fingerprint(page, "0123abcd")

    assert read_page_fingerprint(tagged[:PAGE_FINGERPRINT_READ_SIZE]) == \
        "0123abcd"
    assert read_page_fingerprint(page) is None


def test_a_tag_past_the_read_size_does_not_read_back() -> None:
    tagged = insert_page_fingerprint(
        '<meta charset="utf-8">', "0123abcd")

    assert read_page_fingerprint(tagged) == "0123abcd"
    assert read_page_fingerprint(
        "x" * PAGE_FINGERPRINT_READ_SIZE + tagged) is None


def test_the_stored_fingerprint_reads_only_the_head_of_the_page(
    tmp_path: pathlib.Path,
    data_frame_repo: GenomicResourceRepo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = "x" * (2 * PAGE_FINGERPRINT_READ_SIZE)
    (tmp_path / "df" / "index.html").write_text(page)
    (tmp_path / "df" / "statistics").mkdir(exist_ok=True)
    (tmp_path / "df" / "statistics" / "index.html").write_text(page)
    proto = build_filesystem_test_protocol(tmp_path)
    heads: list[str] = []

    def recording_reader(head: str) -> str | None:
        heads.append(head)
        return read_page_fingerprint(head)

    monkeypatch.setattr(cli_info, "read_page_fingerprint", recording_reader)

    cli_info._stored_page_fingerprint(proto, proto.get_resource("df"))

    assert [len(head) for head in heads] == [PAGE_FINGERPRINT_READ_SIZE]
