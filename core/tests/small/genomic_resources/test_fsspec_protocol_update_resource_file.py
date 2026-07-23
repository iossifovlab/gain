# pylint: disable=W0621,C0114,C0116,W0212,W0613
import time
from typing import Any

import pytest
from fsspec.exceptions import FSTimeoutError
from gain.genomic_resources.fsspec_protocol import (
    ChecksumMismatchError,
    FsspecReadWriteProtocol,
    TruncatedDownloadError,
)
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


class _ShortReadFile:
    """A remote handle that serves a truncated prefix, then a clean EOF.

    Simulates the fsspec silent short read of gain#292 (H1): the
    range-reassembly layer hands back fewer bytes than the file holds and
    then signals end-of-stream with an empty ``read``, so the copy loop's
    ``while chunk := infile.read(...)`` stops early and writes a truncated
    file that looks like a complete download and fails only at the md5 check.

    ``keep`` is the total number of leading bytes served before EOF.
    """

    def __init__(self, real_handle: Any, keep: int) -> None:
        self._real = real_handle
        self._keep = keep

    def __enter__(self) -> "_ShortReadFile":
        self._real.__enter__()
        return self

    def __exit__(self, *args: object) -> bool:
        self._real.__exit__(*args)
        return False

    def read(self, *args: object) -> bytes:
        if self._keep <= 0:
            return b""
        chunk = self._real.read(*args)
        if not chunk:
            return chunk
        if len(chunk) > self._keep:
            chunk = chunk[:self._keep]
        self._keep -= len(chunk)
        return chunk


@pytest.mark.grr_rw
def test_copy_resource_file_raises_truncated_download_on_short_read(
        content_fixture: dict[str, Any],
        fsspec_proto: FsspecReadWriteProtocol,
        mocker: MockerFixture) -> None:
    # A silent short read (fewer bytes than the manifest size, then EOF) must
    # be caught as a truncation -- not misreported as a checksum mismatch --
    # and the error must record both the expected and the received byte count
    # (the size measurement gain#292 says was never captured). A source that
    # is short on every attempt exhausts the retry budget and raises. See
    # gain#292 (H1).

    # Given
    src_proto = build_inmemory_test_protocol(content_fixture)
    proto = fsspec_proto

    src_res = src_proto.get_resource("sub/two")
    dst_res = proto.get_resource("sub/two")

    expected_size = src_res.get_manifest()["genes.gtf"].size
    short_size = expected_size - 5
    assert short_size > 0

    real_open = src_res.open_raw_file
    attempts = {"n": 0}

    def short_open(filename: str, mode: str = "rt", **kwargs: Any) -> Any:
        attempts["n"] += 1
        return _ShortReadFile(real_open(filename, mode, **kwargs), short_size)

    mocker.patch.object(src_res, "open_raw_file", side_effect=short_open)
    sleep = mocker.patch(
        "gain.genomic_resources.fsspec_protocol.time.sleep")

    # When / Then
    with pytest.raises(TruncatedDownloadError) as exc_info:
        proto.copy_resource_file(src_res, dst_res, "genes.gtf")

    # retried the whole file budget, then gave up
    assert attempts["n"] == 4
    assert sleep.call_count == 3
    # the error names the received and the expected size
    message = str(exc_info.value)
    assert str(short_size) in message
    assert str(expected_size) in message


