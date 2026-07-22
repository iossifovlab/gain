# pylint: disable=W0621,C0114,C0116,W0212,W0613

import logging
import pathlib
import textwrap

import pytest
from gain.genomic_resources.cli import collect_dvc_entries
from gain.genomic_resources.dvc import DvcContentDriftError
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GenomicResource,
    Manifest,
    ReadWriteRepositoryProtocol,
)
from gain.genomic_resources.testing import (
    build_filesystem_test_protocol,
    setup_directories,
)

# md5 of "big" - the actual content of the dvc-managed files below
BIG_MD5 = "d861877da56b8b4ceb35c8cbfdf65bb4"
BIG_SIZE = 3


def _setup_grr(path: pathlib.Path) -> None:
    """Set up two resources: one whose sidecars lie, one whose do not.

    The sidecars of `one` deliberately declare an md5 sum and a size that no
    file could have, so that "the sidecar supplied this value" and "the
    content did" can never be confused.
    """
    setup_directories(path, {
        "one": {
            GR_CONF_FILE_NAME: "",
            "data.txt": "alabala",
            "b.big": "big",
            "b.big.dvc": textwrap.dedent("""
                outs:
                - md5: bbbb
                  path: b.big
                  size: 3000000000
            """),
            # pointer only: no `c.big` on disk, as in a `.dvc`-only clone
            "c.big.dvc": textwrap.dedent("""
                outs:
                - md5: cccc
                  path: c.big
                  size: 3000000000
            """),
            "sub": {
                "a.big": "big",
                "a.big.dvc": textwrap.dedent("""
                    outs:
                    - md5: aaaa
                      path: a.big
                      size: 3000000000
                """),
            },
        },
        "clean": {
            GR_CONF_FILE_NAME: "",
            "d.big": "big",
            "d.big.dvc": textwrap.dedent(f"""
                outs:
                - md5: {BIG_MD5}
                  path: d.big
                  size: {BIG_SIZE}
            """),
            # pointer only, as above
            "e.big.dvc": textwrap.dedent("""
                outs:
                - md5: eeee
                  path: e.big
                  size: 3000000000
            """),
        },
    })


@pytest.fixture
def proto_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> ReadWriteRepositoryProtocol:
    """A freshly cloned GRR: no recorded resource file states."""
    path = tmp_path_factory.mktemp("resource_state_and_manifest")
    _setup_grr(path)
    return build_filesystem_test_protocol(path, repair=False)


@pytest.fixture
def repaired_proto_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> ReadWriteRepositoryProtocol:
    """A GRR that HAS recorded resource file states - every real GRR."""
    path = tmp_path_factory.mktemp("resource_state_and_manifest_repaired")
    _setup_grr(path)
    return build_filesystem_test_protocol(path, repair=True)


@pytest.mark.parametrize(
    ("filename", "dvc_md5"), [("sub/a.big", "aaaa"), ("b.big", "bbbb")])
