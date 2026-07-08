# pylint: disable=W0621,C0114,C0116,W0212,W0613

import gzip
import io
from typing import Any, cast

import pysam
import pytest
from gain.genomic_resources.cli import collect_dvc_entries
from gain.genomic_resources.fsspec_protocol import FsspecReadWriteProtocol
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    ReadWriteRepositoryProtocol,
)
from gain.genomic_resources.testing import build_inmemory_test_protocol
from pytest_mock import MockerFixture


@pytest.mark.grr_rw
def test_collect_all_resources(
        fsspec_proto: FsspecReadWriteProtocol) -> None:
    proto = fsspec_proto

    resources = list(proto.collect_all_resources())
    assert len(resources) == 5, resources


def test_resource_paths(
    fsspec_proto: FsspecReadWriteProtocol,
) -> None:
    proto = fsspec_proto

    res = proto.get_resource("one")

    res_path = proto.get_resource_url(res)
    assert res_path.endswith("one")

    config_path = proto.get_resource_file_url(
        res, "genomic_resource.yaml")
    assert config_path.endswith("one/genomic_resource.yaml")


@pytest.mark.grr_rw
def test_build_resource_file_state(
        fsspec_proto: FsspecReadWriteProtocol) -> None:
    proto = fsspec_proto
    timestamp = 42
    res = proto.get_resource("one")

    state = proto.build_resource_file_state(
        res, "data.txt", timestamp=timestamp)

    assert state.filename == "data.txt"
    assert state.timestamp == pytest.approx(timestamp, abs=0.1)
    assert state.md5 == "c1cfdaf7e22865b29b8d62a564dc8f23"

    res = proto.get_resource("sub/two")
    state = proto.build_resource_file_state(
        res, "genes.gtf", timestamp=timestamp)

    assert state.filename == "genes.gtf"
    assert state.timestamp == pytest.approx(timestamp, abs=0.1)
    assert state.md5 == "d9636a8dca9e5626851471d1c0ea92b1"


@pytest.mark.grr_rw
def test_save_load_resource_file_state(
        fsspec_proto: FsspecReadWriteProtocol) -> None:
    proto = fsspec_proto
    timestamp = 42

    res = proto.get_resource("sub/two")
    state = proto.build_resource_file_state(
        res, "genes.gtf", timestamp=timestamp)

    proto.save_resource_file_state(res, state)
    state_path = proto._get_resource_file_state_path(res, "genes.gtf")
    assert proto.filesystem.exists(state_path)

    loaded = proto.load_resource_file_state(res, "genes.gtf")
    assert loaded is not None
    assert loaded.filename == "genes.gtf"
    assert loaded.timestamp == pytest.approx(timestamp, abs=0.1)
    assert loaded.md5 == "d9636a8dca9e5626851471d1c0ea92b1"


@pytest.mark.grr_rw
def test_collect_resource_entries(
        fsspec_proto: FsspecReadWriteProtocol) -> None:
    proto = fsspec_proto

    res = proto.get_resource("one")

    entries = proto.collect_resource_entries(res)
    assert len(entries) == 3

    entry = entries["data.txt"]
    assert entry.name == "data.txt"
    assert entry.size == 7

    entry = entries["genomic_resource.yaml"]
    assert entry.name == "genomic_resource.yaml"
    assert entry.size == 0


def test_file_exists(
        fsspec_proto: FsspecReadWriteProtocol) -> None:
    proto = fsspec_proto

    res = proto.get_resource("one")

    assert proto.file_exists(res, "genomic_resource.yaml")
    assert not proto.file_exists(res, "alabala.txt")


def test_open_raw_file_text_read(
        fsspec_proto: FsspecReadWriteProtocol) -> None:
    proto = fsspec_proto

    res = proto.get_resource("one")

    with proto.open_raw_file(res, "data.txt", mode="rt") as infile:
        content = infile.read()
        assert content == "alabala"


@pytest.mark.grr_rw
def test_open_raw_file_text_write(
        fsspec_proto: FsspecReadWriteProtocol) -> None:
    proto = fsspec_proto

    res = proto.get_resource("one")

    with proto.open_raw_file(res, "new_data.txt", mode="wt") as infile:
        infile.write("new alabala")

    assert proto.file_exists(res, "new_data.txt")


@pytest.mark.grr_rw
def test_open_raw_file_text_write_compression(
        fsspec_proto: FsspecReadWriteProtocol) -> None:
    proto = fsspec_proto

    res = proto.get_resource("one")

    with proto.open_raw_file(
            res, "new_data.txt.gz", mode="wt", compression=True) as outfile:
        outfile.write("new alabala")

    assert proto.file_exists(res, "new_data.txt.gz")

    filepath = proto.get_resource_file_url(res, "new_data.txt.gz")
    with gzip.open(
            cast(io.BytesIO, proto.filesystem.open(filepath)),
            mode="rt") as infile:
        content = infile.read()
        assert content == "new alabala"