@pytest.mark.grr_rw
def test_copy_resource_file_recovers_from_transient_short_read(
        content_fixture: dict[str, Any],
        fsspec_proto: FsspecReadWriteProtocol,
        mocker: MockerFixture) -> None:
    # A short read on the first attempt, then a full stream on retry, must be
    # retried from scratch and complete cleanly -- a truncated download is a
    # retryable error, not a hard failure. See gain#292.

    # Given
    src_proto = build_inmemory_test_protocol(content_fixture)
    proto = fsspec_proto

    src_res = src_proto.get_resource("sub/two")
    dst_res = proto.get_resource("sub/two")

    expected_size = src_res.get_manifest()["genes.gtf"].size

    real_open = src_res.open_raw_file
    attempts = {"n": 0}

    def flaky_open(filename: str, mode: str = "rt", **kwargs: Any) -> Any:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return _ShortReadFile(
                real_open(filename, mode, **kwargs), expected_size - 5)
        return real_open(filename, mode, **kwargs)

    mocker.patch.object(src_res, "open_raw_file", side_effect=flaky_open)
    mocker.patch("gain.genomic_resources.fsspec_protocol.time.sleep")

    # When
    state = proto.copy_resource_file(src_res, dst_res, "genes.gtf")

    # Then: it retried once, then completed correctly
    assert attempts["n"] == 2
    assert state is not None
    assert state.md5 == "d9636a8dca9e5626851471d1c0ea92b1"
    assert proto.file_exists(dst_res, "genes.gtf")


class _CorruptingFile:
    """Serves the file at full length, but with every byte altered.

    Simulates a full-length transfer with wrong content (gain#292, H2/H3): the
    byte count matches the manifest so the truncation guard passes, and only
    the md5 check catches the corruption.
    """

    def __init__(self, real_handle: Any) -> None:
        self._real = real_handle

    def __enter__(self) -> "_CorruptingFile":
        self._real.__enter__()
        return self

    def __exit__(self, *args: object) -> bool:
        self._real.__exit__(*args)
        return False

    def read(self, *args: object) -> bytes:
        chunk = self._real.read(*args)
        if not chunk:
            return chunk
        return bytes(b ^ 0xFF for b in chunk)


@pytest.mark.grr_rw
def test_copy_resource_file_checksum_mismatch_records_size(
        content_fixture: dict[str, Any],
        fsspec_proto: FsspecReadWriteProtocol,
        mocker: MockerFixture) -> None:
    # A full-length transfer with corrupt content (size matches, md5 does not)
    # must still raise ChecksumMismatchError -- and the error must now record
    # the byte count, the measurement gain#292 says was never captured, which
    # is what distinguishes a truncation from a full-length corruption.

    # Given
    src_proto = build_inmemory_test_protocol(content_fixture)
    proto = fsspec_proto

    src_res = src_proto.get_resource("sub/two")
    dst_res = proto.get_resource("sub/two")

    expected_size = src_res.get_manifest()["genes.gtf"].size

    real_open = src_res.open_raw_file

    def corrupt_open(filename: str, mode: str = "rt", **kwargs: Any) -> Any:
        return _CorruptingFile(real_open(filename, mode, **kwargs))

    mocker.patch.object(src_res, "open_raw_file", side_effect=corrupt_open)
    mocker.patch("gain.genomic_resources.fsspec_protocol.time.sleep")

    # When / Then
    with pytest.raises(ChecksumMismatchError) as exc_info:
        proto.copy_resource_file(src_res, dst_res, "genes.gtf")

    # the error records the received byte count (full length, so it is the
    # manifest size) -- proving the transfer was NOT truncated
    assert str(expected_size) in str(exc_info.value)


@pytest.mark.grr_rw
def test_copy_resource_file_on_bytes_deltas_sum_to_size(
        content_fixture: dict[str, Any],
        fsspec_proto: FsspecReadWriteProtocol) -> None:
    # The optional on_bytes callback must be invoked once per written chunk,
    # with the positive deltas summing to the file's byte size and no single
    # delta exceeding CHUNK_SIZE. See gain#77 (slice 1).

    # Given
    src_proto = build_inmemory_test_protocol(content_fixture)
    proto = fsspec_proto

    src_res = src_proto.get_resource("sub/two")
    dst_res = proto.get_resource("sub/two")

    expected_size = src_res.get_manifest()["genes.gtf"].size

    deltas: list[int] = []

    # When
    state = proto.copy_resource_file(
        src_res, dst_res, "genes.gtf", on_bytes=deltas.append)

    # Then
    assert state is not None
    assert deltas, "on_bytes was never called"
    assert all(0 < d <= proto.CHUNK_SIZE for d in deltas)
    assert sum(deltas) == expected_size


