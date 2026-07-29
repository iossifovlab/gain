# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Symlink containment inside a resource (gain#483).

gain#467 contained the resource file *name*. A symlink moves the escape
into the *resolution*: a perfectly contained name can still resolve
outside the resource, and outside the GRR root.

The rule these tests pin (local ``file`` protocol only -- ``http``/``s3``
have no symlinks):

1. No symlinked DIRECTORY component below the repository root, on read,
   write, delete or scan.
2. A symlinked LEAF FILE may be READ, and may resolve anywhere -- this is
   the shared-DVC-cache case and the reason symlinks are allowed at all.
3. A symlinked leaf file is never WRITTEN through. Deleting the link
   itself stays allowed: it removes the link, not the target.
"""
import os
import pathlib
import shutil

import pytest
from gain.genomic_resources.fsspec_protocol import FsspecReadWriteProtocol
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GR_CONTENTS_FILE_NAME,
    GR_INDEX_FILE_NAME,
    GenomicResource,
    ResourceFileState,
)
from gain.genomic_resources.testing import (
    build_filesystem_test_protocol,
    setup_directories,
)


@pytest.fixture
def outside(tmp_path: pathlib.Path) -> pathlib.Path:
    """A directory outside the GRR root, holding a file to reach for."""
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "secret.txt").write_text("TOP SECRET OUTSIDE THE GRR")
    return outside_dir


@pytest.fixture
def fs_proto(
    tmp_path: pathlib.Path, outside: pathlib.Path,
) -> FsspecReadWriteProtocol:
    """A GRR whose resource carries both shapes of symlink."""
    root_path = tmp_path / "grr"
    setup_directories(root_path, {
        "one": {
            GR_CONF_FILE_NAME: "",
            "data.txt": "alabala",
        },
    })
    os.symlink(outside, root_path / "one" / "up")
    os.symlink(outside / "secret.txt", root_path / "one" / "sneak.txt")
    return build_filesystem_test_protocol(root_path)


def test_read_through_a_symlinked_directory_is_refused(
    fs_proto: FsspecReadWriteProtocol,
) -> None:
    res = fs_proto.get_resource("one")

    with pytest.raises(ValueError) as excinfo:
        res.open_raw_file("up/secret.txt")

    message = str(excinfo.value)
    assert "one" in message
    assert "up" in message


def test_symlinked_leaf_file_reads_through_to_outside_the_repository(
    fs_proto: FsspecReadWriteProtocol,
) -> None:
    """The shared-DVC-cache case: a link into a cache outside the root."""
    res = fs_proto.get_resource("one")

    assert res.get_file_content("sneak.txt") == "TOP SECRET OUTSIDE THE GRR"
    assert res.file_exists("sneak.txt")


def test_write_through_a_symlinked_leaf_is_refused(
    fs_proto: FsspecReadWriteProtocol, outside: pathlib.Path,
) -> None:
    """Reading a link out is allowed; writing through one is not."""
    res = fs_proto.get_resource("one")

    with pytest.raises(ValueError) as excinfo:
        res.open_raw_file("sneak.txt", "wt")

    assert "sneak.txt" in str(excinfo.value)
    assert (outside / "secret.txt").read_text() == "TOP SECRET OUTSIDE THE GRR"


def test_delete_through_a_symlinked_directory_is_refused(
    fs_proto: FsspecReadWriteProtocol, outside: pathlib.Path,
) -> None:
    """``.MANIFEST`` diffing drives deletes, so no name need be typed."""
    res = fs_proto.get_resource("one")

    with pytest.raises(ValueError):
        fs_proto.delete_resource_file(res, "up/secret.txt")

    assert (outside / "secret.txt").exists()


def test_deleting_a_symlinked_leaf_removes_the_link_not_the_target(
    fs_proto: FsspecReadWriteProtocol,
    outside: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """Unlinking a link is not a write through it, so it stays allowed."""
    res = fs_proto.get_resource("one")

    fs_proto.delete_resource_file(res, "sneak.txt")

    assert not (tmp_path / "grr" / "one" / "sneak.txt").is_symlink()
    assert (outside / "secret.txt").read_text() == "TOP SECRET OUTSIDE THE GRR"


def test_manifest_keeps_a_symlinked_leaf_and_drops_a_symlinked_directory(
    fs_proto: FsspecReadWriteProtocol,
) -> None:
    """The file scan's two halves, asserted directly.

    Rules 1 and 2 meet here: the leaf link is an ordinary manifest entry
    (a DVC-materialized file must stay manifested), while the directory
    link contributes nothing.
    """
    res = fs_proto.get_resource("one")

    names = set(fs_proto.build_manifest(res).names())

    assert "sneak.txt" in names
    assert not any(name.startswith("up") for name in names)


def test_symlinked_resource_directory_is_skipped_with_a_warning(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """An outside tree must not become resources by being linked in."""
    outside_resource = tmp_path / "elsewhere" / "borrowed"
    setup_directories(outside_resource, {GR_CONF_FILE_NAME: ""})
    root_path = tmp_path / "grr"
    setup_directories(root_path, {"one": {GR_CONF_FILE_NAME: ""}})
    os.symlink(outside_resource, root_path / "linked")

    with caplog.at_level("WARNING"):
        proto = build_filesystem_test_protocol(root_path)
        resource_ids = {res.resource_id for res in proto.get_all_resources()}

    assert resource_ids == {"one"}
    assert "linked" in caplog.text


def test_a_healthy_resource_survives_a_symlinked_sibling(
    tmp_path: pathlib.Path,
) -> None:
    """Skipping must not take the repository down -- the gain#464 shape."""
    outside_resource = tmp_path / "elsewhere" / "borrowed"
    setup_directories(outside_resource, {GR_CONF_FILE_NAME: ""})
    root_path = tmp_path / "grr"
    setup_directories(root_path, {
        "one": {GR_CONF_FILE_NAME: "", "data.txt": "alabala"},
    })
    os.symlink(outside_resource, root_path / "linked")

    proto = build_filesystem_test_protocol(root_path)

    assert proto.get_resource("one").get_file_content("data.txt") == "alabala"