def test_open_raw_file_text_read_compression(
        fsspec_proto: FsspecReadWriteProtocol) -> None:

    proto = fsspec_proto
    res = proto.get_resource("one")

    with proto.open_raw_file(
            res, "data.txt.gz", mode="rt", compression=True) as infile:
        content = infile.read()
        assert content == "alabala"


def test_compute_md5_sum(
        fsspec_proto: FsspecReadWriteProtocol) -> None:
    proto = fsspec_proto

    res = proto.get_resource("one")

    assert proto.compute_md5_sum(res, "data.txt") == \
        "c1cfdaf7e22865b29b8d62a564dc8f23"
    assert proto.compute_md5_sum(res, "genomic_resource.yaml") == \
        "d41d8cd98f00b204e9800998ecf8427e"


@pytest.mark.grr_rw
def test_build_manifest(
        fsspec_proto: FsspecReadWriteProtocol) -> None:
    proto = fsspec_proto

    res = proto.get_resource("one")

    manifest = proto.build_manifest(res)

    assert len(manifest) == 3
    assert manifest["data.txt"].size == 7
    assert manifest["data.txt"].md5 == \
        "c1cfdaf7e22865b29b8d62a564dc8f23"

    assert manifest["genomic_resource.yaml"].size == 0
    assert manifest["genomic_resource.yaml"].md5 == \
        "d41d8cd98f00b204e9800998ecf8427e"


def test_load_manifest(
        fsspec_proto: FsspecReadWriteProtocol) -> None:
    proto = fsspec_proto

    res = proto.get_resource("one")

    manifest = proto.load_manifest(res)

    assert len(manifest) == 3
    assert manifest["data.txt"].size == 7
    assert manifest["data.txt"].md5 == \
        "c1cfdaf7e22865b29b8d62a564dc8f23"

    assert manifest["genomic_resource.yaml"].size == 0
    assert manifest["genomic_resource.yaml"].md5 == \
        "d41d8cd98f00b204e9800998ecf8427e"


@pytest.mark.grr_rw
def test_load_missing_manifest(
        fsspec_proto: FsspecReadWriteProtocol) -> None:
    proto = fsspec_proto

    res = proto.get_resource("one")

    manifest_filename = proto.get_resource_file_url(res, ".MANIFEST")
    assert proto.filesystem.exists(manifest_filename)

    proto.filesystem.delete(manifest_filename)
    assert not proto.filesystem.exists(manifest_filename)

    with pytest.raises(FileNotFoundError):
        proto.load_manifest(res)


def test_get_manifest(
        fsspec_proto: FsspecReadWriteProtocol) -> None:
    proto = fsspec_proto

    res = proto.get_resource("one")

    manifest = proto.get_manifest(res)

    assert len(manifest) == 3

    assert manifest["data.txt"].size == 7
    assert manifest["data.txt"].md5 == \
        "c1cfdaf7e22865b29b8d62a564dc8f23"

    assert manifest["genomic_resource.yaml"].size == 0
    assert manifest["genomic_resource.yaml"].md5 == \
        "d41d8cd98f00b204e9800998ecf8427e"


@pytest.mark.grr_rw
def test_get_missing_manifest(
        fsspec_proto: FsspecReadWriteProtocol) -> None:
    proto = fsspec_proto

    res = proto.get_resource("one")

    manifest_filename = proto.get_resource_file_url(res, ".MANIFEST")
    assert proto.filesystem.exists(manifest_filename)

    proto.filesystem.delete(manifest_filename)
    assert not proto.filesystem.exists(manifest_filename)

    # now manifest file is missing... proto should recreate it...
    manifest = proto.get_manifest(res)

    assert len(manifest) == 3
    assert manifest["data.txt"].size == 7
    assert manifest["data.txt"].md5 == \
        "c1cfdaf7e22865b29b8d62a564dc8f23"

    assert manifest["genomic_resource.yaml"].size == 0
    assert manifest["genomic_resource.yaml"].md5 == \
        "d41d8cd98f00b204e9800998ecf8427e"


@pytest.mark.grr_rw
def test_delete_resource_file(
        fsspec_proto: FsspecReadWriteProtocol) -> None:

    # Given
    proto = fsspec_proto

    res = proto.get_resource("sub/two")

    path = proto.get_resource_file_url(res, "genes.gtf")
    assert proto.filesystem.exists(path)

    # When
    proto.delete_resource_file(res, "genes.gtf")

    # Then
    assert not proto.filesystem.exists(path)


