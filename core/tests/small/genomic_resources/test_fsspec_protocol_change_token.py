# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""What decides that a recorded file state is out of date (gain#881).

A ``.state`` is trusted to describe the bytes in the store, and what it
is checked against decides two different failures. Too strict, and an
unchanged file is re-read and re-hashed for nothing -- which is what a
modification time does on s3, where the value depends on whether the
answer came from ``head_object`` (whole seconds) or ``list_objects_v2``
(milliseconds). Too loose, and a real change goes unnoticed and a stale
md5 sum is published into a ``.MANIFEST``, which is a committed artefact.

The store's own change token is what avoids both, and these tests hold
the second line: rounding the recorded timestamp to whole seconds would
make the first failure go away and quietly buy the second one, because
same-size rewrites land in the same whole second routinely.
"""
import pytest
from gain.genomic_resources.fsspec_protocol import FsspecReadWriteProtocol


def _different_bytes_of_the_same_length(text: str) -> str:
    """Replace every character, keeping the length exactly."""
    return "".join("y" if char != "y" else "z" for char in text)


@pytest.mark.grr_full
def test_a_same_size_rewrite_is_detected_without_a_pause(
        fsspec_proto: FsspecReadWriteProtocol) -> None:
    """A change is noticed even when nothing waits for the clock to tick.

    The two writes are deliberately not separated by a sleep, so on s3
    they land in the same whole second -- measured, 11 of 11 consecutive
    same-size rewrites did. A comparison that only saw whole seconds
    would find the state still current, keep its now-wrong md5 sum, and
    report that the file needs no download.
    """
    proto = fsspec_proto
    resource = proto.get_resource("one")

    # Given a file whose recorded state describes the bytes stored now
    with proto.open_raw_file(resource, "data.txt", "rt") as infile:
        original = infile.read()
    proto.save_resource_file_state(
        resource, proto.build_resource_file_state(resource, "data.txt"))

    # When it is overwritten with different bytes of the very same length
    with proto.open_raw_file(resource, "data.txt", "wt") as outfile:
        outfile.write(_different_bytes_of_the_same_length(original))

    # Then classifying it against the manifest -- which still carries the
    # md5 sum of the original bytes -- reports that it must be fetched
    verdict = proto.classify_resource_file(resource, resource, "data.txt")

    assert verdict.needs_download, (
        "a same-size rewrite went unnoticed, so the state kept an md5 sum "
        "that no longer describes the stored bytes")
