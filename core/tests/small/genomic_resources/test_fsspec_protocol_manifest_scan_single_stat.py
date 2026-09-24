# pylint: disable=W0621
"""What a manifest build spends on metadata for a file it has to hash.

See gain#1084. The scan behind every ``grr_manage`` repair and verify
stats each file it lists once, and a file with no recorded state and no
``.dvc`` sidecar to answer for it has its state built from that stat's
size and change token. On a remote store every further stat is a HEAD.

The budget is the one the download (#936) and the cache verdict (#1039)
keep: one ``info()``, the scan's, and the one ``modified()`` the state
build reads, since the modification time is not taken out of the
``info()`` dict (see ``_StoredFileStat``).

A file that DOES have a current recorded state is a separate budget,
tracked in gain#1659.
"""
from typing import Any
from unittest import mock

import pytest
from gain.genomic_resources.fsspec_protocol import FsspecReadWriteProtocol

from .conftest import (
    CACHED_FILE,
    METADATA_OPERATIONS,
    a_source_resource,
    assert_state_matches_accessors,
    calls_for,
    copy_one_resource,
    forget_the_recorded_state,
    record_filesystem_calls,
)


@pytest.mark.grr_full
@pytest.mark.parametrize("verify_content", [False, True])
def test_a_hashed_file_is_stated_once_by_a_manifest_build(
    content_fixture: dict[str, Any],
    download_dest: FsspecReadWriteProtocol,
    verify_content: bool,
) -> None:
    """The scan's stat carries the size and token the state needs.

    What is left for the state build to read is what that stat cannot
    say: the modification time, and the md5 off the bytes themselves (an
    ``open``, which this budget does not count). The same holds for the
    verifier (``grr_manage --without-dvc``), which hashes every file
    whatever its recorded state.
    """
    # Given a stored file with no recorded state and no sidecar.
    proto = download_dest
    resource, _ = copy_one_resource(
        a_source_resource(content_fixture), proto)
    forget_the_recorded_state(proto, resource)

    # When the manifest is built.
    with record_filesystem_calls(proto, METADATA_OPERATIONS) as calls:
        manifest = proto.build_manifest(
            resource, verify_content=verify_content)

    # Then the file was hashed, and asked about exactly twice.
    assert manifest[CACHED_FILE].md5 is not None
    url = proto.get_resource_file_url(resource, CACHED_FILE)
    assert sorted(calls_for(calls, url)) == ["info", "modified"]


@pytest.mark.grr_rw
def test_a_state_built_from_the_scan_says_what_the_accessors_say(
    content_fixture: dict[str, Any],
    download_dest: FsspecReadWriteProtocol,
) -> None:
    """Taking the size and token from the scan must not change either.

    The state is what the next repair and the next cache verdict are
    compared against, so a field carried across from the scan has to
    read exactly as its own accessor would -- including the ``None`` a
    store without tokens reports.

    The timestamp is not compared: the scan does not supply it, and on
    s3 the one a manifest build records can differ from a later
    ``modified()`` by a fraction of a second (gain#1664).
    """
    # Given stored files with no recorded state and no sidecar.
    proto = download_dest
    resource, names = copy_one_resource(
        a_source_resource(content_fixture), proto)
    for name in names:
        forget_the_recorded_state(proto, resource, name)

    # When the manifest is built.
    proto.build_manifest(resource)

    # Then every state it recorded reads as the accessors do.
    for name in names:
        assert_state_matches_accessors(
            proto, resource, name, compare_timestamp=False)


@pytest.mark.grr_rw
def test_a_file_gone_before_its_state_is_built_names_itself(
    content_fixture: dict[str, Any],
    download_dest: FsspecReadWriteProtocol,
) -> None:
    """A file removed after the scan is refused by name, not by a stat.

    The scan's stat is the last time the file is asked about before its
    bytes are read, so it is the read that meets a file that has since
    gone -- and the refusal still names the resource and the file.
    """
    # Given a stateless file that disappears once the scan has listed it.
    proto = download_dest
    resource, _ = copy_one_resource(
        a_source_resource(content_fixture), proto)
    forget_the_recorded_state(proto, resource)
    scan = proto.scan_resource_entries(resource)
    proto.filesystem.rm(proto.get_resource_file_url(resource, CACHED_FILE))

    # When the manifest is built from that scan.
    with (
        mock.patch.object(proto, "scan_resource_entries", return_value=scan),
        pytest.raises(
            ValueError, match="can't build resource state") as refusal,
    ):
        proto.build_manifest(resource)

    # Then the refusal names the resource and the file.
    assert f"{resource.resource_id} > {CACHED_FILE}" in str(refusal.value)
