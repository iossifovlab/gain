# pylint: disable=W0621
"""A stored file's timestamp is one value, whatever was asked before it.

On s3 a listing and a HEAD report it at different precisions, so the
protocol always reads it with a HEAD -- see ``_get_filepath_timestamp``,
gain#1664 and ADR 0022.
"""
from typing import Any

import pytest
from gain.genomic_resources.fsspec_protocol import FsspecReadWriteProtocol

from .conftest import (
    CACHED_FILE,
    a_source_resource,
    copy_one_resource,
    record_filesystem_calls,
)


@pytest.mark.grr_rw
def test_a_timestamp_does_not_depend_on_a_listing_made_before_it(
    content_fixture: dict[str, Any],
    download_dest: FsspecReadWriteProtocol,
) -> None:
    """The scan's listing must not change what the timestamp reads.

    That the scan lists is asserted rather than assumed: a scan answered
    some other way would leave nothing cached to disagree with, and the
    comparison would pass whatever the timestamp read.
    """
    # Given a stored file whose timestamp is read with nothing cached.
    proto = download_dest
    resource, _ = copy_one_resource(
        a_source_resource(content_fixture), proto)
    proto.filesystem.invalidate_cache()
    unlisted = proto.get_resource_file_timestamp(resource, CACHED_FILE)

    # When the resource is scanned, which lists its directory.
    with record_filesystem_calls(proto, ("ls",)) as calls:
        proto.scan_resource_entries(resource)
    assert calls, "the scan did not list the resource"

    # Then the timestamp reads as it did before the listing.
    assert proto.get_resource_file_timestamp(
        resource, CACHED_FILE) == unlisted
