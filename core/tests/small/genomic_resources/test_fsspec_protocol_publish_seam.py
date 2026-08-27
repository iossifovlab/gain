# pylint: disable=W0621,C0114,C0116,W0212,W0613
import os
import pathlib
from collections.abc import Callable

import pytest
from gain.genomic_resources.fsspec_protocol import (
    GRR_INTERNAL_DIR,
    FsspecReadWriteProtocol,
)
from gain.genomic_resources.repository import (
    GR_CONTENTS_FILE_NAME,
    GR_INDEX_FILE_NAME,
    GR_MANIFEST_FILE_NAME,
    GenomicResource,
)
from gain.genomic_resources.testing import (
    build_faulty_test_protocol,
)
from pytest_mock import MockerFixture

from .conftest import (
    BASIC_RESOURCE_ID as RESOURCE_ID,
)
from .conftest import (
    BASIC_RESOURCE_LAYOUT as RESOURCE_LAYOUT,
)

#: The temp the seam stages through. Named by pattern, not exactly: the
#: uuid is minted per publish inside the protocol.
_STAGED_TEMP = "*.part"

_ABOUT_PAGE = "about.html"

_Publish = Callable[[FsspecReadWriteProtocol, GenomicResource], None]
_PathOf = Callable[[FsspecReadWriteProtocol, GenomicResource], str]


def _read_bytes(proto: FsspecReadWriteProtocol, path: str) -> bytes:
    """Read an artifact off the store, without going through a resource.

    Deliberately the raw filesystem and not ``get_file_content``: these
    tests assert that the bytes on the store survived a failed publish,
    and a read through the resource could answer from a memo rather than
    from the store, which is exactly what must not be trusted here.
    """
    with proto.filesystem.open(path, "rb") as infile:
        return bytes(infile.read())


def _temp_leftovers(proto: FsspecReadWriteProtocol) -> list[str]:
    return [
        path for path in proto.filesystem.find(proto.url)
        if path.endswith(".part")
    ]


def _write_about_md(proto: FsspecReadWriteProtocol) -> None:
    """Give the repository an ``about.md``, so a page is rendered from it."""
    path = os.path.join(proto.url, "about.md")
    with proto.filesystem.open(path, "wt", encoding="utf8") as outfile:
        outfile.write("# about\n")


# The five artifacts the repository publishes, each as the pair of "how to
# publish it" and "where it lands". Each publish is idempotent, so the same
# callable both establishes the baseline and makes the attempt that fails.


def _publish_contents_index(
        proto: FsspecReadWriteProtocol, res: GenomicResource) -> None:
    proto.build_content_file()


def _contents_index_path(
        proto: FsspecReadWriteProtocol, res: GenomicResource) -> str:
    return os.path.join(proto.url, GR_CONTENTS_FILE_NAME)


def _publish_manifest(
        proto: FsspecReadWriteProtocol, res: GenomicResource) -> None:
    proto.save_manifest(res, proto.build_manifest(res))


def _manifest_path(
        proto: FsspecReadWriteProtocol, res: GenomicResource) -> str:
    return proto.get_resource_file_url(res, GR_MANIFEST_FILE_NAME)


def _publish_resource_index(
        proto: FsspecReadWriteProtocol, res: GenomicResource) -> None:
    proto.save_index(res, "<html>published</html>")


def _resource_index_path(
        proto: FsspecReadWriteProtocol, res: GenomicResource) -> str:
    return proto.get_resource_file_url(res, GR_INDEX_FILE_NAME)


def _publish_repository_index(
        proto: FsspecReadWriteProtocol, res: GenomicResource) -> None:
    proto.build_index_info()


def _repository_index_path(
        proto: FsspecReadWriteProtocol, res: GenomicResource) -> str:
    return os.path.join(proto.url, GR_INDEX_FILE_NAME)


def _publish_about_page(
        proto: FsspecReadWriteProtocol, res: GenomicResource) -> None:
    # The `about.md` this renders from is input, not the artifact; writing
    # it again on the second call keeps the publish idempotent.
    _write_about_md(proto)
    proto.build_index_info()


def _about_page_path(
        proto: FsspecReadWriteProtocol, res: GenomicResource) -> str:
    return os.path.join(proto.url, _ABOUT_PAGE)


