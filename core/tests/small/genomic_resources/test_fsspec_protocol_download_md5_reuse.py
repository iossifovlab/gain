# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pathlib
from typing import Any

from gain.genomic_resources.fsspec_protocol import FsspecReadWriteProtocol
from gain.genomic_resources.testing import (
    build_filesystem_test_protocol,
    build_inmemory_test_protocol,
)
from pytest_mock import MockerFixture


def _empty_dest_proto(
    tmp_path: pathlib.Path,
) -> FsspecReadWriteProtocol:
    """Return a read-write filesystem protocol over an empty directory."""
    dest_root = tmp_path / "dest"
    dest_root.mkdir()
    return build_filesystem_test_protocol(dest_root)


def test_copy_resource_reuses_the_verified_download_md5(
    content_fixture: dict[str, Any],
    tmp_path: pathlib.Path,
    mocker: MockerFixture,
) -> None:
    """Copying must not re-read the files it just downloaded and verified.

    The download already hashes every byte it writes and checks that digest
    against the manifest before publishing the file. Recomputing it from the
    stored object is a second full read of every file -- a second transfer
    when the destination is remote. See gain#865.
    """
    # Given a source resource and a destination that does not have it yet
    src_proto = build_inmemory_test_protocol(content_fixture)
    src_resource = src_proto.get_resource("one")
    expected_files = sorted(
        entry.name for entry in src_resource.get_manifest())
    assert expected_files == [
        "data.txt", "data.txt.gz", "genomic_resource.yaml"], \
        "fixture changed; the count below is no longer meaningful"

    dest_proto = _empty_dest_proto(tmp_path)
    assert dest_proto.get_all_resources_dict() == {}

    recompute = mocker.spy(dest_proto, "compute_md5_sum")

    # When
    dest_resource = dest_proto.copy_resource(src_resource)

    # Then the copy really happened...
    dest_manifest = dest_proto.get_manifest(dest_resource)
    assert sorted(entry.name for entry in dest_manifest) == expected_files
    for entry in dest_manifest:
        assert dest_proto.file_exists(dest_resource, entry.name)
        state = dest_proto.load_resource_file_state(
            dest_resource, entry.name)
        assert state is not None, entry.name
        assert state.md5 == entry.md5, entry.name

    # ...and not one byte of it was read back to recompute a known digest
    assert recompute.call_count == 0, (
        f"destination files re-read to recompute md5: "
        f"{[call.args[1] for call in recompute.call_args_list]}")