@pytest.mark.grr_rw
def test_copy_resource_file(
    content_fixture: dict[str, Any],
    fsspec_proto: FsspecReadWriteProtocol,
    mocker: MockerFixture,
) -> None:
    # Given
    src_proto = build_inmemory_test_protocol(content_fixture)
    proto = fsspec_proto

    src_res = src_proto.get_resource("sub/two")
    dst_res = proto.get_resource("sub/two")

    mocker.patch(
        "gain.genomic_resources.fsspec_protocol.FsspecReadWriteProtocol."
        "_get_filepath_timestamp",
        return_value=1_000_000_000.00,
    )

    # When
    state = proto.copy_resource_file(src_res, dst_res, "genes.gtf")

    # Then
    timestamp = proto.get_resource_file_timestamp(dst_res, "genes.gtf")

    assert state is not None
    assert state.filename == "genes.gtf"
    assert state.timestamp == timestamp
    assert state.md5 == "d9636a8dca9e5626851471d1c0ea92b1"

    loaded = proto.load_resource_file_state(dst_res, "genes.gtf")
    assert loaded == state


@pytest.mark.grr_rw
def test_copy_resource(
    content_fixture: dict[str, Any],
    fsspec_proto: FsspecReadWriteProtocol,
    mocker: MockerFixture,
) -> None:
    # Given
    src_proto = build_inmemory_test_protocol(content_fixture)
    proto = fsspec_proto

    src_res = src_proto.get_resource("sub/two")

    mocker.patch(
        "gain.genomic_resources.fsspec_protocol.FsspecReadWriteProtocol."
        "_get_filepath_timestamp",
        return_value=1_000_000_000.00,
    )

    # When
    proto.copy_resource(src_res)

    # Then
    dst_res = proto.get_resource("sub/two")
    timestamp = proto.get_resource_file_timestamp(dst_res, "genes.gtf")

    state = proto.load_resource_file_state(dst_res, "genes.gtf")

    assert state is not None
    assert state.filename == "genes.gtf"
    assert state.timestamp == pytest.approx(timestamp, abs=5)
    assert state.timestamp == pytest.approx(1_000_000_000, abs=5)
    assert state.md5 == "d9636a8dca9e5626851471d1c0ea92b1"


@pytest.mark.grr_rw
def test_update_resource_all_files(
    fsspec_proto: ReadWriteRepositoryProtocol,
) -> None:
    # Given
    src_proto = build_inmemory_test_protocol({
        "sample": {
            GR_CONF_FILE_NAME: "",
            "prim.txt": "alabala",
            "second.txt": "labalaa",
        },
    })
    src_res = src_proto.get_resource("sample")
    proto = fsspec_proto

    # When
    proto.update_resource(src_res)

    # Then
    dst_res = proto.get_resource("sample")
    assert proto.file_exists(dst_res, "prim.txt")
    assert proto.file_exists(dst_res, "second.txt")


@pytest.mark.grr_rw
def test_update_resource_specific_file(
    fsspec_proto: ReadWriteRepositoryProtocol,
) -> None:
    # Given
    src_proto = build_inmemory_test_protocol({
        "sample": {
            GR_CONF_FILE_NAME: "",
            "prim.txt": "alabala",
            "second.txt": "labalaa",
        },
    })
    src_res = src_proto.get_resource("sample")
    proto = fsspec_proto

    # When
    proto.update_resource(src_res, {"prim.txt"})

    # Then
    dst_res = proto.get_resource("sample")
    assert proto.file_exists(dst_res, "prim.txt")
    assert not proto.file_exists(dst_res, "second.txt")


@pytest.mark.grr_rw
def test_build_manifest_should_not_update_existing_resource_state(
    fsspec_proto: ReadWriteRepositoryProtocol,
    mocker: MockerFixture,
) -> None:
    # Given
    proto = fsspec_proto
    res = proto.get_resource("one")
    mocker.patch(
        "gain.genomic_resources.fsspec_protocol.FsspecReadWriteProtocol."
        "_get_filepath_timestamp",
        return_value=1_000_000_000.00,
    )
    proto.build_manifest(res)

    mocker.patch.object(proto, "save_resource_file_state")

    # When
    proto.build_manifest(res)

    # Then
    assert not proto.save_resource_file_state.called  # type: ignore


