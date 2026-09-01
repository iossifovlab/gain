# pylint: disable=W0621,W0212
"""What a cache verdict spends on metadata for a file it keeps.

See gain#1039. Deciding whether a cached file needs re-downloading used
to open with an existence probe and then, whenever the recorded state
was missing or no longer described the stored file, rebuild that state
with a call that probed for existence all over again -- a second
question about a key the verdict had just been told was there. On a
remote store every probe is a HEAD, and a full GRR sync pays the surplus
once per stale-or-stateless cached file.

The budgets below count what is asked about the *stored file's* key.
What the ``.grr/<name>.state`` key costs is a separate account and not
pinned here.

The sibling module ``test_fsspec_protocol_download_single_stat`` makes
the same claim about the download path, over the same fixture and the
same recorder.
"""
from typing import Any

import pytest
from gain.genomic_resources.fsspec_protocol import (
    FileCacheVerdict,
    FsspecReadWriteProtocol,
)
from gain.genomic_resources.repository import (
    GenomicResource,
    ResourceFileState,
)

from .conftest import (
    METADATA_OPERATIONS,
    a_source_resource,
    assert_state_matches_accessors,
    calls_for,
    copy_one_resource,
    record_filesystem_calls,
)

#: The file of ``content_fixture``'s ``one`` resource these budgets are
#: asserted over. One file rather than all three: the budget is per file
#: and identical for each, and naming one keeps the arrangement -- the
#: state that has to be removed or spoiled -- readable.
CACHED_FILE = "data.txt"


def _forget_the_recorded_state(
    proto: FsspecReadWriteProtocol, resource: GenomicResource,
) -> None:
    """Remove :data:`CACHED_FILE`'s ``.state``, leaving the file itself."""
    proto.filesystem.rm(
        proto._get_resource_file_state_path(resource, CACHED_FILE))


@pytest.mark.grr_full
def test_a_stateless_cached_file_is_asked_about_twice(
    content_fixture: dict[str, Any],
    download_dest: FsspecReadWriteProtocol,
) -> None:
    """A verdict that has to rebuild a missing state costs one stat.

    One ``info()`` answers the question the verdict opens with -- is the
    file there -- and carries the size and the change token the rebuilt
    state needs, so the rebuild reads only what that dict cannot say:
    the modification time, and the md5 off the bytes themselves. That is
    the same budget the download path spends, and for the same reason.
    """
    # Given a cached resource whose file has lost its recorded state.
    dest_proto = download_dest
    src_resource = a_source_resource(content_fixture)
    dest_resource, _ = copy_one_resource(src_resource, dest_proto)
    _forget_the_recorded_state(dest_proto, dest_resource)

    # When the cache decides what to do about it.
    with record_filesystem_calls(dest_proto, METADATA_OPERATIONS) as calls:
        verdict = dest_proto.classify_resource_file(
            src_resource, dest_resource, CACHED_FILE)

    # Then it kept the file, and asked about it exactly twice.
    assert verdict == FileCacheVerdict(needs_download=False, size=0)
    url = dest_proto.get_resource_file_url(dest_resource, CACHED_FILE)
    assert sorted(calls_for(calls, url)) == ["info", "modified"]


@pytest.mark.grr_full
def test_a_stale_cached_state_costs_one_stat_to_rebuild(
    content_fixture: dict[str, Any],
    download_dest: FsspecReadWriteProtocol,
) -> None:
    """The other way into the rebuild must not reopen the second probe.

    Four calls, and which of them belongs to whom is the whole point:
    the verdict's opening stat, then the two
    ``_state_describes_stored_file`` spends deciding a token-less state
    is stale -- a modification time and a size, left as ADR 0022 has
    them -- and then the one modification time the rebuild still reads.
    The rebuild's own share is the same as when there was no state at
    all; a regression to probing for a file already stated shows up here
    as an ``exists`` among them.
    """
    # Given a cached file whose recorded state no longer describes it:
    # a state written before there were change tokens, recording a size
    # the stored file does not have.
    dest_proto = download_dest
    src_resource = a_source_resource(content_fixture)
    dest_resource, _ = copy_one_resource(src_resource, dest_proto)
    current = dest_proto.load_resource_file_state(dest_resource, CACHED_FILE)
    assert current is not None
    dest_proto.save_resource_file_state(
        dest_resource,
        ResourceFileState(
            filename=CACHED_FILE, size=current.size + 1,
            timestamp=current.timestamp, md5=current.md5,
            change_token=None))

    # When the cache decides what to do about it.
    with record_filesystem_calls(dest_proto, METADATA_OPERATIONS) as calls:
        verdict = dest_proto.classify_resource_file(
            src_resource, dest_resource, CACHED_FILE)

    # Then it kept the file, and asked about it four times.
    assert verdict == FileCacheVerdict(needs_download=False, size=0)
    url = dest_proto.get_resource_file_url(dest_resource, CACHED_FILE)
    assert sorted(calls_for(calls, url)) == [
        "info", "info", "modified", "modified"]


@pytest.mark.grr_full
def test_a_rebuilt_state_says_what_the_accessors_would_have_said(
    content_fixture: dict[str, Any],
    download_dest: FsspecReadWriteProtocol,
) -> None:
    """Carrying the fields across must not change any of them.

    The state the verdict rebuilds is what the *next* verdict is
    compared against, so a field that now comes from the opening stat
    rather than from its own call has to read identically. A drift would
    not fail here as a wrong value; it would show up in production as a
    file that re-downloads on every sync.
    """
    dest_proto = download_dest
    src_resource = a_source_resource(content_fixture)
    dest_resource, _ = copy_one_resource(src_resource, dest_proto)
    _forget_the_recorded_state(dest_proto, dest_resource)

    dest_proto.classify_resource_file(
        src_resource, dest_resource, CACHED_FILE)

    # Read every field back from the store rather than from a listing
    # left over from the verdict -- see the sibling download module for
    # why the two can disagree on s3.
    dest_proto.filesystem.invalidate_cache()
    assert_state_matches_accessors(dest_proto, dest_resource, CACHED_FILE)
