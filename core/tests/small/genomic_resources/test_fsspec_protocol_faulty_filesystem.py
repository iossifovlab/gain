# pylint: disable=W0621,C0114,C0116,W0212,W0613
import os
import pathlib
from typing import Any

import pytest
from fsspec.exceptions import FSTimeoutError
from gain.genomic_resources.fsspec_protocol import (
    GRR_INTERNAL_DIR,
    ChecksumMismatchError,
    FsspecReadWriteProtocol,
    RetryableCopyError,
    TruncatedDownloadError,
)
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GenomicResource,
)
from gain.genomic_resources.testing import (
    build_faulty_test_protocol,
    build_inmemory_test_protocol,
)
from pytest_mock import MockerFixture

_RESOURCE_ID = "one"
_FILE_NAME = "data.txt"
_FILE_CONTENT = "alabala"

#: The protocol downloads into ``<resource>/.grr/<filename>.<uuid>.part``,
#: so a test can only name that path by pattern -- the uuid is minted per
#: attempt inside the protocol.
_PARTIAL_DOWNLOAD = "*.part"

#: The resource file's state sidecar, written after a successful publish.
_RESOURCE_FILE_STATE = "*.state"

#: The resource file as the source protocol serves it.
_SOURCE_FILE = f"*/{_FILE_NAME}"

#: Short of the manifest's recorded size, so the download ends early --
#: the silent truncation of #292, which the size check is there to catch.
_TRUNCATED_LENGTH = len(_FILE_CONTENT) - 2

#: One backoff between each pair of attempts, so one fewer than the four
#: the protocol makes: its documented 5s, 15s, 45s schedule.
#:
#: Deliberately a literal and NOT ``_COPY_MAX_ATTEMPTS - 1``. Derived from
#: the constant, this assertion would move with it -- dropping the retry
#: budget to 1 would lower the expectation to 0 and the test would still
#: pass, which is the whole failure it exists to catch.
_EXPECTED_BACKOFFS = 3


def _source_content() -> dict[str, Any]:
    return {
        _RESOURCE_ID: {
            GR_CONF_FILE_NAME: "",
            _FILE_NAME: _FILE_CONTENT,
        },
    }


def _a_source_resource() -> GenomicResource:
    return build_inmemory_test_protocol(_source_content()).get_resource(
        _RESOURCE_ID)


def _a_destination_resource(
    proto: FsspecReadWriteProtocol, src_res: GenomicResource,
) -> GenomicResource:
    """Name the resource the download publishes into, before it exists."""
    return GenomicResource(src_res.resource_id, src_res.version, proto)


def _partial_downloads(
    proto: FsspecReadWriteProtocol, resource: GenomicResource,
) -> list[str]:
    """Return the temp files left in the resource's ``.grr`` directory."""
    grr_dir = os.path.join(proto.get_resource_url(resource), GRR_INTERNAL_DIR)
    if not proto.filesystem.exists(grr_dir):
        return []
    return [
        entry for entry in proto.filesystem.ls(grr_dir, detail=False)
        if entry.endswith(".part")
    ]


def test_copy_resource_file_publish_write_failure_surfaces_the_error(
    tmp_path: pathlib.Path,
) -> None:
    src_res = _a_source_resource()
    dest_proto, dest_fs = build_faulty_test_protocol(tmp_path)
    dest_res = _a_destination_resource(dest_proto, src_res)
    dest_fs.fail_write(_PARTIAL_DOWNLOAD, OSError("no space left on device"))

    with pytest.raises(OSError, match="no space left on device"):
        dest_proto.copy_resource_file(src_res, dest_res, _FILE_NAME)


def test_copy_resource_file_publish_write_failure_publishes_no_file(
    tmp_path: pathlib.Path,
) -> None:
    src_res = _a_source_resource()
    dest_proto, dest_fs = build_faulty_test_protocol(tmp_path)
    dest_res = _a_destination_resource(dest_proto, src_res)
    dest_fs.fail_write(_PARTIAL_DOWNLOAD, OSError("no space left on device"))

    with pytest.raises(OSError):
        dest_proto.copy_resource_file(src_res, dest_res, _FILE_NAME)

    assert not dest_proto.file_exists(dest_res, _FILE_NAME)