def test_build_filesystem_http_sets_relaxed_timeout() -> None:
    # An HTTP GRR filesystem must be built with a relaxed aiohttp timeout so
    # a long-but-progressing multi-GB download isn't killed by aiohttp's
    # default 300s total cap; instead a *stalled* read (sock_read) or hung
    # connect (sock_connect) becomes a retryable error. See gain#43.
    from gain.genomic_resources.fsspec_protocol import _build_filesystem

    fs = _build_filesystem(
        "https://grr.example.com", base_url="https://grr.example.com")

    timeout = fs.client_kwargs["timeout"]
    assert timeout.total is None
    assert timeout.sock_read == 120
    assert timeout.sock_connect == 60


def test_htslib_verbosity_is_lowered_at_module_import() -> None:
    # Importing gain.genomic_resources.fsspec_protocol must lower htslib's
    # verbosity to 1 (errors only), suppressing the benign
    # "[W::hts_idx_load3] index older than data" warning emitted on opens
    # of parallel-downloaded GRR resources (caching protocol + DVC).
    assert pysam.get_verbosity() == 1


def test_gitignore_excludes_files_from_entries() -> None:
    proto = build_inmemory_test_protocol({
        "res": {
            GR_CONF_FILE_NAME: "",
            "data.txt": "data",
            "debug.log": "log content",
            ".gitignore": "*.log\n",
        },
    })
    res = proto.get_resource("res")
    entries = proto.collect_resource_entries(res)
    assert "data.txt" in entries
    assert "debug.log" not in entries


def test_gitignore_directory_pattern() -> None:
    proto = build_inmemory_test_protocol({
        "res": {
            GR_CONF_FILE_NAME: "",
            "data.txt": "data",
            "logs": {
                "run.log": "log1",
                "error.log": "log2",
            },
            ".gitignore": "logs/\n",
        },
    })
    res = proto.get_resource("res")
    entries = proto.collect_resource_entries(res)
    assert "data.txt" in entries
    assert "logs/run.log" not in entries
    assert "logs/error.log" not in entries


def test_gitignore_in_subdirectory() -> None:
    proto = build_inmemory_test_protocol({
        "res": {
            GR_CONF_FILE_NAME: "",
            "data.txt": "data",
            "subdir": {
                "keep.txt": "keep",
                "drop.log": "drop",
                ".gitignore": "*.log\n",
            },
        },
    })
    res = proto.get_resource("res")
    entries = proto.collect_resource_entries(res)
    assert "data.txt" in entries
    assert "subdir/keep.txt" in entries
    assert "subdir/drop.log" not in entries


def test_gitignore_pattern_in_root_ignores_nested_files() -> None:
    proto = build_inmemory_test_protocol({
        "res": {
            GR_CONF_FILE_NAME: "",
            "data.txt": "data",
            "subdir": {
                "run.log": "log",
            },
            ".gitignore": "*.log\n",
        },
    })
    res = proto.get_resource("res")
    entries = proto.collect_resource_entries(res)
    assert "data.txt" in entries
    assert "subdir/run.log" not in entries


def test_gitignore_comments_and_blank_lines_are_ignored() -> None:
    proto = build_inmemory_test_protocol({
        "res": {
            GR_CONF_FILE_NAME: "",
            "data.txt": "data",
            "notes.md": "notes",
            ".gitignore": "\n# This is a comment\n*.log\n",
        },
    })
    res = proto.get_resource("res")
    entries = proto.collect_resource_entries(res)
    assert "data.txt" in entries
    assert "notes.md" in entries


def test_gitignore_build_manifest_excludes_ignored_files() -> None:
    proto = build_inmemory_test_protocol({
        "res": {
            GR_CONF_FILE_NAME: "",
            "data.txt": "data",
            "temp.log": "temp",
            ".gitignore": "*.log\n",
        },
    })
    res = proto.get_resource("res")
    manifest = proto.build_manifest(res)
    assert "data.txt" in manifest
    assert "temp.log" not in manifest


def test_gitignore_subdirectory_pattern_does_not_affect_sibling() -> None:
    proto = build_inmemory_test_protocol({
        "res": {
            GR_CONF_FILE_NAME: "",
            "a": {
                "data.txt": "data",
                "drop.log": "drop",
                ".gitignore": "*.log\n",
            },
            "b": {
                "also.log": "kept",
            },
        },
    })
    res = proto.get_resource("res")
    entries = proto.collect_resource_entries(res)
    assert "a/data.txt" in entries
    assert "a/drop.log" not in entries
    assert "b/also.log" in entries


# `dvc add scores.bw` writes `/scores.bw` into .gitignore and creates a
# `scores.bw.dvc` pointer; the real data file must stay in the manifest.
SCORES_DVC_CONTENT = (
    "outs:\n"
    "- md5: 0123456789abcdef0123456789abcdef\n"
    "  size: 12\n"
    "  path: scores.bw\n"
)


