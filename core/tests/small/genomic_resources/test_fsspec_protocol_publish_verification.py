# pylint: disable=W0621,C0114,C0116,W0212,W0613
from typing import Any

import pytest
from gain.genomic_resources.fsspec_protocol import (
    _COPY_MAX_ATTEMPTS,
    CorruptedPublishError,
    FsspecReadWriteProtocol,
)
from gain.genomic_resources.repository import GR_CONTENTS_FILE_NAME
from gain.genomic_resources.testing import build_inmemory_test_protocol
from pytest_mock import MockerFixture

# Corrupt payloads of a DIFFERENT length than the real file. The length is
# what makes the corruption detectable at all: the download's own byte-count
# and md5 checks both ran before the publish, so only a post-publish stat can
# still notice. Same-length corruption is deliberately out of reach (#880).
#
# One payload is shorter than the fixture file and one is longer. The guard
# is an equality, and a test set that only ever shrank the object would let a
# `<` pass just as happily.
CORRUPT_PAYLOAD = b"corrupt"
LONGER_CORRUPT_PAYLOAD = b"corrupt" * 10

# Corrupt every attempt the retry loop will make. Tied to the loop's own cap
# so the tests keep meaning "always" if that cap ever moves.
EVERY_ATTEMPT = _COPY_MAX_ATTEMPTS


class _CorruptingPublish:
    """A filesystem ``mv`` that publishes the object, then corrupts it.

    Simulates a publish that lands different bytes than the download
    verified -- a partial object-store copy, a rename that loses the tail.
    The download hashes and size-checks every byte it writes *before* the
    move, so a corruption introduced by the move itself is invisible to
    those checks; before #880 the state then recorded the pre-move digest
    against the post-move object and nothing downstream could notice.

    Corruption is applied through the filesystem rather than the local
    ``os`` module, so the same fault injects on every scheme -- including
    the s3 arm, where ``mv`` is copy+delete rather than a rename and is
    therefore the arm this guard most needs (ADR 0021).

    ``corrupt_first`` bounds how many publishes are corrupted, so a test can
    corrupt only the first and let the retry publish cleanly. A ``payload``
    of ``None`` publishes nothing at all -- the limit case of a partial
    object-store copy, where the move leaves no object behind.
    """

    def __init__(
        self,
        filesystem: Any,
        filename: str,
        *,
        corrupt_first: int,
        payload: bytes | None,
    ) -> None:
        self._real_mv = filesystem.mv
        self._filesystem = filesystem
        self._filename = filename
        self._corrupt_first = corrupt_first
        self._payload = payload
        self.corrupted = 0

    def __call__(
        self, path1: str, path2: str, *args: Any, **kwargs: Any,
    ) -> None:
        self._real_mv(path1, path2, *args, **kwargs)
        if not str(path2).endswith(self._filename) \
                or self.corrupted >= self._corrupt_first:
            return
        self.corrupted += 1
        if self._payload is None:
            self._filesystem.rm(path2)
        else:
            with self._filesystem.open(path2, "wb") as outfile:
                outfile.write(self._payload)


def _corrupt_publishes_of(
    proto: FsspecReadWriteProtocol,
    filename: str,
    mocker: MockerFixture,
    *,
    corrupt_first: int,
    payload: bytes | None = CORRUPT_PAYLOAD,
) -> _CorruptingPublish:
    """Make ``proto``'s publishes of ``filename`` corrupt, and stop waiting.

    Keeps the one ordering constraint in a single place: the fault has to
    capture the real ``mv`` before the patch replaces it. Also silences the
    retry backoff -- it is 5s, 15s then 45s, so a fault that outlives the
    first attempt would otherwise put a full minute of real sleeping into
    the suite.
    """
    corrupting = _CorruptingPublish(
        proto.filesystem, filename,
        corrupt_first=corrupt_first, payload=payload)
    mocker.patch.object(proto.filesystem, "mv", side_effect=corrupting)
    mocker.patch("gain.genomic_resources.fsspec_protocol.time.sleep")
    return corrupting


