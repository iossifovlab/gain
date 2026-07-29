# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Containment of resource-relative file names (gain#467).

A resource file name reaches the repository protocols straight from GRR
content -- the resource's ``genomic_resource.yaml`` and its ``.MANIFEST``.
GRR content is fetched from remote repositories, so it is untrusted input.
These tests pin that no such name can address anything outside the
resource's own directory.
"""
import gzip
import hashlib
import json
import pathlib
from typing import Any

import pytest
from gain.genomic_resources.cached_repository import (
    GenomicResourceCachedRepo,
)
from gain.genomic_resources.cli import _create_contents_db, cli_manage
from gain.genomic_resources.fsspec_protocol import (
    FsspecReadWriteProtocol,
    build_fsspec_protocol,
)
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GR_CONTENTS_FILE_NAME,
    GR_MANIFEST_FILE_NAME,
    GenomicResource,
    GenomicResourceProtocolRepo,
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
    but the joined url is handed to fsspec unnormalised and the three
    backends then disagree about what it means -- measured, not assumed:
    `yarl`/aiohttp normalises it away client-side before the request is
    sent, minio rejects the key (``XMinioInvalidResourceName``), and local
    `file` resolves it. One name, three outcomes, so it is refused on every
    protocol rather than left to mean whatever the backend decides.
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

    The remote is named ``remote`` rather than by its own directory: a
    cached repo derives its cache directory from the protocol id, so the
    absolute id ``build_filesystem_test_repository`` hands out by default is
    refused outright (#460) and this test never reached its subject (#486).
    """
    remote_root = tmp_path / "remote"
    setup_directories(remote_root, {
        "one": {
            GR_CONF_FILE_NAME: "",
            "data.txt": "alabala",
        },
    })
    remote_repo = build_filesystem_test_repository(
        remote_root, proto_id="remote")
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
def test_manifest_parses_a_traversing_entry_and_refuses_to_use_it(
    fs_proto: FsspecReadWriteProtocol, name: str,
) -> None:
    """Parse-time rejection was an availability bug, so the parse survives.

    A ``.MANIFEST`` is parsed while ENUMERATING a repository, so raising
    from ``ManifestEntry`` took every healthy resource down with the
    poisoned one -- the gain#464 shape. The entry is parsed; the choke
    point refuses every use of it.
    """
    manifest = Manifest.from_file_content(
        f'- name: "{name}"\n  size: 5\n  md5: abc\n')
    assert manifest.names() == {name}

    res = fs_proto.get_resource("one")
    with pytest.raises(ValueError) as excinfo:
        res.open_raw_file(name)
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


# ---------------------------------------------------------------------------
# The resource id is the OTHER operand of the same join (gain#467).
#
# ``get_resource_url`` joins ``resource.get_genomic_resource_id_version()``
# onto the repository url.  On the remote path that id is read verbatim out
# of the repository's ``.CONTENTS.json.gz``, which is fetched from an
# untrusted GRR -- so containing only the file name leaves the escape open
# through its sibling.
# ---------------------------------------------------------------------------


def _poisoned_contents_entry(
    resource_id: str, payload: str,
) -> dict[str, Any]:
    return {
        "id": resource_id,
        "version": "0",
        "config": {"type": "basic"},
        "manifest": [
            {
                "name": GR_CONF_FILE_NAME,
                "size": len("type: basic\n"),
                "md5": hashlib.md5(  # noqa: S324
                    b"type: basic\n").hexdigest(),
            },
            {
                "name": "data.txt",
                "size": len(payload),
                "md5": hashlib.md5(  # noqa: S324
                    payload.encode()).hexdigest(),
            },
        ],
    }