# `dvc add bigdir` (a directory add) writes `/bigdir` into .gitignore and
# drops a sibling `bigdir.dvc` whose single out declares the directory itself:
# a `.dir`-suffixed md5, the total tree size, an `nfiles` count, and
# `path: bigdir`. Crucially `out["path"] == "bigdir"`, so the parse guard
# matches — only the `isdir` guard keeps a directory from being exempted.
BIGDIR_DVC_CONTENT = (
    "outs:\n"
    "- md5: 1234567890abcdef1234567890abcdef.dir\n"
    "  size: 246\n"
    "  nfiles: 2\n"
    "  path: bigdir\n"
)


# A genuine per-file pointer for `bigdir/a.bw` (`path: a.bw`). It lives INSIDE
# the gitignored directory. If the directory were wrongly exempted and the
# scan recursed into it, this pointer would exempt `a.bw` under the inherited
# `/bigdir` ancestor rule — the exact half-populated-subtree leak guard #1
# prevents. Its path must equal `a.bw` (not `scores.bw`) or the leak the test
# guards against could never occur, leaving guard #1 untested.
A_BW_DVC_CONTENT = (
    "outs:\n"
    "- md5: fedcba9876543210fedcba9876543210\n"
    "  size: 3\n"
    "  path: a.bw\n"
)


def test_gitignore_dvc_managed_leaf_is_kept_in_entries() -> None:
    proto = build_inmemory_test_protocol({
        "res": {
            GR_CONF_FILE_NAME: "",
            "scores.bw": "score data.",
            "scores.bw.dvc": SCORES_DVC_CONTENT,
            ".gitignore": "/scores.bw\n",
        },
    })
    res = proto.get_resource("res")
    entries = proto.collect_resource_entries(res)
    assert "scores.bw" in entries
    assert "scores.bw.dvc" in entries


def test_gitignore_leaf_without_dvc_sibling_is_excluded() -> None:
    proto = build_inmemory_test_protocol({
        "res": {
            GR_CONF_FILE_NAME: "",
            "data.txt": "data",
            "debug.log": "log content",
            ".gitignore": "*.log\n",
        },
    })
    res = proto.get_resource("res")
    entries = proto.collect_resource_entries(res)
    assert "data.txt" in entries
    assert "debug.log" not in entries


def test_gitignore_dvc_managed_directory_is_not_exempted() -> None:
    # Per-file `dvc add <file>` is the only supported DVC mode here. A stray
    # `dvc add <dir>` writes `/<dir>` into .gitignore and drops a sibling
    # `<dir>.dvc`; but a gitignored *directory* must NOT be exempted. If it
    # were, the scan would recurse into it and re-skip its children under the
    # inherited ancestor gitignore spec — silently yielding a half-populated
    # subtree (here: `bigdir/a.bw`, which has its own nested `.dvc`, would
    # slip in while `bigdir/b.bw` stays skipped). The directory is instead
    # treated like any other gitignored directory: skipped whole, its files
    # entirely absent from the entries.
    #
    # `bigdir.dvc` is a REALISTIC `dvc add <dir>` pointer whose single out has
    # `path: bigdir` (a `.dir` md5 + total size), exactly as DVC writes for a
    # directory add. This is deliberate: with `path == "bigdir"`, the parse
    # guard (out["path"] == name) MATCHES, so only the `isdir` guard #1 stands
    # between the directory and a wrongful exemption. Using a `path: scores.bw`
    # pointer here would let the parse guard reject the directory on its own
    # and leave guard #1 untested (see #209 review).
    proto = build_inmemory_test_protocol({
        "res": {
            GR_CONF_FILE_NAME: "",
            "data.txt": "data",
            "bigdir": {
                "a.bw": "aaa",
                "a.bw.dvc": A_BW_DVC_CONTENT,
                "b.bw": "bbb",
            },
            "bigdir.dvc": BIGDIR_DVC_CONTENT,
            ".gitignore": "/bigdir\n",
        },
    })
    res = proto.get_resource("res")
    entries = proto.collect_resource_entries(res)
    assert "data.txt" in entries
    assert "bigdir.dvc" in entries
    # The gitignored directory is skipped whole: none of its children appear,
    # not even the one carrying its own sibling `.dvc` pointer.
    assert "bigdir/a.bw" not in entries
    assert "bigdir/a.bw.dvc" not in entries
    assert "bigdir/b.bw" not in entries


# A `.dvc` pointer whose md5/size DELIBERATELY disagree with the real
# on-disk `scores.bw` bytes ("score data." -> size 11, md5 below). Used to
# tell apart "the scan supplied this value" from "the DVC pointer did".
REAL_SCORES_BW_MD5 = "0c5a7cf3aa752666540db748364115ea"
REAL_SCORES_BW_SIZE = 11
WRONG_SCORES_DVC_CONTENT = (
    "outs:\n"
    "- md5: ffffffffffffffffffffffffffffffff\n"
    "  size: 999\n"
    "  path: scores.bw\n"
)


