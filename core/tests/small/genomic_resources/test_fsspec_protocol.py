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


def test_build_manifest_keeps_dvc_managed_file_present_on_disk() -> None:
    proto = build_inmemory_test_protocol({
        "res": {
            GR_CONF_FILE_NAME: "",
            "scores.bw": "score data.",
            "scores.bw.dvc": SCORES_DVC_CONTENT,
            ".gitignore": "/scores.bw\n",
        },
    })
    res = proto.get_resource("res")
    prebuild_entries = collect_dvc_entries(proto, res)
    manifest = proto.build_manifest(res, prebuild_entries)
    assert "scores.bw" in manifest
    assert "scores.bw.dvc" in manifest


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
