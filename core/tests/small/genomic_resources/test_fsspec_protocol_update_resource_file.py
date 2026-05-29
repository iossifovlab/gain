# pylint: disable=W0621,C0114,C0116,W0212,W0613
import time
from typing import Any

import pytest
from fsspec.exceptions import FSTimeoutError
from gain.genomic_resources.fsspec_protocol import FsspecReadWriteProtocol
from gain.genomic_resources.testing import build_inmemory_test_protocol
from pytest_mock import MockerFixture


@pytest.mark.grr_rw
def test_update_resource_file_when_file_missing(
        content_fixture: dict[str, Any],
        fsspec_proto: FsspecReadWriteProtocol) -> None:

    # Given
    src_proto = build_inmemory_test_protocol(content_fixture)
    proto = fsspec_proto

    src_res = src_proto.get_resource("sub/two")
    dst_res = proto.get_resource("sub/two")

    proto.filesystem.delete(
        proto.get_resource_file_url(dst_res, "genes.gtf"))

    assert not proto.file_exists(dst_res, "genes.gtf")
    assert proto.load_resource_file_state(dst_res, "genes.gtf")

    # When
    state = proto.update_resource_file(src_res, dst_res, "genes.gtf")

    # Then
    assert proto.file_exists(dst_res, "genes.gtf")

    timestamp = proto.get_resource_file_timestamp(dst_res, "genes.gtf")

    assert state is not None
    assert state.filename == "genes.gtf"
    assert state.timestamp == timestamp
    assert state.timestamp == pytest.approx(time.time(), abs=5)
    assert state.md5 == "d9636a8dca9e5626851471d1c0ea92b1"


@pytest.mark.grr_rw
def test_update_resource_file_when_state_missing(
        content_fixture: dict[str, Any],
        fsspec_proto: FsspecReadWriteProtocol) -> None:

    # Given
    src_proto = build_inmemory_test_protocol(content_fixture)
    proto = fsspec_proto

    src_res = src_proto.get_resource("sub/two")
    dst_res = proto.get_resource("sub/two")

    proto.filesystem.delete(
        proto._get_resource_file_state_path(dst_res, "genes.gtf"))

    assert proto.file_exists(dst_res, "genes.gtf")
    assert not proto.load_resource_file_state(dst_res, "genes.gtf")
    fileurl = proto.get_resource_file_url(dst_res, "genes.gtf")
    timestamp = proto.filesystem.modified(fileurl)

    # When
    state = proto.update_resource_file(src_res, dst_res, "genes.gtf")

    # Then
    assert proto.file_exists(dst_res, "genes.gtf")

    assert state is not None
    assert state.filename == "genes.gtf"
    assert state.md5 == "d9636a8dca9e5626851471d1c0ea92b1"
    assert proto.filesystem.modified(fileurl) == timestamp


@pytest.mark.grr_rw
def test_update_resource_file_when_changed(
        content_fixture: dict[str, Any],
        fsspec_proto: FsspecReadWriteProtocol) -> None:

    # Given
    src_proto = build_inmemory_test_protocol(content_fixture)
    proto = fsspec_proto

    src_res = src_proto.get_resource("sub/two")
    dst_res = proto.get_resource("sub/two")

    with proto.open_raw_file(dst_res, "genes.gtf", mode="wt") as outfile:
        outfile.write("aaaa")
    proto.save_manifest(dst_res, proto.build_manifest(dst_res))

    # When
    state = proto.update_resource_file(src_res, dst_res, "genes.gtf")

    # Then
    assert proto.file_exists(dst_res, "genes.gtf")

    timestamp = proto.get_resource_file_timestamp(dst_res, "genes.gtf")

    assert state is not None
    assert state.filename == "genes.gtf"
    assert state.timestamp == timestamp
    assert state.timestamp == pytest.approx(time.time(), abs=5)

    assert state.md5 == "d9636a8dca9e5626851471d1c0ea92b1"


class _StallingFile:
    """A remote file handle whose read() always stalls (raises a timeout)."""

    def __enter__(self) -> "_StallingFile":
        return self

    def __exit__(self, *_args: object) -> bool:
        return False

    def read(self, *_args: object) -> bytes:
        raise FSTimeoutError("simulated stalled read")


@pytest.mark.grr_rw
def test_copy_resource_file_retries_transient_read_failure(
        content_fixture: dict[str, Any],
        fsspec_proto: FsspecReadWriteProtocol,
        mocker: MockerFixture) -> None:
    # A transient stall (e.g. aiohttp sock_read timeout) mid-download must be
    # retried with a fresh remote GET, not abort the file. See gain#43.

    # Given
    src_proto = build_inmemory_test_protocol(content_fixture)
    proto = fsspec_proto

    src_res = src_proto.get_resource("sub/two")
    dst_res = proto.get_resource("sub/two")

    # The first two opens hand back a stalling handle; the third succeeds.
    real_open = src_res.open_raw_file
    attempts = {"n": 0}

    def flaky_open(filename: str, mode: str = "rt", **kwargs: Any) -> Any:
        attempts["n"] += 1
        if attempts["n"] <= 2:
            return _StallingFile()
        return real_open(filename, mode, **kwargs)

    mocker.patch.object(src_res, "open_raw_file", side_effect=flaky_open)
    sleep = mocker.patch(
        "gain.genomic_resources.fsspec_protocol.time.sleep")

    # When
    state = proto.copy_resource_file(src_res, dst_res, "genes.gtf")

    # Then: it retried twice, then completed correctly
    assert attempts["n"] == 3
    assert sleep.call_count == 2
    assert state is not None
    assert state.md5 == "d9636a8dca9e5626851471d1c0ea92b1"
    assert proto.file_exists(dst_res, "genes.gtf")


@pytest.mark.grr_rw
def test_copy_resource_file_raises_after_retries_exhausted(
        content_fixture: dict[str, Any],
        fsspec_proto: FsspecReadWriteProtocol,
        mocker: MockerFixture) -> None:
    # A read that never recovers must raise after the retry budget is spent.
    # See gain#43.

    # Given
    src_proto = build_inmemory_test_protocol(content_fixture)
    proto = fsspec_proto

    src_res = src_proto.get_resource("sub/two")
    dst_res = proto.get_resource("sub/two")

    mocker.patch.object(
        src_res, "open_raw_file",
        side_effect=lambda *_a, **_k: _StallingFile())
    mocker.patch("gain.genomic_resources.fsspec_protocol.time.sleep")

    # When / Then
    with pytest.raises(FSTimeoutError):
        proto.copy_resource_file(src_res, dst_res, "genes.gtf")


@pytest.mark.grr_rw
def test_do_not_update_resource_file_when_state_changed_but_file_not(
        content_fixture: dict[str, Any],
        fsspec_proto: FsspecReadWriteProtocol) -> None:

    # Given
    src_proto = build_inmemory_test_protocol(content_fixture)
    proto = fsspec_proto

    src_res = src_proto.get_resource("sub/two")
    dst_res = proto.get_resource("sub/two")

    state = proto.load_resource_file_state(dst_res, "genes.gtf")
    assert state is not None
    state.timestamp = 0

    proto.save_resource_file_state(dst_res, state)

    fileurl = proto.get_resource_file_url(dst_res, "genes.gtf")
    fileid = (
        proto.filesystem.modified(fileurl),)

    # When
    proto.update_resource_file(src_res, dst_res, "genes.gtf")

    # Then: file not changed
    assert fileid == (
        proto.filesystem.modified(fileurl), )