def test_scan_of_present_dvc_managed_file_uses_real_on_disk_size() -> None:
    # Isolates the present-on-disk scanner path (the `.dvc`-sibling gitignore
    # exemption). `collect_resource_entries` never consults the `.dvc`
    # pointer, so the scanned entry must carry the REAL on-disk size even
    # when the pointer lies. If the exemption were reverted, `scores.bw`
    # would be gitignored out of the scan entirely and this would fail.
    proto = build_inmemory_test_protocol({
        "res": {
            GR_CONF_FILE_NAME: "",
            "scores.bw": "score data.",
            "scores.bw.dvc": WRONG_SCORES_DVC_CONTENT,
            ".gitignore": "/scores.bw\n",
        },
    })
    res = proto.get_resource("res")
    entries = proto.collect_resource_entries(res)
    assert "scores.bw" in entries
    assert entries["scores.bw"].size == REAL_SCORES_BW_SIZE


def test_build_manifest_keeps_dvc_managed_file_present_on_disk() -> None:
    # The data file is present on disk AND has a `.dvc` pointer whose md5/size
    # are deliberately wrong. Two things are asserted:
    #   1. The pure scan (`collect_resource_entries`) sees the real on-disk
    #      file (size 11) — proving the gitignore exemption keeps it.
    #   2. `build_manifest` merges the DVC pointer LAST, so the final manifest
    #      entry carries the POINTER's md5/size (999 / all-f), NOT the scanned
    #      value. This documents CURRENT behavior: for a present-on-disk DVC
    #      file the pointer wins over the scan.
    #
    # OPEN QUESTION (flagged in review, deliberately not changed here): for a
    # file that is present on disk, the manifest arguably should describe the
    # bytes actually served — i.e. the SCANNED md5/size should win, so a stale
    # `.dvc` pointer cannot produce a manifest that mismatches the real file.
    # Reconciling that would change production behavior in `build_manifest`
    # (and `check_update_manifest`), so it is left for a separate decision.
    proto = build_inmemory_test_protocol({
        "res": {
            GR_CONF_FILE_NAME: "",
            "scores.bw": "score data.",
            "scores.bw.dvc": WRONG_SCORES_DVC_CONTENT,
            ".gitignore": "/scores.bw\n",
        },
    })
    res = proto.get_resource("res")

    # The scanner (no pointer involved) sees the real file.
    scanned = proto.collect_resource_entries(res)
    assert scanned["scores.bw"].size == REAL_SCORES_BW_SIZE

    prebuild_entries = collect_dvc_entries(proto, res)
    manifest = proto.build_manifest(res, prebuild_entries)
    assert "scores.bw" in manifest
    assert "scores.bw.dvc" in manifest
    # Current behavior: the DVC pointer overrides the scanned value.
    assert manifest["scores.bw"].size == 999
    assert manifest["scores.bw"].md5 == "ffffffffffffffffffffffffffffffff"


def test_gitignore_dvc_managed_leaf_in_subdirectory_is_kept() -> None:
    # The `.dvc`-sibling exemption is applied per-directory during the
    # recursive scan, so a `dvc add`ed file living in a subdirectory (with the
    # subdirectory's own `.gitignore`) is kept just like one at the resource
    # root.
    proto = build_inmemory_test_protocol({
        "res": {
            GR_CONF_FILE_NAME: "",
            "a": {
                "scores.bw": "score data.",
                "scores.bw.dvc": SCORES_DVC_CONTENT,
                ".gitignore": "/scores.bw\n",
            },
        },
    })
    res = proto.get_resource("res")
    entries = proto.collect_resource_entries(res)
    assert "a/scores.bw" in entries
    assert "a/scores.bw.dvc" in entries


def test_gitignore_multiple_dvc_managed_leaves_in_one_directory() -> None:
    # Several `dvc add`ed files in the same directory are each exempted from
    # a shared gitignore rule, while a plain gitignored sibling is not. Each
    # pointer declares its own matching `outs.path`, as real `dvc add` does.
    one_dvc = (
        "outs:\n"
        "- md5: 0123456789abcdef0123456789abcdef\n"
        "  size: 12\n"
        "  path: one.bw\n"
    )
    two_dvc = (
        "outs:\n"
        "- md5: 0123456789abcdef0123456789abcdef\n"
        "  size: 12\n"
        "  path: two.bw\n"
    )
    proto = build_inmemory_test_protocol({
        "res": {
            GR_CONF_FILE_NAME: "",
            "one.bw": "score data.",
            "one.bw.dvc": one_dvc,
            "two.bw": "score data.",
            "two.bw.dvc": two_dvc,
            "debug.log": "log",
            ".gitignore": "*.bw\n*.log\n",
        },
    })
    res = proto.get_resource("res")
    entries = proto.collect_resource_entries(res)
    assert "one.bw" in entries
    assert "two.bw" in entries
    assert "debug.log" not in entries


