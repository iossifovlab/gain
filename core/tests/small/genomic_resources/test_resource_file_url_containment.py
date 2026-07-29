# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Containment of resource-relative file names (gain#467).

A resource file name reaches the repository protocols straight from GRR
content -- the resource's ``genomic_resource.yaml`` and its ``.MANIFEST``.
GRR content is fetched from remote repositories, so it is untrusted input.
These tests pin that no such name can address anything outside the
resource's own directory.
"""
import pathlib

import pytest
from gain.genomic_resources.cached_repository import (
    GenomicResourceCachedRepo,
)
from gain.genomic_resources.fsspec_protocol import (
    FsspecReadWriteProtocol,
)
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    Manifest,
    ResourceFileState,
)
from gain.genomic_resources.testing import (
    build_filesystem_test_protocol,
    build_filesystem_test_repository,
    proto_builder,
    setup_directories,
)


@pytest.fixture
def fs_proto(tmp_path: pathlib.Path) -> FsspecReadWriteProtocol:
    root_path = tmp_path / "grr"
    setup_directories(root_path, {
        "one": {
            GR_CONF_FILE_NAME: "",
            "data.txt": "alabala",
        },
    })
    (tmp_path / "secret.txt").write_text("top secret")
    return build_filesystem_test_protocol(root_path)


def test_open_raw_file_rejects_traversing_name(
    fs_proto: FsspecReadWriteProtocol,
) -> None:
    res = fs_proto.get_resource("one")

    with pytest.raises(ValueError) as excinfo:
        res.open_raw_file("../../secret.txt")

    message = str(excinfo.value)
    assert "one" in message
    assert "../../secret.txt" in message


def test_open_raw_file_rejects_absolute_name(
    fs_proto: FsspecReadWriteProtocol,
) -> None:
    res = fs_proto.get_resource("one")

    with pytest.raises(ValueError) as excinfo:
        res.open_raw_file("/etc/passwd")

    message = str(excinfo.value)
    assert "one" in message
    assert "/etc/passwd" in message


def test_write_rejects_traversing_name_and_creates_nothing_outside(
    fs_proto: FsspecReadWriteProtocol, tmp_path: pathlib.Path,
) -> None:
    res = fs_proto.get_resource("one")
    outside_dir = tmp_path / "pwned-dir"

    with pytest.raises(ValueError):
        res.open_raw_file("../../pwned-dir/evil.txt", "wt")

    assert not outside_dir.exists()


def test_delete_rejects_traversing_name(
    fs_proto: FsspecReadWriteProtocol, tmp_path: pathlib.Path,
) -> None:
    res = fs_proto.get_resource("one")
    secret = tmp_path / "secret.txt"
    assert secret.exists()

    with pytest.raises(ValueError):
        fs_proto.delete_resource_file(res, "../../secret.txt")

    assert secret.exists()


def test_resource_file_state_path_rejects_traversing_name(
    fs_proto: FsspecReadWriteProtocol, tmp_path: pathlib.Path,
) -> None:
    res = fs_proto.get_resource("one")
    state = ResourceFileState("../../../state-escape.txt", 1, 1.0, "md5")

    with pytest.raises(ValueError):
        fs_proto.save_resource_file_state(res, state)

    assert list(tmp_path.glob("state-escape*")) == []


def test_nested_name_round_trips(fs_proto: FsspecReadWriteProtocol) -> None:
    """``statistics/<file>`` is the ubiquitous legitimate nested name."""
    res = fs_proto.get_resource("one")

    with res.open_raw_file("statistics/histogram_score.json", "wt") as outfile:
        outfile.write("{}")

    assert res.file_exists("statistics/histogram_score.json")
    assert res.get_file_content("statistics/histogram_score.json") == "{}"
    assert fs_proto.get_resource_file_url(
        res, "statistics/histogram_score.json",
    ).endswith("/one/statistics/histogram_score.json")


def test_inward_dotdot_name_is_rejected(
    fs_proto: FsspecReadWriteProtocol,
) -> None:
    """A ``..`` that stays inside the resource is rejected too.

    ``sub/../other.txt`` resolves inside the resource on a POSIX filesystem,
    but the joined url is handed to fsspec unnormalised, and an object store
    (s3, http) treats ``..`` as a literal key segment rather than resolving
    it. Accepting the name would therefore make it address two different
    objects depending on the protocol, so it is rejected on every protocol.
    """
    res = fs_proto.get_resource("one")

    with pytest.raises(ValueError) as excinfo:
        res.open_raw_file("sub/../other.txt")

    assert "sub/../other.txt" in str(excinfo.value)


@pytest.mark.parametrize("filename", [
    "%2e%2e/%2e%2e/secret.txt",
    "%2E%2E/%2E%2E/secret.txt",
    "sub/%2e%2e%2f%2e%2e%2fsecret.txt",
])
def test_percent_encoded_traversal_is_rejected(
    fs_proto: FsspecReadWriteProtocol, filename: str,
) -> None:
    """A url-encoded ``..`` is decoded by an http server, so reject it here.

    The joined location is a URL, not an os path: an http(s) GRR hands it to
    a server that percent-decodes the path before resolving it, so
    ``%2e%2e`` is a traversal there even though a local filesystem would
    treat it as a literal name.
    """
    res = fs_proto.get_resource("one")

    with pytest.raises(ValueError) as excinfo:
        res.open_raw_file(filename)

    assert filename in str(excinfo.value)


@pytest.mark.parametrize("filename", [
    "..\\..\\secret.txt",
    "sub\\..\\..\\secret.txt",
    "%2e%2e\\secret.txt",
])
def test_backslash_traversal_is_rejected(
    fs_proto: FsspecReadWriteProtocol, filename: str,
) -> None:
    """A backslash counts as a separator when scanning for ``..``.

    It is a path separator on Windows and in several fsspec backends, and a
    resource file name has no legitimate use for one next to ``..``.
    """
    res = fs_proto.get_resource("one")

    with pytest.raises(ValueError) as excinfo:
        res.open_raw_file(filename)

    assert filename in str(excinfo.value)


def test_backslash_in_an_ordinary_name_is_not_rejected(
    fs_proto: FsspecReadWriteProtocol,
) -> None:
    """Only ``..`` segments are rejected -- a stray backslash is not."""
    res = fs_proto.get_resource("one")

    with res.open_raw_file("odd\\name.txt", "wt") as outfile:
        outfile.write("ok")

    assert res.get_file_content("odd\\name.txt") == "ok"


def test_copy_resource_with_traversing_manifest_entry_fails_loudly(
    tmp_path: pathlib.Path,
) -> None:
    """A poisoned ``.MANIFEST`` must not be able to write outside the dest."""
    src_area = tmp_path / "src-area"
    dest_area = tmp_path / "dest-area"
    setup_directories(src_area, {
        "evil.txt": "pwned",
        "grr": {
            "one": {
                GR_CONF_FILE_NAME: "",
                "data.txt": "alabala",
                ".MANIFEST": (
                    "- name: genomic_resource.yaml\n"
                    "  size: 0\n"
                    "  md5: d41d8cd98f00b204e9800998ecf8427e\n"
                    "- name: data.txt\n"
                    "  size: 7\n"
                    "  md5: c1cfdaf7e22865b29b8d62a564dc8f23\n"
                    "- name: ../../evil.txt\n"
                    "  size: 5\n"
                    "  md5: 5e93de3efa544e85dcd6311732d28f95\n"
                ),
            },
        },
    })
    setup_directories(dest_area, {"grr": {}})

    src_proto = build_filesystem_test_protocol(
        src_area / "grr", repair=False)
    dest_proto = build_filesystem_test_protocol(
        dest_area / "grr", repair=False)

    with pytest.raises(ValueError) as excinfo:
        dest_proto.copy_resource(src_proto.get_resource("one"))

    assert "../../evil.txt" in str(excinfo.value)
    assert not (dest_area / "evil.txt").exists()
    assert sorted(p.name for p in dest_area.iterdir()) == ["grr"]


def test_cached_repository_inherits_containment(
    tmp_path: pathlib.Path,
) -> None:
    """The caching protocol delegates the join, so it inherits the check.

    It also takes a per-file lock in the cache before delegating, and that
    lockfile path is built by a separate join of its own.
    """
    remote_root = tmp_path / "remote"
    setup_directories(remote_root, {
        "one": {
            GR_CONF_FILE_NAME: "",
            "data.txt": "alabala",
        },
    })
    remote_repo = build_filesystem_test_repository(remote_root)
    cached_repo = GenomicResourceCachedRepo(
        remote_repo, str(tmp_path / "cache"))

    res = cached_repo.get_resource("one")

    with pytest.raises(ValueError) as excinfo:
        res.open_raw_file("../../../../evil-cache-escape.txt")

    assert "../../../../evil-cache-escape.txt" in str(excinfo.value)
    assert list(tmp_path.rglob("evil-cache-escape*")) == []


def test_resource_file_lockfile_path_rejects_traversing_name(
    fs_proto: FsspecReadWriteProtocol, tmp_path: pathlib.Path,
) -> None:
    """The per-file lockfile path is a third, separate join.

    ``filelock`` creates (and truncates) the lockfile on acquire, so an
    unchecked name here is an arbitrary-file-creation primitive even though
    the file is removed again on release.
    """
    res = fs_proto.get_resource("one")

    with pytest.raises(ValueError) as excinfo, \
            fs_proto.obtain_resource_file_lock(res, "../../../secret.txt"):
        pass

    assert "../../../secret.txt" in str(excinfo.value)
    assert not (tmp_path / "secret.txt.lockfile").exists()


@pytest.mark.parametrize("name", [
    "../../evil.txt",
    "/etc/passwd",
    "%2e%2e/evil.txt",
])
def test_manifest_rejects_traversing_entry_name(name: str) -> None:
    """Defence in depth: a poisoned manifest fails at parse time.

    The choke point in ``get_resource_file_url`` is the load-bearing check;
    this one only makes the diagnostic arrive earlier and closer to the
    poisoned input.
    """
    with pytest.raises(ValueError) as excinfo:
        Manifest.from_file_content(
            f'- name: "{name}"\n  size: 5\n  md5: abc\n')

    assert name in str(excinfo.value)


def test_manifest_keeps_nested_entry_names() -> None:
    manifest = Manifest.from_file_content(
        "- name: statistics/histogram_score.json\n  size: 2\n  md5: abc\n")

    assert manifest.names() == {"statistics/histogram_score.json"}


def _assert_containment_holds(grr_scheme: str) -> None:
    content = {
        GR_CONF_FILE_NAME: "",
        "data.txt": "alabala",
        "statistics": {"histogram_score.json": "{}"},
    }
    with proto_builder(grr_scheme, content) as proto:
        res = proto.get_resource("")

        with pytest.raises(ValueError) as excinfo:
            res.open_raw_file("../../secret.txt")
        assert "../../secret.txt" in str(excinfo.value)

        with pytest.raises(ValueError):
            res.open_raw_file("/etc/passwd")

        with pytest.raises(ValueError):
            res.open_raw_file("%2e%2e/%2e%2e/secret.txt")

        assert res.get_file_content("statistics/histogram_score.json") == "{}"


@pytest.mark.grr_full
def test_containment_holds_on_every_scheme(grr_scheme: str) -> None:
    """The check is url-shaped, so it holds off the local filesystem too."""
    _assert_containment_holds(grr_scheme)


@pytest.mark.grr_http
def test_containment_holds_on_http_scheme(grr_scheme: str) -> None:
    """``grr_full`` covers file+s3; http is parametrized by its own mark."""
    _assert_containment_holds(grr_scheme)
