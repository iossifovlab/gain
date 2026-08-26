# pylint: disable=W0621,C0114,C0116,W0212,W0613
from typing import Any

import pytest
from gain.genomic_resources.fsspec_protocol import (
    CorruptedPublishError,
    FsspecReadWriteProtocol,
)
from gain.genomic_resources.testing import build_inmemory_test_protocol
from pytest_mock import MockerFixture

# The manifest digest and byte size of `sub/two`'s genes.gtf in
# ``content_fixture`` -- what a healthy publish must leave behind.
GENES_GTF_MD5 = "d9636a8dca9e5626851471d1c0ea92b1"
GENES_GTF_SIZE = 17

# A corrupt payload of a DIFFERENT length than the real file. The size is
# what makes the corruption detectable at all: the download's own byte-count
# and md5 checks both ran before the publish, so only a post-publish stat can
# still notice. Same-length corruption is deliberately out of reach (#880).
CORRUPT_PAYLOAD = b"corrupt"


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
    corrupt only the first and let the retry publish cleanly.
    """

    def __init__(
        self,
        filesystem: Any,
        filename: str,
        *,
        corrupt_first: int,
    ) -> None:
        self._real_mv = filesystem.mv
        self._filesystem = filesystem
        self._filename = filename
        self._corrupt_first = corrupt_first
        self.corrupted = 0

    def __call__(
        self, path1: str, path2: str, *args: Any, **kwargs: Any,
    ) -> Any:
        result = self._real_mv(path1, path2, *args, **kwargs)
        if str(path2).endswith(self._filename) \
                and self.corrupted < self._corrupt_first:
            self.corrupted += 1
            with self._filesystem.open(path2, "wb") as outfile:
                outfile.write(CORRUPT_PAYLOAD)
        return result


@pytest.mark.grr_rw
def test_copy_resource_file_refuses_a_corrupting_publish(
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

    corrupting = _CorruptingPublish(
        proto.filesystem, "genes.gtf", corrupt_first=99)
    mocker.patch.object(proto.filesystem, "mv", side_effect=corrupting)
    mocker.patch("gain.genomic_resources.fsspec_protocol.time.sleep")

    # When / Then: the copy refuses rather than returning a state
    with pytest.raises(CorruptedPublishError) as excinfo:
        proto.copy_resource_file(src_res, dst_res, "genes.gtf")

    # ...and the message names both counts, so the fault is diagnosable
    message = str(excinfo.value)
    assert str(GENES_GTF_SIZE) in message
    assert str(len(CORRUPT_PAYLOAD)) in message
    assert "genes.gtf" in message


@pytest.mark.grr_rw
def test_copy_resource_file_repairs_a_corrupting_publish_by_retrying(
        content_fixture: dict[str, Any],
        fsspec_proto: FsspecReadWriteProtocol,
        mocker: MockerFixture) -> None:
    # A corrupting publish is retryable, like a stalled read or a bad
    # checksum: the file is downloaded and published again, and the caller
    # gets a healthy file rather than an error. See #880.

    # Given a destination whose FIRST publish corrupts, and no other
    src_proto = build_inmemory_test_protocol(content_fixture)
    proto = fsspec_proto

    src_res = src_proto.get_resource("sub/two")
    dst_res = proto.get_resource("sub/two")

    corrupting = _CorruptingPublish(
        proto.filesystem, "genes.gtf", corrupt_first=1)
    mocker.patch.object(proto.filesystem, "mv", side_effect=corrupting)
    sleep = mocker.patch(
        "gain.genomic_resources.fsspec_protocol.time.sleep")

    # When
    state = proto.copy_resource_file(src_res, dst_res, "genes.gtf")

    # Then the corruption happened, was retried once, and was repaired
    assert corrupting.corrupted == 1
    assert sleep.call_count == 1

    assert state is not None
    assert state.md5 == GENES_GTF_MD5
    assert state.size == GENES_GTF_SIZE

    # ...and the state describes what is actually stored
    assert proto.get_resource_file_size(dst_res, "genes.gtf") \
        == GENES_GTF_SIZE


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

    corrupting = _CorruptingPublish(
        proto.filesystem, "genes.gtf", corrupt_first=99)
    mocker.patch.object(proto.filesystem, "mv", side_effect=corrupting)
    mocker.patch("gain.genomic_resources.fsspec_protocol.time.sleep")

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
    # Verifying the publish costs nothing: the size it checks is the one the
    # state was going to be built from anyway, so it is stat'ed once and
    # passed on, not stat'ed again. On a remote store every stat is a round
    # trip, which is the same reasoning that stopped the digest being
    # recomputed there (#865). See #880.

    # Given a source and a destination, publishing normally
    src_proto = build_inmemory_test_protocol(content_fixture)
    proto = fsspec_proto

    src_res = src_proto.get_resource("sub/two")
    dst_res = proto.get_resource("sub/two")

    stat = mocker.spy(proto, "get_resource_file_size")

    # When
    state = proto.copy_resource_file(src_res, dst_res, "genes.gtf")

    # Then the file published cleanly...
    assert state is not None
    assert state.md5 == GENES_GTF_MD5
    assert state.size == GENES_GTF_SIZE

    # ...having been measured exactly once
    assert stat.call_count == 1
