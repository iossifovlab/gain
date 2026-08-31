# pylint: disable=W0621,C0114,C0116,W0212
"""What a download spends probing the directories it writes into.

See gain#1042. Three sites used to create a containing directory with
``if not exists(d): makedirs(d, exist_ok=True)``. On an object store the
guard is the expensive half: ``exists()`` of a *prefix* costs a
``head_object`` plus a ``list_objects_v2``, while the ``makedirs`` it
guards costs one ``head_bucket`` and is a no-op once the directory is
there. So the guard paid two requests forever to save one, and a full
GRR sync paid it once per published file.

``_publish_file`` never had the guard, and its comment makes exactly
this argument; this pins that the other three now agree with it.
"""
import os
from typing import Any

import pytest
from gain.genomic_resources.fsspec_protocol import FsspecReadWriteProtocol
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing import build_inmemory_test_protocol

from .conftest import ONE_RESOURCE_FILES, record_filesystem_calls

#: Asking whether a directory is there. The probe this change removes.
PROBE_OPERATIONS = ("exists",)

#: Making a directory. All three sites now spell it ``makedirs``, but
#: ``mkdir`` is counted too: it is the spelling one of them used before
#: gain#1042, and counting only the current one would let a site that
#: went back to it read as no creation at all rather than as a failure.
CREATE_OPERATIONS = ("mkdir", "makedirs")


def _guarded_directories(
    proto: FsspecReadWriteProtocol,
    resource: GenomicResource,
    filename: str,
) -> tuple[str, str]:
    """The two directories the three sites create, for one file.

    Derived from the protocol's own path helpers rather than spelled out,
    so a change to the internal layout moves this with it instead of
    quietly making the assertions below address nothing.

    ``copy_resource_file`` creates the parent of the published file --
    the resource directory. ``_download_resource_file`` creates the
    parent of the temp download path and ``save_resource_file_state``
    the parent of the state document; both of those are the resource's
    internal ``.grr`` directory, which is why the counts differ below.
    """
    resource_directory = os.path.dirname(
        proto.get_resource_file_url(resource, filename))
    internal_directory = os.path.dirname(
        proto._get_resource_file_state_path(resource, filename))
    assert internal_directory == os.path.dirname(
        proto._get_resource_file_download_path(resource, filename)), \
        "temp and state no longer share a parent; the counts below split"
    return resource_directory, internal_directory


def _count(
    calls: list[tuple[str, str]],
    operations: tuple[str, ...],
    directory: str,
) -> int:
    """How many of ``operations`` were asked about ``directory``."""
    return sum(
        1 for operation, path in calls
        if path == directory and operation in operations)


@pytest.mark.grr_rw
def test_a_download_never_asks_whether_a_directory_is_there(
    content_fixture: dict[str, Any],
    download_dest: FsspecReadWriteProtocol,
) -> None:
    """The directories a download writes into are made, never probed.

    Both halves are asserted, and neither is worth much alone. Zero
    probes on its own would also be what a download that stopped
    creating the directories looked like, right up until the write
    failed; the create counts on their own would not notice the guard
    coming back. Together they say the guard is gone and the
    unconditional call took its place.
    """
    # Given a source resource and a destination that does not have it yet.
    src_proto = build_inmemory_test_protocol(content_fixture)
    src_resource = src_proto.get_resource("one")

    dest_proto = download_dest
    assert dest_proto.get_all_resources_dict() == {}

    # When
    with record_filesystem_calls(
            dest_proto, PROBE_OPERATIONS + CREATE_OPERATIONS) as calls:
        dest_resource = dest_proto.copy_resource(src_resource)

    # Then
    published = sorted(
        entry.name for entry in dest_proto.get_manifest(dest_resource))
    assert published == ONE_RESOURCE_FILES, "fixture changed; update this pin"

    resource_directory, internal_directory = _guarded_directories(
        dest_proto, dest_resource, published[0])

    assert _count(calls, PROBE_OPERATIONS, resource_directory) == 0
    assert _count(calls, PROBE_OPERATIONS, internal_directory) == 0

    # One per published file: ``copy_resource_file`` makes the resource
    # directory once for each file it copies in.
    assert _count(calls, CREATE_OPERATIONS, resource_directory) == \
        len(published)

    # Two per published file -- the temp download parent and the state
    # parent -- plus one more that belongs to no file: publishing the
    # manifest at the end of the copy goes through ``_publish_file``,
    # whose own unconditional ``makedirs`` lands on the same directory.
    assert _count(calls, CREATE_OPERATIONS, internal_directory) == \
        2 * len(published) + 1


@pytest.mark.grr_rw
def test_a_download_creates_a_resource_directory_that_is_not_there(
    content_fixture: dict[str, Any],
    download_dest: FsspecReadWriteProtocol,
) -> None:
    """Dropping the probe must not drop the directory creation with it.

    Run on every read-write scheme, but the three do not each prove the
    same thing, and it is worth being exact about which proves what --
    measured by removing the production calls one at a time:

    * ``file`` is the only scheme where the creation is load-bearing.
      ``LocalFileSystem`` is built with ``auto_mkdir=False``, so a write
      into a directory that is not there raises, and this test goes red.
    * ``inmemory`` and ``s3`` would stay green if the creation vanished
      -- the memory backend keys a flat dict and only rejects a *file*
      ancestor, and an s3 PUT needs no prefix object. What they catch is
      the other way the change can go wrong: creating the directory with
      ``mkdir`` rather than ``makedirs``, which raises ``FileExistsError``
      on the memory backend from the second file onwards. s3, having no
      real directories, cannot fail either way; it is here so that the
      scheme the production code actually runs against is exercised.

    Both states of the directory are covered by copying a resource of
    more than one file: the first file meets a directory that is not
    there, and every file after it meets one that is. That second state
    is the one the ``mkdir`` spelling breaks.
    """
    # Given a source resource and a destination with nothing in it.
    src_proto = build_inmemory_test_protocol(content_fixture)
    src_resource = src_proto.get_resource("one")

    dest_proto = download_dest
    assert dest_proto.get_all_resources_dict() == {}
    assert len(ONE_RESOURCE_FILES) > 1, \
        "a single-file resource would not reach the already-there case"

    # When
    dest_resource = dest_proto.copy_resource(src_resource)

    # Then the resource arrived whole, contents and all -- which it could
    # not have without its directory.
    published = sorted(
        entry.name for entry in dest_proto.get_manifest(dest_resource))
    assert published == ONE_RESOURCE_FILES, "fixture changed; update this pin"

    for name in published:
        with src_resource.open_raw_file(name, "rb", uncompress=False) as src, \
                dest_resource.open_raw_file(
                    name, "rb", uncompress=False) as dest:
            assert dest.read() == src.read(), name
