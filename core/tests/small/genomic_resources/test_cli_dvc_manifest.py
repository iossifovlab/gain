# pylint: disable=W0621,C0114,C0116,W0212,W0613
import hashlib
import pathlib
import textwrap

import pytest
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.fsspec_protocol import FsspecReadWriteProtocol
from gain.genomic_resources.repository import (
    GenomicResource,
    ReadWriteRepositoryProtocol,
)
from gain.genomic_resources.testing import (
    build_filesystem_test_protocol,
    setup_directories,
)

ORIGINAL_DATA = "ORIGINAL DATA - trust me\n"
TAMPERED_DATA = "TAMPERED DATA - not what the sidecar says!!\n"


def md5_of(content: str) -> str:
    return hashlib.md5(  # noqa: S324
        content.encode("utf8")).hexdigest()


def dvc_sidecar(path: str, content: str) -> str:
    return textwrap.dedent(f"""
        outs:
        - md5: {md5_of(content)}
          size: {len(content.encode("utf8"))}
          path: {path}
    """)


@pytest.fixture
def dvc_proto_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[pathlib.Path, FsspecReadWriteProtocol]:
    """Build a GRR with a materialised file and a matching .dvc sidecar."""
    path = tmp_path_factory.mktemp("cli_dvc_manifest")
    setup_directories(path, {
        "one": {
            "genomic_resource.yaml": "",
            "data.txt": ORIGINAL_DATA,
            "data.txt.dvc": dvc_sidecar("data.txt", ORIGINAL_DATA),
        },
    })
    proto = build_filesystem_test_protocol(path, repair=False)
    return path, proto


@pytest.fixture
def pointer_only_proto_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[pathlib.Path, FsspecReadWriteProtocol]:
    """Build a GRR where the data file is a .dvc pointer only."""
    path = tmp_path_factory.mktemp("cli_dvc_pointer_only")
    setup_directories(path, {
        "one": {
            "genomic_resource.yaml": "",
            "data.txt.dvc": dvc_sidecar("data.txt", ORIGINAL_DATA),
        },
    })
    proto = build_filesystem_test_protocol(path, repair=False)
    return path, proto


@pytest.fixture
def stale_dvc_proto_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[pathlib.Path, FsspecReadWriteProtocol]:
    """Build a GRR whose .dvc sidecar lies about the file it describes."""
    path = tmp_path_factory.mktemp("cli_dvc_stale")
    setup_directories(path, {
        "one": {
            "genomic_resource.yaml": "",
            "data.txt": TAMPERED_DATA,
            "data.txt.dvc": dvc_sidecar("data.txt", ORIGINAL_DATA),
        },
    })
    proto = build_filesystem_test_protocol(path, repair=False)
    return path, proto


def test_resource_repair_detects_tampered_file_with_stale_dvc(
    dvc_proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
) -> None:
    # Given a resource whose manifest agrees with its .dvc sidecar
    path, proto = dvc_proto_fixture
    cli_manage(["resource-manifest", "-R", str(path), "-r", "one"])

    res = proto.get_resource("one")
    manifest = proto.load_manifest(res)
    assert manifest["data.txt"].md5 == md5_of(ORIGINAL_DATA)

    # When the data file is edited in place, leaving the sidecar stale
    (path / "one" / "data.txt").write_text(TAMPERED_DATA, encoding="utf8")

    cli_manage(["resource-repair", "-R", str(path), "-r", "one"])

    # Then the manifest carries the md5 of the file's actual bytes
    res = proto.get_resource("one")
    manifest = proto.load_manifest(res)
    assert manifest["data.txt"].md5 == md5_of(TAMPERED_DATA)
    assert manifest["data.txt"].size == len(TAMPERED_DATA.encode("utf8"))


def test_force_manifest_rebuild_rehashes_tampered_file(
    dvc_proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
) -> None:
    # Given a resource whose manifest agrees with its .dvc sidecar
    path, proto = dvc_proto_fixture
    cli_manage(["resource-manifest", "-R", str(path), "-r", "one"])

    # When the data file is edited in place and the manifest is rebuilt
    (path / "one" / "data.txt").write_text(TAMPERED_DATA, encoding="utf8")

    cli_manage([
        "resource-manifest", "-R", str(path), "-r", "one", "--force"])

    # Then the manifest carries the md5 of the file's actual bytes
    res = proto.get_resource("one")
    manifest = proto.load_manifest(res)
    assert manifest["data.txt"].md5 == md5_of(TAMPERED_DATA)
    assert manifest["data.txt"].size == len(TAMPERED_DATA.encode("utf8"))

    # ... and so does the recorded file state
    state = proto.load_resource_file_state(res, "data.txt")
    assert state is not None
    assert state.md5 == md5_of(TAMPERED_DATA)
    assert state.size == len(TAMPERED_DATA.encode("utf8"))


