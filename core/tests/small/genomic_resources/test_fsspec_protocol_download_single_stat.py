# pylint: disable=W0621,C0114,C0116
"""What a download spends on metadata for the file it just published.

See gain#936. The state of a downloaded file used to be assembled one
field at a time, each field its own call against the same key: an
existence probe, a modification time, a change token, on top of the size
stat that verifies the move. On a remote store every one of those is a
HEAD, and a full GRR sync pays the surplus once per published file.
"""
from typing import Any

import pytest
from gain.genomic_resources.fsspec_protocol import FsspecReadWriteProtocol
from gain.genomic_resources.testing.faulty_filesystem import (
    corrupt_same_length,
)

# ``download_dest`` comes from the package conftest, beside the pin below;
# ``test_fsspec_protocol_download_md5_reuse.py`` makes the sibling claim
# about the md5 over the same fixture.
from .conftest import (
    METADATA_OPERATIONS,
    a_source_resource,
    assert_published_one,
    assert_state_matches_accessors,
    calls_for,
    copy_one_resource,
    record_filesystem_calls,
)


@pytest.mark.grr_full
def test_a_downloaded_file_is_asked_about_twice(
    content_fixture: dict[str, Any],
    download_dest: FsspecReadWriteProtocol,
) -> None:
    """Downloading a file costs one stat and one modification time.

    One ``info()`` carries the published size and the change token
    together, and the size half of it is a stat the move verification
    makes anyway. The modification time is the one field ``info()``
    cannot be trusted for -- on the memory filesystem the dict's
    ``created`` and what ``modified()`` reports are different values --
    so it stays its own call, and that is the whole budget.
    """
    # Given a source resource and a destination that does not have it yet.
    src_resource = a_source_resource(content_fixture)

    dest_proto = download_dest
    assert dest_proto.get_all_resources_dict() == {}

    # When
    with record_filesystem_calls(dest_proto, METADATA_OPERATIONS) as calls:
        dest_resource = dest_proto.copy_resource(src_resource)

    # Then every published file was asked about exactly twice.
    for name in assert_published_one(dest_proto, dest_resource):
        url = dest_proto.get_resource_file_url(dest_resource, name)
        spent = sorted(calls_for(calls, url))
        assert spent == ["info", "modified"], (name, spent)


@pytest.mark.grr_full
def test_a_downloaded_state_says_what_the_accessors_would_have_said(
    content_fixture: dict[str, Any],
    download_dest: FsspecReadWriteProtocol,
) -> None:
    """Carrying the fields across must not change any of them.

    The saved state is what every later cache verdict is compared
    against, so a field that now comes from somewhere else has to read
    identically -- the timestamp to its rounding, the change token to the
    ``None`` a store without one reports. A drift in any of them would
    not fail here as a wrong value; it would show up in production as a
    file that re-downloads on every sync.
    """
    dest_proto = download_dest
    dest_resource, names = copy_one_resource(
        a_source_resource(content_fixture), dest_proto)

    # Read every field back from the store rather than from a listing
    # left over from the copy. On s3 the modification time is reported to
    # the whole second by ``head_object`` and to the millisecond by
    # ``list_objects_v2`` -- the split ``test_fsspec_protocol_change_token``
    # opens on -- so which of the two answered decides the value, and
    # comparing across the two compares s3fs's caching rather than these
    # fields. That caching is out of scope here.
    dest_proto.filesystem.invalidate_cache()

    for name in names:
        assert_state_matches_accessors(dest_proto, dest_resource, name)


@pytest.mark.grr_full
def test_a_downloaded_state_still_judges_a_rewrite(
    content_fixture: dict[str, Any],
    download_dest: FsspecReadWriteProtocol,
    grr_scheme: str,
) -> None:
    """The state a download writes must still decide the next verdict.

    The point of recording the token from the move's own stat rather than
    from a stat of its own is that it is the same token; if it were not,
    an untouched file would read as changed. Both halves are asserted,
    because a token that never matches and one that always matches are
    each half of this test passing on its own.
    """
    dest_proto = download_dest
    dest_resource, _ = copy_one_resource(
        a_source_resource(content_fixture), dest_proto)

    state = dest_proto.load_resource_file_state(dest_resource, "data.txt")
    assert state is not None
    if grr_scheme == "s3":
        assert state.change_token is not None, \
            "s3 reports an ETag; this test is vacuous without one"

    assert dest_proto._state_describes_stored_file(dest_resource, state)

    if state.change_token is None:
        # Nothing further is decidable here. A store with no token falls
        # back to the modification time and the size, and the recorded
        # time is rounded to a hundredth of a second, so a same-length
        # rewrite this soon after the download lands in the same value.
        # That limit is the recorded resolution's, not this change's --
        # ``test_fsspec_protocol_change_token`` opens on it -- and the
        # half such a store can answer is asserted above. Skipped rather
        # than returned so the run says which half it got.
        pytest.skip("store reports no change token; the rewrite is undecidable")

    with dest_proto.open_raw_file(dest_resource, "data.txt", "rt") as infile:
        original = str(infile.read())
    with dest_proto.open_raw_file(dest_resource, "data.txt", "wt") as outfile:
        outfile.write(corrupt_same_length(original))

    assert not dest_proto._state_describes_stored_file(dest_resource, state)