@pytest.mark.grr_rw
@pytest.mark.parametrize("payload", [
    pytest.param(CORRUPT_PAYLOAD, id="shrinks-the-object"),
    pytest.param(LONGER_CORRUPT_PAYLOAD, id="grows-the-object"),
])
def test_copy_resource_file_refuses_a_corrupting_publish(
        payload: bytes,
        content_fixture: dict[str, Any],
        fsspec_proto: FsspecReadWriteProtocol,
        mocker: MockerFixture) -> None:
    # A publish that lands a different object than the download verified
    # must not be recorded as good. The digest reused from the download
    # (#865) describes bytes that are no longer on the store, so nothing
    # downstream could ever notice it. See #880.

    # Given a destination whose every publish corrupts the object
    src_proto = build_inmemory_test_protocol(content_fixture)
    proto = fsspec_proto

    src_res = src_proto.get_resource("sub/two")
    dst_res = proto.get_resource("sub/two")
    entry = src_res.get_manifest()["genes.gtf"]

    assert len(payload) != entry.size, \
        "a same-length payload leaves the guard nothing to catch"

    _corrupt_publishes_of(
        proto, "genes.gtf", mocker,
        corrupt_first=EVERY_ATTEMPT, payload=payload)

    # When / Then: the copy refuses rather than returning a state
    with pytest.raises(CorruptedPublishError) as excinfo:
        proto.copy_resource_file(src_res, dst_res, "genes.gtf")

    # ...and the message names both counts, so the fault is diagnosable
    message = str(excinfo.value)
    assert f"verified {entry.size} bytes" in message
    assert f"published {len(payload)}" in message
    assert "genes.gtf" in message


@pytest.mark.grr_rw
@pytest.mark.parametrize("payload", [
    pytest.param(CORRUPT_PAYLOAD, id="publishes-wrong-bytes"),
    pytest.param(None, id="publishes-nothing"),
])
def test_copy_resource_file_repairs_a_corrupting_publish_by_retrying(
        payload: bytes | None,
        content_fixture: dict[str, Any],
        fsspec_proto: FsspecReadWriteProtocol,
        mocker: MockerFixture) -> None:
    # A corrupting publish is retryable, like a stalled read or a bad
    # checksum: the file is downloaded and published again, and the caller
    # gets a healthy file rather than an error. That holds for a move which
    # landed nothing at all, too -- otherwise the most extreme form of the
    # fault would be the one form that never gets repaired. See #880.

    # Given a destination whose FIRST publish corrupts, and no other
    src_proto = build_inmemory_test_protocol(content_fixture)
    proto = fsspec_proto

    src_res = src_proto.get_resource("sub/two")
    dst_res = proto.get_resource("sub/two")
    entry = src_res.get_manifest()["genes.gtf"]

    corrupting = _corrupt_publishes_of(
        proto, "genes.gtf", mocker, corrupt_first=1, payload=payload)
    sleep = mocker.patch(
        "gain.genomic_resources.fsspec_protocol.time.sleep")

    # When
    state = proto.copy_resource_file(src_res, dst_res, "genes.gtf")

    # Then the corruption happened, was retried once, and was repaired
    assert corrupting.corrupted == 1
    assert sleep.call_count == 1

    assert state is not None
    assert state.md5 == entry.md5
    assert state.size == entry.size

    # ...and the state describes what is actually stored
    assert proto.get_resource_file_size(dst_res, "genes.gtf") == entry.size