def _setup_poisoned_remote(
    tmp_path: pathlib.Path, resource_id: str, payload: str,
) -> pathlib.Path:
    """Lay out a remote GRR whose ``.CONTENTS`` carries a traversing id.

    The escaped resource is real and readable on disk -- the question the
    tests ask is whether GAIn will follow the id out of the GRR root, not
    whether the target happens to exist.
    """
    remote_root = tmp_path / "area" / "remote"
    remote_root.mkdir(parents=True)
    setup_directories(remote_root, {
        "good": {GR_CONF_FILE_NAME: "type: basic\n", "data.txt": "alabala"},
    })
    escaped = tmp_path / "ESCAPED" / "evil"
    escaped.mkdir(parents=True)
    (escaped / GR_CONF_FILE_NAME).write_text("type: basic\n")
    (escaped / "data.txt").write_text(payload)

    entries = [_poisoned_contents_entry(resource_id, payload)]
    with gzip.open(remote_root / GR_CONTENTS_FILE_NAME, "wt") as outfile:
        json.dump(entries, outfile)
    return remote_root


def test_get_resource_url_rejects_traversing_resource_id(
    fs_proto: FsspecReadWriteProtocol,
) -> None:
    """The id is as untrusted as the file name and joined the same way."""
    poisoned = GenomicResource("../../ESCAPED/evil", (0,), fs_proto)

    with pytest.raises(ValueError) as excinfo:
        fs_proto.get_resource_url(poisoned)

    assert "../../ESCAPED/evil" in str(excinfo.value)


@pytest.mark.parametrize("resource_id", [
    "../../ESCAPED/evil",
    "/etc",
    "one/../../ESCAPED",
    "..\\..\\ESCAPED",
    "%2e%2e/%2e%2e/ESCAPED",
])
def test_get_resource_file_url_rejects_traversing_resource_id(
    fs_proto: FsspecReadWriteProtocol, resource_id: str,
) -> None:
    poisoned = GenomicResource(resource_id, (0,), fs_proto)

    with pytest.raises(ValueError) as excinfo:
        fs_proto.get_resource_file_url(poisoned, "data.txt")

    assert resource_id in str(excinfo.value)


def test_ordinary_resource_ids_still_build_a_url(
    fs_proto: FsspecReadWriteProtocol,
) -> None:
    """Nested ids and the root resource are ordinary and stay allowed."""
    nested = GenomicResource("hg38/gene_models/refSeq", (1, 2), fs_proto)
    assert fs_proto.get_resource_url(nested).endswith(
        "/hg38/gene_models/refSeq(1.2)")

    root = GenomicResource("", (0,), fs_proto)
    assert fs_proto.get_resource_url(root).rstrip("/").endswith("grr")


def test_remote_contents_traversing_id_is_not_served(
    tmp_path: pathlib.Path,
) -> None:
    """A poisoned ``.CONTENTS`` id must not become a usable resource.

    This is the reviewer's read-escape probe: on the unfixed code the
    payload above the GRR root was returned verbatim.
    """
    remote_root = _setup_poisoned_remote(
        tmp_path, "../../ESCAPED/evil", "pwned payload\n")
    proto = build_fsspec_protocol(
        "remote", str(remote_root), read_only=True)
    repo = GenomicResourceProtocolRepo(proto)

    assert repo.find_resource("../../ESCAPED/evil") is None
    assert [res.resource_id for res in repo.get_all_resources()] == []


def test_remote_contents_traversing_id_does_not_hide_healthy_resources(
    tmp_path: pathlib.Path,
) -> None:
    """One poisoned entry must not cost the repository its good ones."""
    remote_root = tmp_path / "area" / "remote"
    remote_root.mkdir(parents=True)
    entries = [
        _poisoned_contents_entry("../../ESCAPED/evil", "pwned\n"),
        _poisoned_contents_entry("good_one", "alabala"),
        _poisoned_contents_entry("good_two", "alabala"),
    ]
    with gzip.open(remote_root / GR_CONTENTS_FILE_NAME, "wt") as outfile:
        json.dump(entries, outfile)

    proto = build_fsspec_protocol(
        "remote", str(remote_root), read_only=True)

    assert sorted(res.resource_id for res in proto.get_all_resources()) == [
        "good_one", "good_two",
    ]


