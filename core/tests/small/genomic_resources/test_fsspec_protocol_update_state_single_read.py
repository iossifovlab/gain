# pylint: disable=W0621,W0212
"""What updating a kept file spends on its ``.state`` key.

See gain#1083. ``update_resource_file`` hands back the state of a file it
keeps. That state is the one its verdict has just loaded, or rebuilt and
saved, so handing it back must not ask the store for the ``.state`` key a
second time -- on a remote store each ask is a HEAD and a GET, paid once
per kept file of a full sync.

The budget below counts what is asked about the ``.state`` key only. What
the stored file's own key costs is pinned by the sibling module
``test_fsspec_protocol_classify_single_stat``, over the same fixture and
the same recorder.
"""
from collections.abc import Callable
from typing import Any

import pytest
from gain.genomic_resources.fsspec_protocol import FsspecReadWriteProtocol
from gain.genomic_resources.repository import GenomicResource

from .conftest import (
    CACHED_FILE,
    METADATA_OPERATIONS,
    a_source_resource,
    calls_for,
    copy_one_resource,
    forget_the_recorded_state,
    record_filesystem_calls,
)

#: What reading or writing the ``.state`` key costs: the metadata a
#: probe asks for, plus the ``open`` that moves its bytes.
STATE_OPERATIONS = (*METADATA_OPERATIONS, "open")


def _keep_the_recorded_state(
    proto: FsspecReadWriteProtocol, resource: GenomicResource,
) -> None:
    """Leave :data:`CACHED_FILE`'s ``.state`` as the copy recorded it."""


@pytest.mark.grr_full
@pytest.mark.parametrize("arrange", [
    # The verdict loads the state once and hands it back.
    pytest.param(_keep_the_recorded_state, id="current-state"),
    # The verdict probes, finds nothing, and its one open writes the
    # rebuild -- which is handed back, not read back.
    pytest.param(forget_the_recorded_state, id="rebuilt-state"),
])
def test_a_kept_file_has_its_state_asked_about_once(
    content_fixture: dict[str, Any],
    download_dest: FsspecReadWriteProtocol,
    arrange: Callable[[FsspecReadWriteProtocol, GenomicResource], None],
) -> None:
    """The ``.state`` key costs one probe and one open, by the verdict."""
    # Given a cached resource, its state arranged.
    dest_proto = download_dest
    src_resource = a_source_resource(content_fixture)
    dest_resource, _ = copy_one_resource(src_resource, dest_proto)
    arrange(dest_proto, dest_resource)
    state_path = dest_proto._get_resource_file_state_path(
        dest_resource, CACHED_FILE)

    # When the cache updates it.
    with record_filesystem_calls(dest_proto, STATE_OPERATIONS) as calls:
        state = dest_proto.update_resource_file(
            src_resource, dest_resource, CACHED_FILE)

    # Then it kept the file, and asked about its state once.
    assert state is not None
    assert calls_for(calls, state_path) == ["exists", "open"]
