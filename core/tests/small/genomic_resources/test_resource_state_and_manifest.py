# pylint: disable=W0621,C0114,C0116,W0212,W0613

import pathlib
import textwrap

import pytest
from gain.genomic_resources.cli import collect_dvc_entries
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
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


@pytest.mark.parametrize("use_dvc", [True, False])
@pytest.mark.parametrize("filename", ["sub/a.big", "b.big"])
def test_build_manifest_hashes_materialised_file(
    proto_fixture: ReadWriteRepositoryProtocol,
    use_dvc: bool,
    filename: str,
) -> None:
    """A file that is on disk gets its md5 from its content, dvc or not.

    A `.dvc` sidecar cannot be confirmed without reading the bytes it claims
    to describe, so it is never the md5 source for a materialised file. These
    sidecars deliberately lie (md5 `aaaa`/`bbbb`, size 3e9).
    """
    res = proto_fixture.get_resource("one")
    prebuild_entries = {}
    if use_dvc:
        prebuild_entries = collect_dvc_entries(proto_fixture, res)

    manifest = proto_fixture.build_manifest(
        res, prebuild_entries, verify_content=not use_dvc)
    entry = manifest[filename]

    assert entry.md5 == BIG_MD5
    assert entry.size == BIG_SIZE


@pytest.mark.parametrize("use_dvc", [True, False])
@pytest.mark.parametrize("filename", ["sub/a.big", "b.big"])
def test_build_manifest_with_dvc_on_a_resource_that_has_state(
    repaired_proto_fixture: ReadWriteRepositoryProtocol,
    use_dvc: bool,
    filename: str,
) -> None:
    """The normal condition of every real GRR: recorded file states exist.

    The recorded state was derived from the file's content, so reusing it
    still describes the bytes on disk - the lying `.dvc` sidecar does not
    win over it.
    """
    res = repaired_proto_fixture.get_resource("one")
    assert repaired_proto_fixture.load_resource_file_state(
        res, filename) is not None

    prebuild_entries = {}
    if use_dvc:
        prebuild_entries = collect_dvc_entries(repaired_proto_fixture, res)

    manifest = repaired_proto_fixture.build_manifest(
        res, prebuild_entries, verify_content=not use_dvc)
    entry = manifest[filename]

    assert entry.md5 == BIG_MD5
    assert entry.size == BIG_SIZE


@pytest.mark.parametrize("use_dvc", [True, False])
def test_build_manifest_keeps_pointer_only_entry(
    proto_fixture: ReadWriteRepositoryProtocol,
    use_dvc: bool,
) -> None:
    """`c.big` has no bytes on disk; its `.dvc` file is all there is."""
    res = proto_fixture.get_resource("one")
    prebuild_entries = collect_dvc_entries(proto_fixture, res)

    manifest = proto_fixture.build_manifest(
        res, prebuild_entries, verify_content=not use_dvc)

    assert "c.big" in manifest
    assert manifest["c.big"].md5 == "cccc"
    assert manifest["c.big"].size == 3_000_000_000


@pytest.mark.parametrize("use_dvc", [True, False])
@pytest.mark.parametrize("filename", ["sub/a.big", "b.big"])
def test_build_update_manifest_rehashes_changed_file(
    proto_fixture: ReadWriteRepositoryProtocol,
    use_dvc: bool,
    filename: str,
) -> None:
    """A file changed on disk is hashed from content, dvc or not.

    Its `.dvc` sidecar describes the *old* bytes, so it cannot be the md5
    source for a file that has been edited in place.
    """
    res = proto_fixture.get_resource("one")

    prebuild_entries = {}
    if use_dvc:
        prebuild_entries = collect_dvc_entries(proto_fixture, res)

    proto_fixture.save_manifest(
        res, proto_fixture.build_manifest(res, prebuild_entries))

    with proto_fixture.open_raw_file(res, filename, "wt") as outfile:
        outfile.write("bigger")

    manifest = proto_fixture.update_manifest(
        res, prebuild_entries, verify_content=not use_dvc)
    proto_fixture.save_manifest(res, manifest)

    manifest = proto_fixture.load_manifest(res)
    entry = manifest[filename]

    assert entry.md5 == "7de99d55a70b4e1215218f00d95a9720"
    assert entry.size == 6