def test_cached_repository_writes_nothing_outside_the_cache(
    tmp_path: pathlib.Path,
) -> None:
    """The reviewer's write-escape probe, end to end.

    On the unfixed code caching a resource whose ``.CONTENTS`` id was
    ``../../ESCAPED/evil`` created the directory chain and wrote the file
    *two levels above* the cache root.
    """
    remote_root = _setup_poisoned_remote(
        tmp_path, "../../ESCAPED/evil", "pwned payload\n")
    remote_repo = GenomicResourceProtocolRepo(
        build_fsspec_protocol("remote", str(remote_root), read_only=True))
    cache_dir = tmp_path / "cache-area" / "sub" / "cache"

    cached_repo = GenomicResourceCachedRepo(remote_repo, str(cache_dir))

    assert cached_repo.find_resource("../../ESCAPED/evil") is None
    escapes = [
        path for path in (tmp_path / "cache-area").rglob("*")
        if path.is_file() and cache_dir not in path.parents
    ]
    assert escapes == []


# ---------------------------------------------------------------------------
# Windows-shaped absolute names (gain#467 review, finding 4).
#
# The rule already treats a backslash as a separator when scanning for
# ``..``; ignoring Windows absoluteness while doing so is incoherent, and
# every one of these discards the base under ``ntpath.join``.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("filename", [
    "C:/windows/system32/x",
    "C:\\windows\\x",
    "\\\\srv\\share\\x",
    "\\windows\\x",
    "x:y",
])
def test_windows_absolute_names_are_rejected(
    fs_proto: FsspecReadWriteProtocol, filename: str,
) -> None:
    res = fs_proto.get_resource("one")

    with pytest.raises(ValueError) as excinfo:
        res.open_raw_file(filename)

    assert filename in str(excinfo.value)


# ---------------------------------------------------------------------------
# Degenerate names (gain#467 review, finding 6).
#
# ``open_raw_file("")`` addressed the resource directory itself; a ``.`` or
# an empty segment names the resource directory too, or means nothing at
# all, and no legitimate resource file is spelled that way.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("filename", [
    "",
    "   ",
    "\t",
    ".",
    "./x",
    "sub/./x",
    "x/",
    "a//b",
])
def test_degenerate_names_are_rejected(
    fs_proto: FsspecReadWriteProtocol, filename: str,
) -> None:
    res = fs_proto.get_resource("one")

    with pytest.raises(ValueError):
        res.open_raw_file(filename)


@pytest.mark.parametrize("filename", [
    "data.txt",
    "statistics/histogram_score.json",
    "a.b/c-d/e_f.txt",
    "odd name.txt",
])
def test_ordinary_names_are_still_accepted(
    fs_proto: FsspecReadWriteProtocol, filename: str,
) -> None:
    """The rule stays proportionate -- nested, dotted and spaced names."""
    res = fs_proto.get_resource("one")

    with res.open_raw_file(filename, "wt") as outfile:
        outfile.write("ok")

    assert res.get_file_content(filename) == "ok"


# ---------------------------------------------------------------------------
# A poisoned .MANIFEST must not brick the repository (gain#467 review,
# finding 2 -- the gain#464 shape).
#
# ``collect_all_resources`` parses every ``.MANIFEST`` while enumerating, so
# a raise at manifest-parse time kills the generator before ANY resource is
# yielded: listing and repairing an unrelated healthy resource became
# impossible.  Containment is enforced at the choke point instead, where the
# poisoned name fails loudly on the resource that actually carries it.
# ---------------------------------------------------------------------------


_PWNED_ENTRY_NAME = "../../../tmp/PWNED"


@pytest.fixture
def poisoned_repo_path(tmp_path: pathlib.Path) -> pathlib.Path:
    root_path = tmp_path / "grr"
    for res_id in ("good_one", "good_two", "good_three"):
        setup_directories(root_path, {
            res_id: {GR_CONF_FILE_NAME: "type: basic\n", "data.txt": "ok"},
        })
    setup_directories(root_path, {
        "poisoned": {
            GR_CONF_FILE_NAME: "type: basic\n",
            "data.txt": "ok",
            GR_MANIFEST_FILE_NAME: (
                "- name: genomic_resource.yaml\n"
                "  size: 12\n"
                "  md5: 3c65b5f0b3e0a45f4b1c1d4a04e8ba86\n"
                "- name: data.txt\n"
                "  size: 2\n"
                "  md5: 444bcb3a3fcf8389296c49467f27e1d6\n"
                f"- name: {_PWNED_ENTRY_NAME}\n"
                "  size: 2\n"
                "  md5: 444bcb3a3fcf8389296c49467f27e1d6\n"
            ),
        },
    })
    return root_path


