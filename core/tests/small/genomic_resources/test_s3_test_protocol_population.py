# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""How ``copy_proto_genomic_resources`` populates an s3 test protocol.

The s3 population is the single most expensive thing the core suite does
under ``--enable-s3-testing``: it ran once per ``[s3]``-parametrized test
and, file by file through the protocol, cost 246 s3 round-trips a time
(gain#862).  Populating in bulk is only allowed to change how many round
trips that takes -- never what ends up in the bucket -- so the repository
it produces is pinned here against the ``file`` scheme, which publishes
the same source through the same helper and is not touched by the bulk
path.
"""
import pathlib
from typing import Any

import pytest
from aiobotocore.client import AioBaseClient
from gain.genomic_resources.fsspec_protocol import (
    FsspecReadWriteProtocol,
)
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GR_CONTENTS_FILE_NAME,
    GR_MANIFEST_FILE_NAME,
)
from gain.genomic_resources.testing import (
    build_filesystem_test_protocol,
    copy_proto_genomic_resources,
    s3_test_protocol,
    setup_directories,
)

STATE_SUFFIX = ".state"


@pytest.fixture
def s3_enabled(request: pytest.FixtureRequest) -> None:
    """Skip unless the run was started with ``--enable-s3-testing``.

    These tests speak to the s3 protocol directly rather than through the
    ``grr_scheme`` parametrization -- what they cover is the population
    itself, not a behaviour that every scheme shares -- so they need the
    same gate the parametrization applies, applied by hand.
    """
    if not request.config.getoption("enable_s3"):
        pytest.skip("S3 testing not enabled")


@pytest.fixture
def source_proto(
    tmp_path: pathlib.Path,
    content_fixture: dict[str, Any],
) -> FsspecReadWriteProtocol:
    """A filesystem GRR to publish -- the shape the hot fixture populates.

    ``content_fixture`` rather than a builder on purpose: it is the exact
    content the ``fsspec_proto`` fixture pushes to s3 on every ``[s3]``
    test, and it carries the shapes that make population non-trivial --
    versioned resource ids, nested subdirectories and a gzipped file.
    """
    source_root = tmp_path / "source"
    setup_directories(source_root, content_fixture)
    return build_filesystem_test_protocol(source_root)


@pytest.fixture
def s3_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, str]]:
    """Record the s3 API calls made, as ``(operation, key)`` pairs.

    Recorded at the client rather than inside s3fs: s3fs reaches the
    client by more than one route -- a filesystem operation goes through
    ``S3FileSystem._call_s3`` but writing an open file goes through
    ``S3File._call_s3`` and the synchronous wrapper -- and a record that
    saw only one of them would miss exactly the uploads this is about.
    Every route ends in one ``_make_api_call``, which is one round trip.
    """
    calls: list[tuple[str, str]] = []
    make_api_call = AioBaseClient._make_api_call

    async def recording_make_api_call(
        self: AioBaseClient, operation_name: str, api_params: Any,
    ) -> Any:
        calls.append((operation_name, (api_params or {}).get("Key", "")))
        return await make_api_call(self, operation_name, api_params)

    monkeypatch.setattr(
        AioBaseClient, "_make_api_call", recording_make_api_call)
    return calls


def keys_of(calls: list[tuple[str, str]], operation: str) -> list[str]:
    return [key for name, key in calls if name == operation]


def filesystem_tree(root: pathlib.Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def s3_tree(proto: FsspecReadWriteProtocol) -> dict[str, bytes]:
    filesystem = proto.filesystem
    filesystem.invalidate_cache()
    prefix = proto.url.removeprefix("s3://").rstrip("/")
    return {
        path[len(prefix) + 1:]: filesystem.cat_file(path)
        for path in sorted(filesystem.find(prefix))
    }


def without_states(tree: dict[str, bytes]) -> dict[str, bytes]:
    return {
        name: content for name, content in tree.items()
        if not name.endswith(STATE_SUFFIX)
    }


def test_the_s3_repository_holds_what_the_filesystem_one_holds(
    s3_enabled: None,
    tmp_path: pathlib.Path,
    source_proto: FsspecReadWriteProtocol,
) -> None:
    """Publishing to s3 and to a directory must produce the same repository.

    Byte-identical, and that includes the derived files -- every
    ``.MANIFEST`` and the gzipped ``.CONTENTS.json.gz`` index -- so a
    population that dropped a resource, reordered the index or rewrote a
    manifest could not pass.  The ``.grr/*.state`` files are excluded
    because they record each file's modification time, which is a
    property of the store, not of the repository; they get their own test.
    """
    # Given a directory GRR published to a directory the ordinary way
    reference_root = tmp_path / "reference"
    reference_root.mkdir()
    reference = build_filesystem_test_protocol(reference_root)
    copy_proto_genomic_resources(reference, source_proto)

    # When the same source is published to a fresh s3 prefix
    s3_proto = s3_test_protocol()
    copy_proto_genomic_resources(s3_proto, source_proto)

    # Then the two repositories hold the same files, byte for byte
    assert without_states(s3_tree(s3_proto)) == \
        without_states(filesystem_tree(reference_root))


def test_a_freshly_published_s3_repository_does_not_look_stale(
    s3_enabled: None,
    source_proto: FsspecReadWriteProtocol,
    s3_calls: list[tuple[str, str]],
) -> None:
    """A just-published repository must not read as already drifted.

    ``classify_resource_file`` compares each ``.state`` against the object
    it describes and, on any difference in size or modification time,
    re-hashes the file and rewrites the state.  So a population whose
    states do not agree with what the protocol reads back leaves every
    resource looking stale: the first use of a fixture repository pays for
    a re-hash of every file and writes into a repository the test only
    meant to read.

    Read back the way a test reads it -- enumerate, then look -- with no
    cache handling of its own, because that is what decides the answer
    here.  ``modified()`` is served from whatever call last filled the
    s3fs listing cache, and MinIO reports ``LastModified`` to the
    millisecond on ``list_objects_v2`` where ``head_object`` reports
    whole seconds.  A test that dropped the cache first would read a HEAD,
    match whatever the population recorded, and never notice.
    """
    # Given a freshly published s3 repository...
    proto = s3_test_protocol()
    s3_calls.clear()
    copy_proto_genomic_resources(proto, source_proto)
    # ...published in bulk, and not by the file-by-file fallback, whose
    # states this would otherwise be re-testing
    assert keys_of(s3_calls, "CopyObject") == []

    # When it is enumerated and its states compared with the stored objects
    drifted = []
    checked = 0
    for resource in proto.get_all_resources():
        for entry in resource.get_manifest():
            state = proto.load_resource_file_state(resource, entry.name)
            assert state is not None, (resource.resource_id, entry.name)
            checked += 1
            stored = (
                proto.get_resource_file_timestamp(resource, entry.name),
                proto.get_resource_file_size(resource, entry.name),
                # hashed from the stored bytes, not read off the manifest
                # the state's md5 was copied from
                proto.compute_md5_sum(resource, entry.name),
            )
            if (state.timestamp, state.size, state.md5) != stored:
                drifted.append(
                    (f"{resource.get_full_id()}/{entry.name}",
                     (state.timestamp, state.size, state.md5), stored))

    # Then nothing has drifted
    assert checked == 12, "the content fixture publishes twelve files"
    assert not drifted, drifted


def test_populating_a_fresh_s3_repository_only_uploads(
    s3_enabled: None,
    source_proto: FsspecReadWriteProtocol,
    s3_calls: list[tuple[str, str]],
) -> None:
    """Populating a fresh prefix must be uploads and nothing else.

    The per-file protocol copy this replaces re-read every file it had
    just written (to checksum it) and published each one by copying it
    within the bucket and deleting the staged object -- 246 round trips
    for the twelve files of the content fixture, paid once per
    ``[s3]``-parametrized test (gain#862).  Nothing about a freshly
    prepared repository needs reading back: it was assembled locally, so
    each object is written exactly once and never read.
    """
    # Given a fresh, empty s3 prefix
    proto = s3_test_protocol()
    s3_calls.clear()

    # When a repository is published into it
    copy_proto_genomic_resources(proto, source_proto)
    during_population = list(s3_calls)

    # Then every stored object was uploaded exactly once...
    # (an object's Key is its path within the bucket, which the request
    # names separately, so the bucket comes off the front of the url)
    _, key_prefix = proto.url.removeprefix("s3://").rstrip("/").split("/", 1)
    stored = s3_tree(proto)
    assert sorted(keys_of(during_population, "PutObject")) == \
        sorted(f"{key_prefix}/{name}" for name in stored)

    # ...no resource file's bytes were read back out of the store.  The
    # repository's own bookkeeping is read -- manifests, resource configs
    # and the index -- to rebuild the resource memo that the file-by-file
    # copy also leaves warm; the files themselves are never fetched.
    bookkeeping = (
        GR_MANIFEST_FILE_NAME, GR_CONTENTS_FILE_NAME, GR_CONF_FILE_NAME)
    assert [
        key for key in keys_of(during_population, "GetObject")
        if not key.endswith(bookkeeping)
    ] == []

    # ...and nothing was published by a move within the bucket
    assert keys_of(during_population, "CopyObject") == []


def test_republishing_a_shrunken_source_drops_the_removed_file(
    s3_enabled: None,
    tmp_path: pathlib.Path,
) -> None:
    """Publishing over a populated repository must still remove files.

    A bulk upload can only add and overwrite, so it cannot be what
    publishes into a repository that already holds resources: a file that
    had left the source's manifest since would survive in the store and
    keep being served.  Only a fresh prefix is populated in bulk;
    anything else goes resource by resource, which deletes what the
    manifest no longer lists.
    """
    # Given a repository published from a source carrying two data files
    full_root = tmp_path / "full"
    setup_directories(full_root, {
        "one": {
            GR_CONF_FILE_NAME: "",
            "keep.txt": "keep",
            "drop.txt": "drop",
        },
    })
    proto = s3_test_protocol()
    copy_proto_genomic_resources(
        proto, build_filesystem_test_protocol(full_root))
    assert "one/drop.txt" in s3_tree(proto)

    # When the same resource is published again from a source without one
    shrunken_root = tmp_path / "shrunken"
    setup_directories(shrunken_root, {
        "one": {
            GR_CONF_FILE_NAME: "",
            "keep.txt": "keep",
        },
    })
    copy_proto_genomic_resources(
        proto, build_filesystem_test_protocol(shrunken_root))

    # Then the dropped file is gone from the repository
    tree = s3_tree(proto)
    assert "one/keep.txt" in tree
    assert "one/drop.txt" not in tree


def test_a_protocol_looked_at_before_publishing_sees_the_resources(
    s3_enabled: None,
    source_proto: FsspecReadWriteProtocol,
) -> None:
    """A destination enumerated before the publish must not stay empty.

    The protocol memoizes its resource list, and a caller that looks at a
    freshly built protocol before publishing into it -- reasonable enough,
    and what an assertion about the starting state would do -- caches the
    empty answer.  Populating has to invalidate that memo, or the
    repository is fully published on s3 and permanently empty in process:
    an empty repository, not an error, so nothing would point at the
    population.
    """
    # Given a destination that has already been asked what it holds
    proto = s3_test_protocol()
    assert not list(proto.get_all_resources())

    # When a repository is published into it
    copy_proto_genomic_resources(proto, source_proto)

    # Then it reports what was published
    assert sorted(res.get_full_id() for res in proto.get_all_resources()) == [
        "one", "sub/two", "sub/two(1.0)", "three(2.0)", "xxxxx-genome",
    ]


def test_a_resource_file_named_like_a_state_document_is_published(
    s3_enabled: None,
    tmp_path: pathlib.Path,
) -> None:
    """``.state`` names a location, not a suffix.

    The protocol's own state documents live under a resource's ``.grr``
    directory; a resource is free to carry a data file whose name happens
    to end the same way.  Skipping the upload by suffix would quietly
    leave that file out of the repository on s3 and nowhere else.
    """
    # Given a resource whose data file is named like a state document
    root = tmp_path / "source"
    setup_directories(root, {
        "one": {
            GR_CONF_FILE_NAME: "",
            "checkpoint.state": "not a state document",
        },
    })

    # When it is published to a fresh s3 prefix
    proto = s3_test_protocol()
    copy_proto_genomic_resources(
        proto, build_filesystem_test_protocol(root))

    # Then the file is in the repository, with its own state beside it
    tree = s3_tree(proto)
    assert tree["one/checkpoint.state"] == b"not a state document"
    assert "one/.grr/checkpoint.state.state" in tree


def test_classify_does_not_re_hash_a_listed_repository(
    s3_enabled: None,
    source_proto: FsspecReadWriteProtocol,
    s3_calls: list[tuple[str, str]],
) -> None:
    """A listing must not make an unchanged repository look drifted.

    ``modified()`` is answered from whichever call last filled the s3fs
    listing cache, and MinIO reports ``LastModified`` to the millisecond
    on ``list_objects_v2`` where ``head_object`` reports whole seconds.
    So merely enumerating the bucket changes the answer for every object,
    and a state recorded against the HEAD value then reads as drifted --
    which costs a full re-read of the file to re-hash it, and a rewrite of
    a state that was already correct (gain#881).

    Nothing about the repository has changed between the publish and the
    sweep, so the sweep must read no content and write nothing at all.
    The manifests are loaded before the call record is cleared: reading
    one is a legitimate ``GetObject`` that is not what this is about.
    """
    # Given a freshly published s3 repository, with its manifests in hand
    proto = s3_test_protocol()
    copy_proto_genomic_resources(proto, source_proto)
    resources = list(proto.get_all_resources())
    manifests = {res.resource_id: res.get_manifest() for res in resources}

    # ...that something has since listed, so the millisecond values from
    # list_objects_v2 are what modified() now serves
    proto.filesystem.invalidate_cache()
    proto.filesystem.find(proto.url.rstrip("/"), withdirs=False)

    # When every file is classified against the state published beside it
    s3_calls.clear()
    for resource in resources:
        for entry in manifests[resource.resource_id]:
            proto.classify_resource_file(resource, resource, entry.name)

    # Then no file was re-read to be re-hashed, and no state was rewritten
    re_read = [
        key for key in keys_of(s3_calls, "GetObject")
        if not key.endswith(STATE_SUFFIX)
    ]
    assert re_read == []
    assert keys_of(s3_calls, "PutObject") == []