def test_build_manifest_keeps_dvc_pointer_only_file() -> None:
    # Supported workflow: the `.dvc` pointer is checked out but the big
    # data file has NOT been `dvc pull`ed, so the scan cannot see it.
    proto = build_inmemory_test_protocol({
        "res": {
            GR_CONF_FILE_NAME: "",
            "scores.bw.dvc": SCORES_DVC_CONTENT,
            ".gitignore": "/scores.bw\n",
        },
    })
    res = proto.get_resource("res")
    prebuild_entries = collect_dvc_entries(proto, res)
    manifest = proto.build_manifest(res, prebuild_entries)
    assert "scores.bw" in manifest


def test_gitignore_non_pointer_dvc_file_does_not_exempt_sibling() -> None:
    # A real data file literally named `model.dvc` that is NOT a DVC pointer
    # must not be mistaken for one: exemption keys on a genuine PARSED DVC
    # output, so the deliberately gitignored sibling `model` stays excluded
    # (gain#209 adversarial review, finding #2).
    proto = build_inmemory_test_protocol({
        "res": {
            GR_CONF_FILE_NAME: "",
            "model": "the real ignored data",
            "model.dvc": "This is a real model file, not a DVC pointer.",
            ".gitignore": "/model\n",
        },
    })
    res = proto.get_resource("res")
    entries = proto.collect_resource_entries(res)
    assert "model" not in entries
    # `model.dvc` is itself a normal, non-gitignored file and still appears.
    assert "model.dvc" in entries


def test_gitignore_malformed_dvc_pointer_does_not_raise_or_exempt() -> None:
    # A `.dvc` sibling that does not parse as a well-formed DVC pointer
    # (here: invalid YAML) must neither crash the scan nor exempt its
    # would-be data file `scores.bw` from the gitignore rule (gain#209).
    proto = build_inmemory_test_protocol({
        "res": {
            GR_CONF_FILE_NAME: "",
            "scores.bw": "score data.",
            "scores.bw.dvc": "not: a: valid: dvc",
            ".gitignore": "/scores.bw\n",
        },
    })
    res = proto.get_resource("res")
    entries = proto.collect_resource_entries(res)  # must not raise
    assert "scores.bw" not in entries


def test_gitignore_dvc_pointer_without_outs_does_not_exempt() -> None:
    # A well-formed YAML that lacks an `outs` list is not a usable DVC
    # pointer; it must not exempt its would-be sibling (gain#209).
    proto = build_inmemory_test_protocol({
        "res": {
            GR_CONF_FILE_NAME: "",
            "scores.bw": "score data.",
            "scores.bw.dvc": "meta:\n  foo: bar\n",
            ".gitignore": "/scores.bw\n",
        },
    })
    res = proto.get_resource("res")
    entries = proto.collect_resource_entries(res)
    assert "scores.bw" not in entries


# A genuine per-file DVC pointer for `x.tmp`.
XTMP_DVC_CONTENT = (
    "outs:\n"
    "- md5: 0123456789abcdef0123456789abcdef\n"
    "  size: 12\n"
    "  path: x.tmp\n"
)


def test_gitignore_dvc_managed_leaf_under_ancestor_rule_is_kept() -> None:
    # A genuine per-file DVC pointer keeps its data file in the manifest even
    # when the rule that gitignores the leaf is an unrelated ANCESTOR pattern
    # (root `*.tmp`) rather than the `/x.tmp` line `dvc add` writes locally.
    # This is correct-by-design (gain#209 review finding #3): if
    # `sub/x.tmp.dvc` is a real pointer for `sub/x.tmp`, then `x.tmp` is
    # legitimately DVC-managed data that MUST appear -- the coincidental
    # ancestor `*.tmp` rule ignoring it is exactly the DVC situation. Because
    # exemption now requires a genuine parsed pointer whose `outs.path` names
    # the leaf, no extra narrowing on WHICH rule ignored it is needed.
    proto = build_inmemory_test_protocol({
        "res": {
            GR_CONF_FILE_NAME: "",
            ".gitignore": "*.tmp\n",
            "sub": {
                "x.tmp": "score data.",
                "x.tmp.dvc": XTMP_DVC_CONTENT,
            },
        },
    })
    res = proto.get_resource("res")
    entries = proto.collect_resource_entries(res)
    assert "sub/x.tmp" in entries
    assert "sub/x.tmp.dvc" in entries