class _PartialThenStallingFile:
    """A remote handle that yields the real file once, then stalls.

    It delegates the first ``read`` to a real file handle (so it returns the
    actual file bytes, one or more chunks), then raises ``FSTimeoutError`` on
    the next ``read`` — simulating a download that streams some bytes before
    the connection stalls mid-transfer.
    """

    def __init__(self, real_handle: Any) -> None:
        self._real = real_handle
        self._served = False

    def __enter__(self) -> "_PartialThenStallingFile":
        self._real.__enter__()
        return self

    def __exit__(self, *args: object) -> bool:
        self._real.__exit__(*args)
        return False

    def read(self, *args: object) -> bytes:
        if self._served:
            raise FSTimeoutError("simulated stall after partial read")
        chunk = self._real.read(*args)
        if chunk:
            self._served = True
            return chunk
        return chunk


@pytest.mark.grr_rw
def test_copy_resource_file_on_bytes_rolls_back_on_retry(
        content_fixture: dict[str, Any],
        fsspec_proto: FsspecReadWriteProtocol,
        mocker: MockerFixture) -> None:
    # A retried download must not double-count bytes: the partial bytes
    # credited on the failed attempt are rolled back with a compensating
    # negative delta before the retry, so the net sum equals the file size
    # exactly. See gain#77 (slice 1).

    # Given
    src_proto = build_inmemory_test_protocol(content_fixture)
    proto = fsspec_proto

    src_res = src_proto.get_resource("sub/two")
    dst_res = proto.get_resource("sub/two")

    expected_size = src_res.get_manifest()["genes.gtf"].size

    real_open = src_res.open_raw_file
    attempts = {"n": 0}

    def flaky_open(filename: str, mode: str = "rt", **kwargs: Any) -> Any:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return _PartialThenStallingFile(
                real_open(filename, mode, **kwargs))
        return real_open(filename, mode, **kwargs)

    mocker.patch.object(src_res, "open_raw_file", side_effect=flaky_open)
    mocker.patch("gain.genomic_resources.fsspec_protocol.time.sleep")

    deltas: list[int] = []

    # When
    state = proto.copy_resource_file(
        src_res, dst_res, "genes.gtf", on_bytes=deltas.append)

    # Then
    assert attempts["n"] == 2
    assert state is not None

    positives = [d for d in deltas if d > 0]
    negatives = [d for d in deltas if d < 0]

    # partial bytes on attempt 1, full bytes on attempt 2
    assert sum(positives) == expected_size * 2
    # exactly one compensating rollback equal to the partial sum
    assert negatives == [-expected_size]
    # net credited bytes equals the file size
    assert sum(deltas) == expected_size


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
def test_classify_resource_file_when_file_missing(
        content_fixture: dict[str, Any],
        fsspec_proto: FsspecReadWriteProtocol) -> None:
    # A file that is not present locally must be flagged for download, with
    # the manifest's recorded size, and classify must take no lock / copy.

    # Given
    src_proto = build_inmemory_test_protocol(content_fixture)
    proto = fsspec_proto

    src_res = src_proto.get_resource("sub/two")
    dst_res = proto.get_resource("sub/two")

    proto.filesystem.delete(
        proto.get_resource_file_url(dst_res, "genes.gtf"))
    assert not proto.file_exists(dst_res, "genes.gtf")

    expected_size = src_res.get_manifest()["genes.gtf"].size

    # When
    verdict = proto.classify_resource_file(src_res, dst_res, "genes.gtf")

    # Then
    assert verdict.needs_download is True
    assert verdict.size == expected_size
    # classify must not have downloaded the file
    assert not proto.file_exists(dst_res, "genes.gtf")