#: ``pattern`` matches the live artifact AND the temp staged beside it, so
#: a fault scripted on it fires whether the publish stages or writes in
#: place -- which is what makes these tests fail on a seam that regressed.
SINKS = [
    pytest.param(
        _publish_contents_index, _contents_index_path,
        f"*{GR_CONTENTS_FILE_NAME}*", id="contents-index"),
    pytest.param(
        _publish_manifest, _manifest_path,
        f"*{GR_MANIFEST_FILE_NAME}*", id="resource-manifest"),
    pytest.param(
        _publish_resource_index, _resource_index_path,
        f"*{GR_INDEX_FILE_NAME}*", id="resource-index"),
    pytest.param(
        _publish_repository_index, _repository_index_path,
        f"*{GR_INDEX_FILE_NAME}*", id="repository-index"),
    pytest.param(
        _publish_about_page, _about_page_path,
        f"*{_ABOUT_PAGE}*", id="about-page"),
]


@pytest.mark.parametrize(("publish", "path_of", "pattern"), SINKS)
def test_a_failed_publish_leaves_the_previous_artifact_intact(
    tmp_path: pathlib.Path,
    publish: _Publish,
    path_of: _PathOf,
    pattern: str,
) -> None:
    proto, filesystem = build_faulty_test_protocol(tmp_path, RESOURCE_LAYOUT)
    res = proto.get_resource(RESOURCE_ID)

    publish(proto, res)
    published = _read_bytes(proto, path_of(proto, res))
    assert published

    filesystem.fail_write(pattern, OSError("scripted write failure"))

    with pytest.raises(OSError, match="scripted write failure"):
        publish(proto, res)

    assert _read_bytes(proto, path_of(proto, res)) == published


@pytest.mark.parametrize(("publish", "path_of", "pattern"), SINKS)
def test_a_failed_publish_leaves_no_temp_behind(
    tmp_path: pathlib.Path,
    publish: _Publish,
    path_of: _PathOf,
    pattern: str,
) -> None:
    proto, filesystem = build_faulty_test_protocol(tmp_path, RESOURCE_LAYOUT)
    res = proto.get_resource(RESOURCE_ID)
    publish(proto, res)

    # Scripted on the temp, not on the artifact: the fault can only fire
    # if the publish really staged through one, so a seam that regressed
    # to writing in place would fail this test rather than pass it
    # vacuously. MemoryFileSystem commits its object at ``open``, so the
    # temp genuinely exists by the time the close fails and the discard
    # has something to remove.
    filesystem.fail_close(_STAGED_TEMP, OSError("scripted close failure"))

    with pytest.raises(OSError, match="scripted close failure"):
        publish(proto, res)

    assert _temp_leftovers(proto) == []


def test_the_seam_stages_inside_the_internal_dir(
    tmp_path: pathlib.Path,
    mocker: MockerFixture,
) -> None:
    # Where the temp lives is the whole reason a publish in flight is
    # invisible to a manifest build, and the reason it is on the same
    # filesystem as the target. Pinned by observing the protocol's
    # filesystem, its actual contract boundary (ADR 0021), rather than by
    # calling the path helper directly.
    proto, filesystem = build_faulty_test_protocol(tmp_path, RESOURCE_LAYOUT)
    res = proto.get_resource(RESOURCE_ID)
    opened = mocker.spy(filesystem, "open")

    proto.build_content_file()
    proto.save_manifest(res, proto.build_manifest(res))

    staged = [
        str(call.args[0]) for call in opened.call_args_list
        if str(call.args[0]).endswith(".part")
    ]

    assert len(staged) == 2, "both publishes must stage through a temp"

    # The repository's own artifact stages at the root's internal dir; the
    # resource's, in the resource's own -- always beside the target.
    assert os.path.dirname(staged[0]) == os.path.join(
        proto.url, GRR_INTERNAL_DIR)
    assert os.path.dirname(staged[1]) == os.path.join(
        proto.get_resource_url(res), GRR_INTERNAL_DIR)


def test_two_publishes_of_one_artifact_stage_through_different_temps(
    tmp_path: pathlib.Path,
    mocker: MockerFixture,
) -> None:
    # Concurrent publishes of the same artifact must not collide on the
    # temp path, which is what the uuid in it is for. Two sequential
    # publishes are what a test can observe; a shared name would show up
    # here just as it would under concurrency.
    proto, filesystem = build_faulty_test_protocol(tmp_path, RESOURCE_LAYOUT)
    opened = mocker.spy(filesystem, "open")

    proto.build_content_file()
    proto.build_content_file()

    staged = [
        str(call.args[0]) for call in opened.call_args_list
        if str(call.args[0]).endswith(".part")
    ]

    assert len(staged) == 2
    assert staged[0] != staged[1]


def test_publishing_refuses_a_mode_that_is_not_a_write(
    tmp_path: pathlib.Path,
) -> None:
    # The handle is opened on an empty temp, so an append mode would
    # append to nothing and the move would then replace the artifact with
    # only the appended part -- silent data loss on a public seam.
    proto, _filesystem = build_faulty_test_protocol(tmp_path, RESOURCE_LAYOUT)
    res = proto.get_resource(RESOURCE_ID)

    with pytest.raises(ValueError, match="needs a write mode"), \
            proto.publish_raw_file(res, "appended.txt", "at"):
        pass  # pragma: no cover -- the open must not be reached