def test_gitignore_binary_dvc_sibling_does_not_crash_or_exempt() -> None:
    # A real data file that merely happens to be named `scores.bw.dvc` and
    # contains non-UTF-8 bytes must NOT crash the scan (gain#209 review
    # finding #1: opening it in text mode raised UnicodeDecodeError, a
    # ValueError outside the caught tuple, aborting the whole manifest build).
    # It is not a well-formed DVC pointer, so the gitignored sibling
    # `scores.bw` stays excluded; the binary `.dvc` file itself is a normal,
    # non-gitignored file and still appears in the entries.
    proto = build_inmemory_test_protocol({
        "res": {
            GR_CONF_FILE_NAME: "",
            "scores.bw": "score data.",
            "scores.bw.dvc": b"\xff\xfe\x00",
            ".gitignore": "/scores.bw\n",
        },
    })
    res = proto.get_resource("res")
    entries = proto.collect_resource_entries(res)  # must not raise
    assert "scores.bw" not in entries
    assert "scores.bw.dvc" in entries


def test_directory_named_dvc_does_not_crash_scan() -> None:
    # A *directory* whose name ends in `.dvc` must NOT crash the scan
    # (gain#209 review finding #2: it used to be passed to `filesystem.open`
    # unconditionally, raising IsADirectoryError, an OSError outside the
    # caught tuple). Nothing here is gitignored, so no `.dvc` is opened at
    # all and the directory is scanned like any other, its child yielded.
    proto = build_inmemory_test_protocol({
        "res": {
            GR_CONF_FILE_NAME: "",
            "data.txt": "data",
            "weird.dvc": {
                "inner.txt": "inner",
            },
        },
    })
    res = proto.get_resource("res")
    entries = proto.collect_resource_entries(res)  # must not raise
    assert "data.txt" in entries
    assert "weird.dvc/inner.txt" in entries


# A well-formed DVC pointer whose `outs[].path` carries a directory prefix
# (`sub/scores.bw`) rather than the bare `scores.bw`.
PREFIXED_SCORES_DVC_CONTENT = (
    "outs:\n"
    "- md5: 0123456789abcdef0123456789abcdef\n"
    "  size: 12\n"
    "  path: sub/scores.bw\n"
)


def test_gitignore_dvc_pointer_with_prefixed_out_path_matches_cli() -> None:
    # A `.dvc` whose `outs[].path` is directory-prefixed (`sub/scores.bw`)
    # must be classified IDENTICALLY by the scanner and by
    # `cli.collect_dvc_entries` (gain#209 review finding #3). cli matches
    # `out["path"] == os.path.basename(<datafile-stem>)`, i.e.
    # `"sub/scores.bw" == "scores.bw"` -> False, so cli does NOT treat this
    # as a DVC data file. The scanner therefore must NOT exempt the bare
    # gitignored `scores.bw` either; the two DVC code paths agree.
    proto = build_inmemory_test_protocol({
        "res": {
            GR_CONF_FILE_NAME: "",
            "scores.bw": "score data.",
            "scores.bw.dvc": PREFIXED_SCORES_DVC_CONTENT,
            ".gitignore": "/scores.bw\n",
        },
    })
    res = proto.get_resource("res")
    entries = proto.collect_resource_entries(res)
    assert "scores.bw" not in entries
    # cli would not match the prefixed out path either -> consistent.
    assert "scores.bw" not in collect_dvc_entries(proto, res)


def test_scan_does_not_open_dvc_when_nothing_gitignored(
    mocker: MockerFixture,
) -> None:
    # Laziness (gain#209 review finding #4): when NOTHING in a directory is
    # gitignored, the scanner must not open any sibling `.dvc` at all (those
    # opens are pure network round-trips on http/s3). A resource with a
    # `data.txt`, its `data.txt.dvc` pointer and no `.gitignore` scans
    # correctly, and no `.dvc` file is ever opened.
    proto = build_inmemory_test_protocol({
        "res": {
            GR_CONF_FILE_NAME: "",
            "data.txt": "data",
            "data.txt.dvc": SCORES_DVC_CONTENT,
        },
    })
    res = proto.get_resource("res")
    spy = mocker.patch.object(
        proto.filesystem, "open", wraps=proto.filesystem.open)
    entries = proto.collect_resource_entries(res)
    assert "data.txt" in entries
    assert "data.txt.dvc" in entries
    opened = [call.args[0] for call in spy.call_args_list if call.args]
    assert not any(str(p).endswith(".dvc") for p in opened), opened