@pytest.fixture
def proto_with_symlinked_state_dir(
    tmp_path: pathlib.Path, outside: pathlib.Path,
) -> FsspecReadWriteProtocol:
    """``.grr/`` itself linked out -- these joins skip the shared one."""
    root_path = tmp_path / "grr"
    setup_directories(root_path, {"one": {GR_CONF_FILE_NAME: ""}})
    proto = build_filesystem_test_protocol(root_path)
    # Building the protocol saves file state, which creates the real
    # ``.grr``; swap it for the link the attack would leave behind.
    state_dir = root_path / "one" / ".grr"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    os.symlink(outside, state_dir)
    return proto


def test_state_path_through_a_symlinked_state_dir_is_refused(
    proto_with_symlinked_state_dir: FsspecReadWriteProtocol,
    outside: pathlib.Path,
) -> None:
    proto = proto_with_symlinked_state_dir
    res = proto.get_resource("one")

    with pytest.raises(ValueError):
        proto.save_resource_file_state(
            res, ResourceFileState("escape.txt", 1, 1.0, "md5"))

    assert list(outside.glob("escape*")) == []


def test_lockfile_through_a_symlinked_state_dir_is_refused(
    proto_with_symlinked_state_dir: FsspecReadWriteProtocol,
    outside: pathlib.Path,
) -> None:
    proto = proto_with_symlinked_state_dir
    res = proto.get_resource("one")

    with (
        pytest.raises(ValueError),
        proto.obtain_resource_file_lock(res, "escape.txt"),
    ):
        pass

    assert list(outside.glob("escape*")) == []


def test_state_file_is_not_written_through_a_symlinked_leaf(
    tmp_path: pathlib.Path, outside: pathlib.Path,
) -> None:
    """``.grr/<file>.state`` is a write sink like any other.

    Reached by every ``grr_manage`` repair, and ``.grr`` is never
    manifested, so manifest diffing does not police it either.
    """
    root_path = tmp_path / "grr"
    setup_directories(root_path, {
        "one": {GR_CONF_FILE_NAME: "", "data.txt": "alabala"},
    })
    proto = build_filesystem_test_protocol(root_path)
    res = proto.get_resource("one")
    planted = outside / "planted.txt"
    state_link = root_path / "one" / ".grr" / "data.txt.state"
    state_link.unlink(missing_ok=True)
    os.symlink(planted, state_link)

    with pytest.raises(ValueError):
        proto.save_resource_file_state(
            res, ResourceFileState("data.txt", 7, 1.0, "deadbeef"))

    assert not planted.exists()