@pytest.mark.grr_rw
def test_a_corrupt_published_file_is_scheduled_for_redownload(
        content_fixture: dict[str, Any],
        fsspec_proto: FsspecReadWriteProtocol,
        mocker: MockerFixture) -> None:
    # When every retry corrupts, the copy gives up with a corrupt object at
    # the real path -- there is nowhere else to put it, the publish already
    # happened. What must NOT happen is that object being blessed: the next
    # cache verdict has to send it back for download. Before #880 the state
    # written by the failing copy said the file matched the manifest, and
    # that verdict came back clean forever.

    # Given a destination whose every publish corrupts the object
    src_proto = build_inmemory_test_protocol(content_fixture)
    proto = fsspec_proto

    src_res = src_proto.get_resource("sub/two")
    dst_res = proto.get_resource("sub/two")

    _corrupt_publishes_of(
        proto, "genes.gtf", mocker, corrupt_first=EVERY_ATTEMPT)

    with pytest.raises(CorruptedPublishError):
        proto.copy_resource_file(src_res, dst_res, "genes.gtf")

    # The corrupt object really is what is stored -- otherwise the verdict
    # below would be about a healthy file and prove nothing
    assert proto.get_resource_file_size(dst_res, "genes.gtf") \
        == len(CORRUPT_PAYLOAD)

    # When
    verdict = proto.classify_resource_file(src_res, dst_res, "genes.gtf")

    # Then
    assert verdict.needs_download


@pytest.mark.grr_rw
def test_a_healthy_download_stats_the_published_object_once(
        content_fixture: dict[str, Any],
        fsspec_proto: FsspecReadWriteProtocol,
        mocker: MockerFixture) -> None:
    # Verifying the publish costs no extra stat of its own: the stat it
    # checks the size against is the one the state was going to be built
    # from anyway, so it is taken once and passed on rather than taken
    # again. On a remote store that would be a second round trip -- the
    # same reasoning that stopped the digest being recomputed there
    # (#865). See #880.
    #
    # Pinned on the stat rather than on the size accessor since #936,
    # which made that one stat carry the change token beside the size and
    # had the download read both off it. The claim is unchanged; what
    # the single call yields is not.
    #
    # This pins that stat specifically; the budget for every metadata
    # call a download makes is in
    # ``test_fsspec_protocol_download_single_stat``.

    # Given a source and a destination, publishing normally
    src_proto = build_inmemory_test_protocol(content_fixture)
    proto = fsspec_proto

    src_res = src_proto.get_resource("sub/two")
    dst_res = proto.get_resource("sub/two")
    entry = src_res.get_manifest()["genes.gtf"]

    stat = mocker.spy(proto, "_stat_filepath")

    # When
    state = proto.copy_resource_file(src_res, dst_res, "genes.gtf")

    # Then the file published cleanly...
    assert state is not None
    assert state.md5 == entry.md5
    assert state.size == entry.size

    # ...having been measured exactly once
    assert stat.call_count == 1


@pytest.mark.grr_rw
@pytest.mark.parametrize("payload", [
    pytest.param(CORRUPT_PAYLOAD, id="publishes-wrong-bytes"),
    pytest.param(None, id="publishes-nothing"),
])
def test_build_content_file_refuses_a_corrupting_publish(
        payload: bytes | None,
        fsspec_proto: FsspecReadWriteProtocol,
        mocker: MockerFixture) -> None:
    # The repository's own artifacts publish through the same
    # write-temp-verify-move seam as a downloaded file (#933), so the
    # post-move check guards them too: a move that lands a short object,
    # or none at all, must be reported rather than left as the contents
    # index every client of the repository reads.
    proto = fsspec_proto

    # Given a repository whose publish of the contents index corrupts it
    _corrupt_publishes_of(
        proto, GR_CONTENTS_FILE_NAME, mocker,
        corrupt_first=1, payload=payload)

    # When / Then: the publish refuses rather than returning content
    with pytest.raises(CorruptedPublishError) as excinfo:
        proto.build_content_file()

    # ...naming the artifact, so the fault is diagnosable
    assert GR_CONTENTS_FILE_NAME in str(excinfo.value)