def test_pointer_only_dvc_entry_is_taken_from_the_sidecar(
    pointer_only_proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
) -> None:
    """The `grr` pipeline works on a .dvc-only clone; guard that path."""
    # Given a resource whose data file is not materialised
    path, proto = pointer_only_proto_fixture
    assert not (path / "one" / "data.txt").exists()

    # When the manifest is built
    cli_manage(["resource-manifest", "-R", str(path), "-r", "one"])

    # Then the manifest entry comes from the .dvc sidecar
    res = proto.get_resource("one")
    manifest = proto.load_manifest(res)
    assert manifest["data.txt"].md5 == md5_of(ORIGINAL_DATA)
    assert manifest["data.txt"].size == len(ORIGINAL_DATA.encode("utf8"))
    assert not (path / "one" / "data.txt").exists()


def test_unchanged_files_are_not_rehashed(
    dvc_proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeat runs keep the no-hash fast path for unchanged files."""
    # Given a resource with an up-to-date manifest
    path, _proto = dvc_proto_fixture
    cli_manage(["resource-manifest", "-R", str(path), "-r", "one"])

    hashed: list[str] = []
    original_compute_md5_sum = ReadWriteRepositoryProtocol.compute_md5_sum

    def spy_compute_md5_sum(
        self: ReadWriteRepositoryProtocol,
        resource: GenomicResource,
        filename: str,
    ) -> str:
        hashed.append(filename)
        return original_compute_md5_sum(self, resource, filename)

    monkeypatch.setattr(
        ReadWriteRepositoryProtocol, "compute_md5_sum", spy_compute_md5_sum)

    # When nothing changed and the manifest is rebuilt
    cli_manage(["resource-manifest", "-R", str(path), "-r", "one"])

    # Then no file is hashed again
    assert hashed == []


def test_without_dvc_hashes_content_and_ignores_the_sidecar(
    stale_dvc_proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
) -> None:
    # Given a resource whose .dvc sidecar disagrees with the file on disk
    path, proto = stale_dvc_proto_fixture

    # When the manifest is built with '--without-dvc'
    cli_manage([
        "resource-manifest", "-R", str(path), "-r", "one", "--without-dvc"])

    # Then the manifest carries the md5 of the file's actual bytes
    res = proto.get_resource("one")
    manifest = proto.load_manifest(res)
    assert manifest["data.txt"].md5 == md5_of(TAMPERED_DATA)
    assert manifest["data.txt"].size == len(TAMPERED_DATA.encode("utf8"))


def test_without_dvc_short_option_hashes_content(
    stale_dvc_proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
) -> None:
    # Given a resource whose .dvc sidecar disagrees with the file on disk
    path, proto = stale_dvc_proto_fixture

    # When the manifest is built with '-D'
    cli_manage(["resource-manifest", "-R", str(path), "-r", "one", "-D"])

    # Then the manifest carries the md5 of the file's actual bytes
    res = proto.get_resource("one")
    manifest = proto.load_manifest(res)
    assert manifest["data.txt"].md5 == md5_of(TAMPERED_DATA)


@pytest.mark.parametrize("dvc_args,expected_hashed", [
    ([], False),
    (["--with-dvc"], False),
    (["--without-dvc"], True),
    (["-D"], True),
])
def test_dvc_option_decides_whether_data_file_is_hashed(
    dvc_proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
    monkeypatch: pytest.MonkeyPatch,
    dvc_args: list[str],
    expected_hashed: bool,
) -> None:
    """With dvc (the default) the sidecar md5 is used instead of hashing."""
    # Given a resource with a materialised file and a consistent sidecar
    path, proto = dvc_proto_fixture

    hashed: list[str] = []
    original_compute_md5_sum = ReadWriteRepositoryProtocol.compute_md5_sum

    def spy_compute_md5_sum(
        self: ReadWriteRepositoryProtocol,
        resource: GenomicResource,
        filename: str,
    ) -> str:
        hashed.append(filename)
        return original_compute_md5_sum(self, resource, filename)

    monkeypatch.setattr(
        ReadWriteRepositoryProtocol, "compute_md5_sum", spy_compute_md5_sum)

    # When the manifest is built
    cli_manage([
        "resource-manifest", "-R", str(path), "-r", "one", *dvc_args])

    # Then the data file's content is hashed only when dvc is turned off
    assert ("data.txt" in hashed) is expected_hashed

    res = proto.get_resource("one")
    manifest = proto.load_manifest(res)
    assert manifest["data.txt"].md5 == md5_of(ORIGINAL_DATA)


DVC_SUBCOMMANDS = [
    "repo-manifest", "resource-manifest",
    "repo-stats", "resource-stats",
    "repo-repair", "resource-repair",
    "repo-info", "resource-info",
]


@pytest.mark.parametrize("subcommand", DVC_SUBCOMMANDS)
def test_dvc_options_are_available_on_subcommand(
    subcommand: str,
    capsys: pytest.CaptureFixture,
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli_manage([subcommand, "--help"])
    assert excinfo.value.code == 0

    out = capsys.readouterr().out
    assert "--with-dvc" in out
    assert "--without-dvc" in out
    assert "-D" in out
