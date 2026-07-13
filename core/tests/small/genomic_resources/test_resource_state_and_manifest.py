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


@pytest.fixture
def proto_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> ReadWriteRepositoryProtocol:
    # `repair=False` leaves the resource without recorded file states, as a
    # freshly cloned GRR is. A file's `.dvc` sidecar is consulted only when
    # there is no state for it yet.
    path = tmp_path_factory.mktemp("resource_state_and_manifest")
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
    return build_filesystem_test_protocol(
        pathlib.Path(path), repair=False)


@pytest.mark.parametrize("use_dvc,filename,expected", [
    (True, "sub/a.big", ("aaaa", 3_000_000_000)),
    (False, "sub/a.big", ("d861877da56b8b4ceb35c8cbfdf65bb4", 3)),
    (True, "b.big", ("bbbb", 3_000_000_000)),
    (False, "b.big", ("d861877da56b8b4ceb35c8cbfdf65bb4", 3)),
])
def test_build_build_manifest_use_dvc(
    proto_fixture: ReadWriteRepositoryProtocol,
    use_dvc: bool,
    filename: str,
    expected: tuple[str, int],
) -> None:

    res = proto_fixture.get_resource("one")
    prebuild_entries = {}
    if use_dvc:
        prebuild_entries = collect_dvc_entries(proto_fixture, res)

    manifest = proto_fixture.build_manifest(res, prebuild_entries)
    md5, size = expected
    entry = manifest[filename]

    assert entry.md5 == md5
    assert entry.size == size


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

    manifest = proto_fixture.update_manifest(res, prebuild_entries)
    proto_fixture.save_manifest(res, manifest)

    manifest = proto_fixture.load_manifest(res)
    entry = manifest[filename]

    assert entry.md5 == "7de99d55a70b4e1215218f00d95a9720"
    assert entry.size == 6
