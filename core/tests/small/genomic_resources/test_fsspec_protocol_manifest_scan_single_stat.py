# pylint: disable=W0621
"""What a manifest build spends on metadata for each file it describes.

See gain#1084. The scan behind every ``grr_manage`` repair and verify
stats each file it lists once, and a file with no recorded state and no
``.dvc`` sidecar to answer for it has its state built from that stat's
size and change token. On a remote store every further stat is a HEAD.

The budget is the one the download (#936) and the cache verdict (#1039)
keep: one ``info()``, the scan's, and the one ``modified()`` the state
build reads, since the modification time is not taken out of the
``info()`` dict (see ``_StoredFileStat``).

A file that DOES have a current recorded state -- the steady state of a
repeat repair -- is judged on the same stat: its size and token are the
scan's, and only a store without tokens reads the modification time as
well (gain#1659). No bytes are read for it.
"""
import dataclasses
import os
import pathlib
from typing import Any
from unittest import mock

import pytest
from gain.genomic_resources.fsspec_protocol import FsspecReadWriteProtocol
from gain.genomic_resources.testing import build_filesystem_test_protocol

from .conftest import (
    CACHED_FILE,
    CURRENT_STATE_BUDGET,
    METADATA_OPERATIONS,
    STATE_OPERATIONS,
    a_source_resource,
    assert_state_matches_accessors,
    calls_for,
    copy_one_resource,
    forget_the_recorded_state,
    md5_of,
    record_filesystem_calls,
    size_of,
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

    The timestamp is compared too, though the scan does not supply it:
    on s3 the one a manifest build records used to come out of the
    scan's listing, a fraction of a second away from a later
    ``modified()`` (gain#1664).
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
        assert_state_matches_accessors(proto, resource, name)


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


@pytest.mark.grr_full
def test_a_file_with_a_current_state_is_stated_once_by_a_manifest_build(
    content_fixture: dict[str, Any],
    download_dest: FsspecReadWriteProtocol,
    grr_scheme: str,
) -> None:
    """A repeat repair trusts a current state on the scan's stat alone.

    The steady-state case -- almost every file of a repeat ``grr_manage``
    repair. Whether the recorded state still describes the file is asked
    of the size and token the scan already read, not of a second stat
    (gain#1659). The file's bytes are not opened: the state is trusted.
    """
    # Given a stored file whose recorded state still describes it.
    proto = download_dest
    resource, _ = copy_one_resource(
        a_source_resource(content_fixture), proto)

    # When the manifest is built.
    with record_filesystem_calls(proto, STATE_OPERATIONS) as calls:
        manifest = proto.build_manifest(resource)

    # Then the recorded md5 was taken, and the file asked about once.
    state = proto.load_resource_file_state(resource, CACHED_FILE)
    assert state is not None
    assert manifest[CACHED_FILE].md5 == state.md5
    url = proto.get_resource_file_url(resource, CACHED_FILE)
    assert sorted(calls_for(calls, url)) == CURRENT_STATE_BUDGET[grr_scheme]


@pytest.mark.grr_full
def test_a_file_rewritten_since_its_state_was_recorded_is_hashed_again(
    content_fixture: dict[str, Any],
    download_dest: FsspecReadWriteProtocol,
    grr_scheme: str,
) -> None:
    """The scan's size and token are compared, not merely carried.

    Judging a state on what the scan read must still notice a file
    that changed. On s3 the rewrite moves the token. On the local
    filesystem, which has none, the recorded modification time is put
    back, so the size the scan read is the only field left to give the
    rewrite away.
    """
    # Given a file rewritten, with a new size, after its state was recorded.
    proto = download_dest
    resource, _ = copy_one_resource(
        a_source_resource(content_fixture), proto)
    recorded = proto.load_resource_file_state(resource, CACHED_FILE)
    assert recorded is not None
    rewritten = "rewritten since the state was recorded\n"
    assert size_of(rewritten) != recorded.size
    with proto.open_raw_file(resource, CACHED_FILE, "wt") as outfile:
        outfile.write(rewritten)
    if grr_scheme == "file":
        url = proto.get_resource_file_url(resource, CACHED_FILE)
        os.utime(
            url.removeprefix("file://"),
            (recorded.timestamp, recorded.timestamp))

    # When the manifest is built.
    manifest = proto.build_manifest(resource)

    # Then the file was hashed again, not described by the old state.
    assert manifest[CACHED_FILE].md5 == md5_of(rewritten)
    assert manifest[CACHED_FILE].size == size_of(rewritten)


def test_a_token_bearing_state_on_a_tokenless_store_is_not_asked_again(
    content_fixture: dict[str, Any],
    tmp_path: pathlib.Path,
) -> None:
    """The scan's "no token" is an answer, not a reason to ask.

    A state can carry a token the store no longer offers -- one copied
    in from a store that had them. The scan has already asked and been
    told there is none, so the state is judged by size and modification
    time without a further stat to hear the same ``None`` again.
    """
    # Given a local file whose otherwise-current state records a token.
    proto = build_filesystem_test_protocol(tmp_path)
    resource, _ = copy_one_resource(
        a_source_resource(content_fixture), proto)
    recorded = proto.load_resource_file_state(resource, CACHED_FILE)
    assert recorded is not None
    proto.save_resource_file_state(
        resource, dataclasses.replace(recorded, change_token='"an-etag"'))

    # When the manifest is built.
    with record_filesystem_calls(proto, STATE_OPERATIONS) as calls:
        manifest = proto.build_manifest(resource)

    # Then the state was trusted, on the scan's stat and one mtime.
    assert manifest[CACHED_FILE].md5 == recorded.md5
    url = proto.get_resource_file_url(resource, CACHED_FILE)
    assert sorted(calls_for(calls, url)) == CURRENT_STATE_BUDGET["file"]