@pytest.mark.grr_rw
def test_classify_resource_file_when_fresh(
        content_fixture: dict[str, Any],
        fsspec_proto: FsspecReadWriteProtocol) -> None:
    # A file already cached with a matching md5 needs no download.

    # Given
    src_proto = build_inmemory_test_protocol(content_fixture)
    proto = fsspec_proto

    src_res = src_proto.get_resource("sub/two")
    dst_res = proto.get_resource("sub/two")

    assert proto.file_exists(dst_res, "genes.gtf")

    # When
    verdict = proto.classify_resource_file(src_res, dst_res, "genes.gtf")

    # Then
    assert verdict.needs_download is False


@pytest.mark.grr_rw
def test_classify_resource_file_when_md5_mismatch(
        content_fixture: dict[str, Any],
        fsspec_proto: FsspecReadWriteProtocol) -> None:
    # A locally-present file whose content drifted from the manifest md5
    # must be flagged for download with the manifest size.

    # Given
    src_proto = build_inmemory_test_protocol(content_fixture)
    proto = fsspec_proto

    src_res = src_proto.get_resource("sub/two")
    dst_res = proto.get_resource("sub/two")

    with proto.open_raw_file(dst_res, "genes.gtf", mode="wt") as outfile:
        outfile.write("aaaa")
    proto.save_manifest(dst_res, proto.build_manifest(dst_res))

    expected_size = src_res.get_manifest()["genes.gtf"].size

    # When
    verdict = proto.classify_resource_file(src_res, dst_res, "genes.gtf")

    # Then
    assert verdict.needs_download is True
    assert verdict.size == expected_size
    # classify must not overwrite the local (drifted) file
    with proto.open_raw_file(dst_res, "genes.gtf") as infile:
        assert infile.read() == "aaaa"


@pytest.mark.grr_rw
def test_classify_resource_file_absent_from_manifest_deletes(
        content_fixture: dict[str, Any],
        fsspec_proto: FsspecReadWriteProtocol) -> None:
    # A locally-cached file no longer present in the remote manifest must be
    # deleted (as update_resource_file does) and flagged as no-download.

    # Given
    src_proto = build_inmemory_test_protocol(content_fixture)
    proto = fsspec_proto

    src_res = src_proto.get_resource("sub/two")
    dst_res = proto.get_resource("sub/two")

    with proto.open_raw_file(dst_res, "stale.txt", mode="wt") as outfile:
        outfile.write("stale")
    proto.save_manifest(dst_res, proto.build_manifest(dst_res))
    assert proto.file_exists(dst_res, "stale.txt")

    # When
    verdict = proto.classify_resource_file(src_res, dst_res, "stale.txt")

    # Then
    assert verdict.needs_download is False
    assert verdict.size == 0
    assert not proto.file_exists(dst_res, "stale.txt")


@pytest.mark.grr_rw
def test_classify_resource_file_refreshes_state_on_drift(
        content_fixture: dict[str, Any],
        fsspec_proto: FsspecReadWriteProtocol) -> None:
    # The state-refresh side effect of update_resource_file (rebuild + save
    # the .state when timestamp/size drift) must be preserved by classify.

    # Given
    src_proto = build_inmemory_test_protocol(content_fixture)
    proto = fsspec_proto

    src_res = src_proto.get_resource("sub/two")
    dst_res = proto.get_resource("sub/two")

    state = proto.load_resource_file_state(dst_res, "genes.gtf")
    assert state is not None
    state.timestamp = 0
    proto.save_resource_file_state(dst_res, state)

    # When
    proto.classify_resource_file(src_res, dst_res, "genes.gtf")

    # Then: the persisted state has been rebuilt to the real timestamp
    refreshed = proto.load_resource_file_state(dst_res, "genes.gtf")
    assert refreshed is not None
    assert refreshed.timestamp != 0


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
