# pylint: disable=W0621,C0114,C0116
import pathlib
from typing import Any

from gain.genomic_resources.testing import (
    build_filesystem_test_protocol,
    build_inmemory_test_protocol,
)
from pytest_mock import MockerFixture

# The same pin ``test_fsspec_protocol_download_single_stat.py`` asserts, which
# makes the sibling claim about the stat rather than the md5.
from .conftest import ONE_RESOURCE_FILES


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
    # Given a source resource and a destination that does not have it yet.
    # The manifest must be non-empty, or the zero asserted at the end is
    # vacuous: an empty resource downloads nothing and so trivially
    # recomputes nothing.
    src_proto = build_inmemory_test_protocol(content_fixture)
    src_resource = src_proto.get_resource("one")
    expected_files = sorted(
        entry.name for entry in src_resource.get_manifest())
    assert expected_files == ONE_RESOURCE_FILES, \
        "fixture changed; update this pin"

    dest_root = tmp_path / "dest"
    dest_root.mkdir()
    dest_proto = build_filesystem_test_protocol(dest_root)
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