def test_copy_resource_file_publish_write_failure_leaves_no_partial(
    tmp_path: pathlib.Path,
) -> None:
    src_res = _a_source_resource()
    dest_proto, dest_fs = build_faulty_test_protocol(tmp_path)
    dest_res = _a_destination_resource(dest_proto, src_res)
    dest_fs.fail_write(_PARTIAL_DOWNLOAD, OSError("no space left on device"))

    with pytest.raises(OSError):
        dest_proto.copy_resource_file(src_res, dest_res, _FILE_NAME)

    assert _partial_downloads(dest_proto, dest_res) == []


def test_copy_resource_file_publish_close_failure_leaves_no_partial(
    tmp_path: pathlib.Path,
) -> None:
    src_res = _a_source_resource()
    dest_proto, dest_fs = build_faulty_test_protocol(tmp_path)
    dest_res = _a_destination_resource(dest_proto, src_res)
    dest_fs.fail_close(_PARTIAL_DOWNLOAD, OSError("commit failed on close"))

    with pytest.raises(OSError, match="commit failed on close"):
        dest_proto.copy_resource_file(src_res, dest_res, _FILE_NAME)

    assert _partial_downloads(dest_proto, dest_res) == []


def test_copy_resource_file_cleanup_failure_does_not_mask_download_error(
    tmp_path: pathlib.Path, mocker: MockerFixture,
) -> None:
    src_proto, src_fs = build_faulty_test_protocol(
        tmp_path / "src", _source_content())
    src_res = src_proto.get_resource(_RESOURCE_ID)
    dest_proto, dest_fs = build_faulty_test_protocol(tmp_path / "dst")
    dest_res = _a_destination_resource(dest_proto, src_res)
    src_fs.corrupt_read(_SOURCE_FILE)
    dest_fs.fail_rm(_PARTIAL_DOWNLOAD, OSError("cleanup is broken too"))
    mocker.patch("gain.genomic_resources.fsspec_protocol.time.sleep")

    with pytest.raises(ChecksumMismatchError):
        dest_proto.copy_resource_file(src_res, dest_res, _FILE_NAME)

    # The scripted rm really did match: had its glob been wrong, the real
    # rm would have run and taken the partials with it. Without this the
    # test would pass just as well with a fault that never fired.
    assert _partial_downloads(dest_proto, dest_res) != []


def test_copy_resource_file_state_write_failure_surfaces_the_error(
    tmp_path: pathlib.Path,
) -> None:
    src_res = _a_source_resource()
    dest_proto, dest_fs = build_faulty_test_protocol(tmp_path)
    dest_res = _a_destination_resource(dest_proto, src_res)
    dest_fs.fail_write(_RESOURCE_FILE_STATE, OSError("state write refused"))

    with pytest.raises(OSError, match="state write refused"):
        dest_proto.copy_resource_file(src_res, dest_res, _FILE_NAME)


def test_copy_resource_file_state_write_failure_keeps_the_published_file(
    tmp_path: pathlib.Path,
) -> None:
    src_res = _a_source_resource()
    dest_proto, dest_fs = build_faulty_test_protocol(tmp_path)
    dest_res = _a_destination_resource(dest_proto, src_res)
    dest_fs.fail_write(_RESOURCE_FILE_STATE, OSError("state write refused"))

    with pytest.raises(OSError):
        dest_proto.copy_resource_file(src_res, dest_res, _FILE_NAME)

    assert dest_proto.file_exists(dest_res, _FILE_NAME)


def test_copy_resource_file_source_open_failure_surfaces_it_unretried(
    tmp_path: pathlib.Path, mocker: MockerFixture,
) -> None:
    src_proto, src_fs = build_faulty_test_protocol(
        tmp_path / "src", _source_content())
    src_res = src_proto.get_resource(_RESOURCE_ID)
    dest_proto, _ = build_faulty_test_protocol(tmp_path / "dst")
    dest_res = _a_destination_resource(dest_proto, src_res)
    src_fs.fail_open(_SOURCE_FILE, OSError("the remote refused the read"))
    sleep = mocker.patch("gain.genomic_resources.fsspec_protocol.time.sleep")

    with pytest.raises(OSError, match="the remote refused the read"):
        dest_proto.copy_resource_file(src_res, dest_res, _FILE_NAME)

    # RetryableCopyError is what confers retryability, not being an
    # OSError -- so a fault that would repeat identically is not slept on
    # and retried, it aborts the file on the first attempt.
    assert sleep.call_count == 0