def test_publishing_carries_the_encoding_to_the_staged_open(
    tmp_path: pathlib.Path,
    mocker: MockerFixture,
) -> None:
    # The repository pages are written with an explicit utf8 encoding.
    # Staging must not quietly drop it: without it the bytes follow the
    # process's preferred encoding, so the same page publishes
    # differently under a non-utf8 locale.
    proto, filesystem = build_faulty_test_protocol(tmp_path, RESOURCE_LAYOUT)
    _write_about_md(proto)
    opened = mocker.spy(filesystem, "open")

    proto.build_index_info()

    staged = [
        call for call in opened.call_args_list
        if str(call.args[0]).endswith(".part")
    ]
    assert len(staged) == 2, "about.html and the index page both stage"
    for call in staged:
        assert call.kwargs.get("encoding") == "utf8"


def test_a_failing_staged_open_does_not_leak_url_credentials(
    tmp_path: pathlib.Path,
) -> None:
    # The sinks that publish through this seam used to open through
    # `_open_fsspec_file`, whose failures are re-raised with the url
    # userinfo stripped. Staging the write must not cost them that.
    proto, filesystem = build_faulty_test_protocol(tmp_path, RESOURCE_LAYOUT)
    res = proto.get_resource(RESOURCE_ID)

    filesystem.fail_open(
        _STAGED_TEMP,
        OSError("cannot open https://user:s3cr3t@grr.example.com/one"))

    with pytest.raises(OSError, match="cannot open") as excinfo:
        proto.save_manifest(res, proto.build_manifest(res))

    message = str(excinfo.value)
    assert "s3cr3t" not in message
    assert "user" not in message
    assert "grr.example.com" in message


def test_a_staged_temp_never_becomes_a_manifest_entry(
    tmp_path: pathlib.Path,
) -> None:
    # An interrupted publish is discarded, but a publish in flight is
    # concurrent with whatever else is reading the repository -- and a
    # manifest built while one is staged must not record the temp as one
    # of the resource's files.
    #
    # A CONTROL file is planted beside the temp and asserted to BE picked
    # up. Without it the assertion also passes on a scan that skipped
    # every candidate, or one that never ran at all.
    #
    # Only the manifest walk is asserted on. The resource walk skips a
    # dot-name too, but no temp could make it invent a resource whether it
    # did or not -- a `.grr` holding a `.part` carries no
    # `genomic_resource.yaml` -- so an assertion about it would be
    # decorative: verified by removing that walk's dot-skip, which this
    # file does not notice, while removing the manifest walk's turns this
    # test red.
    proto, filesystem = build_faulty_test_protocol(tmp_path, RESOURCE_LAYOUT)
    res = proto.get_resource(RESOURCE_ID)

    files_before = sorted(
        name for name, _ in proto.build_manifest(res).get_files())

    staged = os.path.join(
        proto.get_resource_url(res), GRR_INTERNAL_DIR, "in-flight.part")
    filesystem.makedirs(os.path.dirname(staged), exist_ok=True)
    with filesystem.open(staged, "wb") as outfile:
        outfile.write(b"a publish in flight")

    control = os.path.join(proto.get_resource_url(res), "control.txt")
    with filesystem.open(control, "wt") as outfile:
        outfile.write("not a temp")

    assert sorted(
        name for name, _ in proto.build_manifest(res).get_files()
    ) == sorted([*files_before, "control.txt"])


@pytest.mark.grr_rw
def test_republishing_the_contents_file_lands_identical_bytes(
        fsspec_proto: FsspecReadWriteProtocol) -> None:
    # The seam must not disturb what a publish produces. The contents
    # index is deliberately byte-deterministic -- gzip mtime pinned to 0
    # and the header OS byte normalised -- so that republishing unchanged
    # content shows clean under `git status --porcelain` in the GRRs that
    # are published as git trees. Staging through a temp and moving must
    # preserve that on every scheme, including the object store, where
    # the move is a copy-and-delete rather than a rename.
    #
    # The only test here that is not `inmemory`-only, and deliberately so:
    # its point IS the store's own answer to the move, which a scripted
    # filesystem cannot give (ADR 0021).
    proto = fsspec_proto

    proto.build_content_file()
    first = _read_bytes(
        proto, os.path.join(proto.url, GR_CONTENTS_FILE_NAME))
    assert first

    proto.build_content_file()

    assert _read_bytes(
        proto, os.path.join(proto.url, GR_CONTENTS_FILE_NAME)) == first
    assert _temp_leftovers(proto) == []