def test_build_manifest_takes_a_materialised_file_from_its_sidecar(
    proto_fixture: ReadWriteRepositoryProtocol,
    filename: str,
    dvc_md5: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A DVC-managed file's md5 sum AND size come from its sidecar (#373)."""
    res = proto_fixture.get_resource("one")
    prebuild_entries = collect_dvc_entries(proto_fixture, res)

    with caplog.at_level(logging.WARNING):
        manifest = proto_fixture.build_manifest(res, prebuild_entries)
    entry = manifest[filename]

    assert entry.md5 == dvc_md5
    assert entry.size == 3_000_000_000

    # the size the sidecar declares is not the size on disk: warn, and let
    # the sidecar win anyway
    assert any(
        filename in record.getMessage() and "dvc add" in record.getMessage()
        for record in caplog.records
    ), caplog.text

    # ... and nothing was recorded as hashed, because nothing was hashed
    assert proto_fixture.load_resource_file_state(res, filename) is None


def test_build_manifest_hashes_a_file_without_a_sidecar(
    proto_fixture: ReadWriteRepositoryProtocol,
) -> None:
    res = proto_fixture.get_resource("one")
    prebuild_entries = collect_dvc_entries(proto_fixture, res)

    manifest = proto_fixture.build_manifest(res, prebuild_entries)
    entry = manifest["data.txt"]

    assert entry.md5 == "c1cfdaf7e22865b29b8d62a564dc8f23"
    assert entry.size == 7

    state = proto_fixture.load_resource_file_state(res, "data.txt")
    assert state is not None
    assert state.md5 == entry.md5


@pytest.mark.parametrize("filename", ["sub/a.big", "b.big"])
def test_a_recorded_state_outranks_the_sidecar(
    repaired_proto_fixture: ReadWriteRepositoryProtocol,
    filename: str,
) -> None:
    """A state says "GAIn hashed these bytes"; a sidecar cannot overrule it."""
    res = repaired_proto_fixture.get_resource("one")
    assert repaired_proto_fixture.load_resource_file_state(
        res, filename) is not None

    prebuild_entries = collect_dvc_entries(repaired_proto_fixture, res)
    manifest = repaired_proto_fixture.build_manifest(res, prebuild_entries)
    entry = manifest[filename]

    assert entry.md5 == BIG_MD5
    assert entry.size == BIG_SIZE


@pytest.mark.parametrize("verify_content", [True, False])
def test_build_manifest_keeps_pointer_only_entry(
    proto_fixture: ReadWriteRepositoryProtocol,
    verify_content: bool,
) -> None:
    """`e.big` has no bytes on disk; its `.dvc` file is all there is."""
    res = proto_fixture.get_resource("clean")
    prebuild_entries = collect_dvc_entries(proto_fixture, res)

    manifest = proto_fixture.build_manifest(
        res, prebuild_entries, verify_content=verify_content)

    assert "e.big" in manifest
    assert manifest["e.big"].md5 == "eeee"
    assert manifest["e.big"].size == 3_000_000_000


def test_verify_content_hashes_and_persists_state(
    proto_fixture: ReadWriteRepositoryProtocol,
) -> None:
    """The verifier on a resource that agrees with its sidecars."""
    res = proto_fixture.get_resource("clean")
    prebuild_entries = collect_dvc_entries(proto_fixture, res)

    manifest = proto_fixture.build_manifest(
        res, prebuild_entries, verify_content=True)

    assert manifest["d.big"].md5 == BIG_MD5
    assert manifest["d.big"].size == BIG_SIZE

    state = proto_fixture.load_resource_file_state(res, "d.big")
    assert state is not None
    assert state.md5 == BIG_MD5


def test_verify_content_collects_every_drifted_file(
    proto_fixture: ReadWriteRepositoryProtocol,
) -> None:
    """Every drifted file of the resource is reported, not just the first."""
    res = proto_fixture.get_resource("one")
    prebuild_entries = collect_dvc_entries(proto_fixture, res)

    with pytest.raises(DvcContentDriftError) as excinfo:
        proto_fixture.build_manifest(
            res, prebuild_entries, verify_content=True)

    error = excinfo.value
    assert error.resource_id == "one"
    assert {drift.name for drift in error.drifts} == {"b.big", "sub/a.big"}
    assert {drift.content_md5 for drift in error.drifts} == {BIG_MD5}
    assert {drift.dvc_md5 for drift in error.drifts} == {"aaaa", "bbbb"}
    assert "dvc add" in str(error)

    # no state records an md5 sum that contradicts a sidecar
    assert proto_fixture.load_resource_file_state(res, "b.big") is None


def test_a_materialised_file_the_scan_skipped_falls_back_to_its_sidecar(
    proto_fixture: ReadWriteRepositoryProtocol,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file the scan stops yielding must not vanish from the manifest.

    Whether the bytes are on disk is not the question the merge asks: a file
    the scan did not describe has no scanned entry to fill in, so its sidecar
    is the only source there is - exactly as for a pointer-only clone (#373).
    """
    res = proto_fixture.get_resource("one")
    prebuild_entries = collect_dvc_entries(proto_fixture, res)
    assert proto_fixture.file_exists(res, "b.big")

    original_scan = proto_fixture.collect_resource_entries

    def scan_without_b_big(resource: GenomicResource) -> Manifest:
        result = Manifest()
        for entry in original_scan(resource):
            if entry.name != "b.big":
                result.add(entry)
        return result

    monkeypatch.setattr(
        proto_fixture, "collect_resource_entries", scan_without_b_big)

    manifest = proto_fixture.build_manifest(res, prebuild_entries)

    assert "b.big" in manifest
    assert manifest["b.big"].md5 == "bbbb"


def test_build_update_manifest_rehashes_a_changed_file_without_a_sidecar(
    proto_fixture: ReadWriteRepositoryProtocol,
) -> None:
    res = proto_fixture.get_resource("one")
    prebuild_entries = collect_dvc_entries(proto_fixture, res)

    proto_fixture.save_manifest(
        res, proto_fixture.build_manifest(res, prebuild_entries))

    with proto_fixture.open_raw_file(res, "data.txt", "wt") as outfile:
        outfile.write("bigger")

    manifest = proto_fixture.update_manifest(res, prebuild_entries)
    proto_fixture.save_manifest(res, manifest)

    entry = proto_fixture.load_manifest(res)["data.txt"]
    assert entry.md5 == "7de99d55a70b4e1215218f00d95a9720"
    assert entry.size == 6


@pytest.mark.parametrize("filename", ["sub/a.big", "b.big"])
def test_build_update_manifest_keeps_the_sidecar_of_a_changed_file(
    proto_fixture: ReadWriteRepositoryProtocol,
    filename: str,
) -> None:
    """Editing a DVC-managed file in place does not move its manifest entry.

    What clients download is the DVC cache object, so the manifest keeps
    describing it; the drift is a workflow error, and `grr_manage -D` is
    what hunts for it.
    """
    res = proto_fixture.get_resource("one")
    prebuild_entries = collect_dvc_entries(proto_fixture, res)

    proto_fixture.save_manifest(
        res, proto_fixture.build_manifest(res, prebuild_entries))

    with proto_fixture.open_raw_file(res, filename, "wt") as outfile:
        outfile.write("bigger")

    manifest = proto_fixture.update_manifest(res, prebuild_entries)

    assert manifest[filename].md5 == prebuild_entries[filename].md5
    assert manifest[filename].size == 3_000_000_000