def test_copy_resource_file_stalled_read_raises_after_retries(
    tmp_path: pathlib.Path, mocker: MockerFixture,
) -> None:
    src_proto, src_fs = build_faulty_test_protocol(
        tmp_path / "src", _source_content())
    src_res = src_proto.get_resource(_RESOURCE_ID)
    dest_proto, _ = build_faulty_test_protocol(tmp_path / "dst")
    dest_res = _a_destination_resource(dest_proto, src_res)
    src_fs.stall_read(_SOURCE_FILE)
    sleep = mocker.patch("gain.genomic_resources.fsspec_protocol.time.sleep")

    with pytest.raises(FSTimeoutError):
        dest_proto.copy_resource_file(src_res, dest_res, _FILE_NAME)

    # Every attempt was made, not just the first: the backoff slept
    # between each pair of them, and the last failure is what surfaced.
    assert sleep.call_count == _EXPECTED_BACKOFFS


def test_copy_resource_file_recovers_when_only_the_first_read_stalls(
    tmp_path: pathlib.Path, mocker: MockerFixture,
) -> None:
    src_proto, src_fs = build_faulty_test_protocol(
        tmp_path / "src", _source_content())
    src_res = src_proto.get_resource(_RESOURCE_ID)
    dest_proto, _ = build_faulty_test_protocol(tmp_path / "dst")
    dest_res = _a_destination_resource(dest_proto, src_res)
    src_fs.stall_read(_SOURCE_FILE, on_call=1)
    mocker.patch("gain.genomic_resources.fsspec_protocol.time.sleep")

    dest_proto.copy_resource_file(src_res, dest_res, _FILE_NAME)

    assert dest_proto.file_exists(dest_res, _FILE_NAME)


def test_copy_resource_file_short_read_raises_truncated_download(
    tmp_path: pathlib.Path, mocker: MockerFixture,
) -> None:
    src_proto, src_fs = build_faulty_test_protocol(
        tmp_path / "src", _source_content())
    src_res = src_proto.get_resource(_RESOURCE_ID)
    dest_proto, _ = build_faulty_test_protocol(tmp_path / "dst")
    dest_res = _a_destination_resource(dest_proto, src_res)
    src_fs.short_read(_SOURCE_FILE, after_bytes=_TRUNCATED_LENGTH)
    mocker.patch("gain.genomic_resources.fsspec_protocol.time.sleep")

    with pytest.raises(TruncatedDownloadError):
        dest_proto.copy_resource_file(src_res, dest_res, _FILE_NAME)


def test_copy_resource_file_corrupted_bytes_raise_checksum_mismatch(
    tmp_path: pathlib.Path, mocker: MockerFixture,
) -> None:
    src_proto, src_fs = build_faulty_test_protocol(
        tmp_path / "src", _source_content())
    src_res = src_proto.get_resource(_RESOURCE_ID)
    dest_proto, _ = build_faulty_test_protocol(tmp_path / "dst")
    dest_res = _a_destination_resource(dest_proto, src_res)
    src_fs.corrupt_read(_SOURCE_FILE)
    mocker.patch("gain.genomic_resources.fsspec_protocol.time.sleep")

    with pytest.raises(ChecksumMismatchError):
        dest_proto.copy_resource_file(src_res, dest_res, _FILE_NAME)


class _NewRetryableFailure(RetryableCopyError):
    """A retryable failure shape the copy loop has never been told about.

    Stands in for the fourth, fifth, nth transient failure gain will grow:
    declared here, in a test, so that nothing in the protocol module can
    have enumerated it. If retryability were still a hand-maintained list,
    this class would not be on it.
    """


def test_copy_resource_file_retries_an_unenumerated_retryable_subclass(
    tmp_path: pathlib.Path, mocker: MockerFixture,
) -> None:
    src_proto, src_fs = build_faulty_test_protocol(
        tmp_path / "src", _source_content())
    src_res = src_proto.get_resource(_RESOURCE_ID)
    dest_proto, _ = build_faulty_test_protocol(tmp_path / "dst")
    dest_res = _a_destination_resource(dest_proto, src_res)
    src_fs.fail_open(
        _SOURCE_FILE, _NewRetryableFailure("a transient fault"), on_call=1)
    sleep = mocker.patch("gain.genomic_resources.fsspec_protocol.time.sleep")

    dest_proto.copy_resource_file(src_res, dest_res, _FILE_NAME)

    # The second attempt published the file, so the first failure was
    # retried rather than surfaced -- and one backoff was slept.
    assert dest_proto.file_exists(dest_res, _FILE_NAME)
    assert sleep.call_count == 1
