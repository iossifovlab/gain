# pylint: disable=C0114,C0116
"""``drop_stale_histogram_file`` on each on-disk shape of a stale file.

gain#1732: a ``.dvc`` pointer alone (blob not pulled) is still a
DVC-tracked stale file and must be reported, not skipped silently.
"""
import pathlib

import pytest
from gain.genomic_resources.histogram import drop_stale_histogram_file
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing.builders import a_basic_resource

STALE = "statistics/histogram_x.json"
DVC_WARNING = (
    f"stale <{STALE}> of resource <> is DVC-tracked; left in place, "
    f"run 'dvc remove {STALE}.dvc' to drop it")


def a_resource_with(
        tmp_path: pathlib.Path, *files: str) -> GenomicResource:
    builder = a_basic_resource()
    for name in files:
        builder = builder.with_file(name, "{}\n")
    return builder.build_resource(tmp_path)


def files_of(tmp_path: pathlib.Path) -> set[str]:
    """The resource's files, without the protocol's ``.grr`` state."""
    return {
        str(path.relative_to(tmp_path))
        for path in tmp_path.rglob("*")
        if path.is_file() and path.relative_to(tmp_path).parts[0] != ".grr"
    }


def test_a_pointer_without_its_blob_is_reported_and_kept(
        tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture) -> None:
    resource = a_resource_with(tmp_path, f"{STALE}.dvc")
    before = files_of(tmp_path)

    drop_stale_histogram_file(resource, STALE)

    assert DVC_WARNING in caplog.messages
    assert files_of(tmp_path) == before


def test_a_pulled_dvc_tracked_file_is_reported_and_kept(
        tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture) -> None:
    resource = a_resource_with(tmp_path, STALE, f"{STALE}.dvc")
    before = files_of(tmp_path)

    drop_stale_histogram_file(resource, STALE)

    assert DVC_WARNING in caplog.messages
    assert files_of(tmp_path) == before


def test_an_untracked_file_is_deleted_quietly(
        tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture) -> None:
    resource = a_resource_with(tmp_path, STALE)
    before = files_of(tmp_path)

    drop_stale_histogram_file(resource, STALE)

    assert files_of(tmp_path) == before - {STALE}
    assert caplog.messages == []


def test_a_missing_file_is_a_silent_no_op(
        tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture) -> None:
    resource = a_resource_with(tmp_path)
    before = files_of(tmp_path)

    drop_stale_histogram_file(resource, STALE)

    assert files_of(tmp_path) == before
    assert caplog.messages == []