def test_lockfile_is_not_written_through_a_symlinked_leaf(
    tmp_path: pathlib.Path, outside: pathlib.Path,
) -> None:
    """``filelock`` truncates on acquire, so the link must be refused here.

    ``filelock`` happens to pass ``O_NOFOLLOW`` on this platform, but that
    is a third-party guarantee and surfaces as a bare ``OSError``.
    """
    root_path = tmp_path / "grr"
    setup_directories(root_path, {
        "one": {GR_CONF_FILE_NAME: "", "data.txt": "alabala"},
    })
    proto = build_filesystem_test_protocol(root_path)
    res = proto.get_resource("one")
    planted = outside / "planted.lock"
    os.symlink(planted, root_path / "one" / ".grr" / "data.txt.lockfile")

    with (
        pytest.raises(ValueError),
        proto.obtain_resource_file_lock(res, "data.txt"),
    ):
        pass

    assert not planted.exists()


@pytest.mark.parametrize(("root_file", "publishes_it"), [
    (GR_CONTENTS_FILE_NAME, "build_content_file"),
    (GR_CONTENTS_FILE_NAME[:-3], "build_content_file"),
    (GR_INDEX_FILE_NAME, "build_index_info"),
])
def test_repository_root_files_are_not_written_through_a_symlink(
    tmp_path: pathlib.Path, outside: pathlib.Path,
    root_file: str, publishes_it: str,
) -> None:
    """``.CONTENTS``/``index.html`` join the repository url themselves.

    They are not resource files, so they inherit nothing from the
    resource-file join -- but they are written from repository content
    all the same.
    """
    root_path = tmp_path / "grr"
    setup_directories(root_path, {"one": {GR_CONF_FILE_NAME: ""}})
    proto = build_filesystem_test_protocol(root_path)
    planted = outside / "planted.out"
    # Building the protocol already published a real ``.CONTENTS``; swap
    # it for the link a poisoned clone would carry.
    (root_path / root_file).unlink(missing_ok=True)
    os.symlink(planted, root_path / root_file)

    with pytest.raises(ValueError):
        getattr(proto, publishes_it)()

    assert not planted.exists()


def test_repository_root_reached_through_a_symlink_still_works(
    tmp_path: pathlib.Path,
) -> None:
    """The atomic-flip publish layout: ``/repo/<name> -> <name>.<sha>``.

    The walk starts AT the root and only tests components below it, so
    the root's own linkness is never examined -- which is what keeps this
    layout, live on every served GRR, working.
    """
    snapshot = tmp_path / "grr.a06fc30c"
    setup_directories(snapshot, {
        "one": {GR_CONF_FILE_NAME: "", "data.txt": "alabala"},
    })
    flip_link = tmp_path / "grr"
    os.symlink(snapshot, flip_link)

    proto = build_filesystem_test_protocol(flip_link)
    res = proto.get_resource("one")

    assert res.get_file_content("data.txt") == "alabala"
    with res.open_raw_file("written.txt", "wt") as outfile:
        outfile.write("ok")
    assert res.get_file_content("written.txt") == "ok"


def test_write_into_a_symlinked_resource_directory_is_refused(
    tmp_path: pathlib.Path, outside: pathlib.Path,
) -> None:
    """A copy destination is addressed directly, never via the scan.

    This is the shape that makes an escaping resource directory serious:
    ``copy_resource_file`` writes REMOTE file content under a name the
    remote chose, so a resource directory linked at e.g. ``~/.ssh`` is an
    arbitrary write, not merely an arbitrary read.
    """
    root_path = tmp_path / "grr"
    setup_directories(root_path, {"one": {GR_CONF_FILE_NAME: ""}})
    proto = build_filesystem_test_protocol(root_path)
    os.symlink(outside, root_path / "linked")
    linked = GenomicResource("linked", (0,), proto)

    with pytest.raises(ValueError):
        proto.open_raw_file(linked, "planted.txt", "wt")

    assert list(outside.glob("planted*")) == []