def test_manifest_parsing_keeps_a_traversing_entry() -> None:
    """Parsing a poisoned manifest is not the place to fail.

    Rejecting at parse time reads as defence in depth but is really an
    availability bug: the parse happens while *enumerating* a repository,
    so it takes the healthy resources down with the poisoned one.  The
    entry survives parsing; every path that would USE it raises.
    """
    manifest = Manifest.from_file_content(
        f'- name: "{_PWNED_ENTRY_NAME}"\n  size: 5\n  md5: abc\n')

    assert manifest.names() == {_PWNED_ENTRY_NAME}


def test_poisoned_manifest_keeps_the_repository_enumerable(
    poisoned_repo_path: pathlib.Path,
) -> None:
    proto = build_filesystem_test_protocol(poisoned_repo_path, repair=False)

    assert sorted(res.resource_id for res in proto.get_all_resources()) == [
        "good_one", "good_three", "good_two", "poisoned",
    ]


def test_poisoned_manifest_entry_still_fails_on_access(
    poisoned_repo_path: pathlib.Path,
) -> None:
    """The choke point is the load-bearing check and must keep working."""
    proto = build_filesystem_test_protocol(poisoned_repo_path, repair=False)
    res = proto.get_resource("poisoned")

    with pytest.raises(ValueError) as excinfo:
        res.open_raw_file(_PWNED_ENTRY_NAME)

    assert _PWNED_ENTRY_NAME in str(excinfo.value)


@pytest.mark.parametrize("argv", [
    ["list"],
    ["repo-repair"],
    ["resource-repair", "--resource", "good_one"],
])
def test_grr_manage_survives_a_poisoned_resource(
    poisoned_repo_path: pathlib.Path, argv: list[str],
) -> None:
    """Repairing an unrelated healthy resource must stay possible."""
    escape_target = (
        poisoned_repo_path / "poisoned" / _PWNED_ENTRY_NAME).resolve()

    cli_manage([*argv, "-R", str(poisoned_repo_path)])

    if argv[0] != "list":
        assert (
            poisoned_repo_path / "good_one" / GR_MANIFEST_FILE_NAME).exists()
    assert not escape_target.exists()
    proto = build_filesystem_test_protocol(poisoned_repo_path, repair=False)
    assert sorted(res.resource_id for res in proto.get_all_resources()) == [
        "good_one", "good_three", "good_two", "poisoned",
    ]


def test_fts_search_survives_a_dropped_poisoned_id(
    tmp_path: pathlib.Path,
) -> None:
    """Dropping a poisoned id must not turn search into a crash.

    The FTS index is a separate artefact of the repository, published by
    the same untrusted GRR, so it can name a resource the ``.CONTENTS``
    loader refused to build. Resolving the hit through the resource dict
    then raised ``KeyError`` -- one poisoned entry taking search down for
    the whole repository, which is the failure this fix exists to avoid.
    """
    root_path = tmp_path / "grr"
    setup_directories(root_path, {
        "good_one": {GR_CONF_FILE_NAME: "type: basic\n", "d.txt": "alabala"},
        "evil": {GR_CONF_FILE_NAME: "type: basic\n", "d.txt": "alabala"},
    })
    rw_proto = build_filesystem_test_protocol(root_path)
    _create_contents_db(rw_proto)

    with gzip.open(root_path / GR_CONTENTS_FILE_NAME, "rt") as infile:
        contents = json.load(infile)
    for entry in contents:
        if entry["id"] == "evil":
            entry["id"] = "../../ESCAPED/evil"
    with gzip.open(root_path / GR_CONTENTS_FILE_NAME, "wt") as outfile:
        json.dump(contents, outfile)

    proto = build_fsspec_protocol("remote", str(root_path), read_only=True)

    assert [res.resource_id for res in proto.search_resources("basic")] == [
        "good_one",
    ]
