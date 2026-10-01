# pylint: disable=C0114,C0116
"""``drop_stale_histogram_file`` on each on-disk shape of a stale file.

gain#1732: a ``.dvc`` pointer alone (blob not pulled) is still a
DVC-tracked stale file and must be reported, not skipped silently.
"""
import pathlib

import pytest
from gain.genomic_resources.histogram import drop_stale_histogram_file
from gain.genomic_resources.testing import build_filesystem_test_resource

STALE = "statistics/histogram_x.json"
DVC_WARNING = (
    f"stale <{STALE}> of resource <> is DVC-tracked; left in place, "
    f"run 'dvc remove {STALE}.dvc' to drop it")


def a_resource_with(tmp_path: pathlib.Path, *files: str) -> None:
    (tmp_path / "genomic_resource.yaml").write_text("type: basic\n")
    for name in files:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n")


def files_of(tmp_path: pathlib.Path) -> set[str]:
    """The resource's files, without the protocol's ``.grr`` state."""
    return {
        str(path.relative_to(tmp_path))
        for path in tmp_path.rglob("*")
        if path.is_file() and path.relative_to(tmp_path).parts[0] != ".grr"
    }


def test_a_pointer_without_its_blob_is_reported_and_kept(
        tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture) -> None:
    a_resource_with(tmp_path, f"{STALE}.dvc")
    resource = build_filesystem_test_resource(tmp_path)
    before = files_of(tmp_path)

    drop_stale_histogram_file(resource, STALE)

    assert DVC_WARNING in caplog.messages
    assert files_of(tmp_path) == before


def test_a_pulled_dvc_tracked_file_is_reported_and_kept(
        tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture) -> None:
    a_resource_with(tmp_path, STALE, f"{STALE}.dvc")
    resource = build_filesystem_test_resource(tmp_path)
    before = files_of(tmp_path)

    drop_stale_histogram_file(resource, STALE)

    assert DVC_WARNING in caplog.messages
    assert files_of(tmp_path) == before


def test_an_untracked_file_is_deleted_quietly(
        tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture) -> None:
    a_resource_with(tmp_path, STALE)
    resource = build_filesystem_test_resource(tmp_path)
    before = files_of(tmp_path)

    drop_stale_histogram_file(resource, STALE)

    assert files_of(tmp_path) == before - {STALE}
    assert caplog.messages == []


def test_a_missing_file_is_a_silent_no_op(
        tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture) -> None:
    a_resource_with(tmp_path)
    resource = build_filesystem_test_resource(tmp_path)
    before = files_of(tmp_path)

    drop_stale_histogram_file(resource, STALE)

    assert files_of(tmp_path) == before
    assert caplog.messages == []
