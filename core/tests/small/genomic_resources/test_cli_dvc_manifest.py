# pylint: disable=W0621,C0114,C0116,W0212,W0613
import hashlib
import os
import pathlib
import textwrap
from collections import Counter

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
# Same length as ORIGINAL_DATA: an in-place edit that a size check cannot see.
SAME_SIZE_TAMPERED_DATA = "TAMPERED DATA - trust me\n"


def md5_of(content: str) -> str:
    return hashlib.md5(  # noqa: S324
        content.encode("utf8")).hexdigest()


def size_of(content: str) -> int:
    return len(content.encode("utf8"))


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


@pytest.fixture
def stale_same_size_dvc_proto_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[pathlib.Path, FsspecReadWriteProtocol]:
    """Build a GRR tampered with in place, preserving the file's size.

    The `.dvc` sidecar describes ORIGINAL_DATA; the file on disk holds
    different bytes of exactly the same length. No size check can tell
    them apart - only hashing the content can.
    """
    path = tmp_path_factory.mktemp("cli_dvc_stale_same_size")
    assert size_of(SAME_SIZE_TAMPERED_DATA) == size_of(ORIGINAL_DATA)
    setup_directories(path, {
        "one": {
            "genomic_resource.yaml": "",
            "data.txt": SAME_SIZE_TAMPERED_DATA,
            "data.txt.dvc": dvc_sidecar("data.txt", ORIGINAL_DATA),
        },
    })
    proto = build_filesystem_test_protocol(path, repair=False)
    return path, proto


@pytest.fixture
def pointer_only_repo_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[pathlib.Path, FsspecReadWriteProtocol]:
    """Build a multi-resource GRR that is a `.dvc`-only clone.

    This is the shape of `iossifovlab/grr`'s working copy: the `.dvc`
    sidecars are checked out, the data files are not `dvc pull`ed.
    """
    path = tmp_path_factory.mktemp("cli_dvc_pointer_only_repo")
    setup_directories(path, {
        "one": {
            "genomic_resource.yaml": "",
            "big.bw.dvc": dvc_sidecar("big.bw", ORIGINAL_DATA),
        },
        "two": {
            "genomic_resource.yaml": "",
            "scores.bw.dvc": dvc_sidecar("scores.bw", TAMPERED_DATA),
        },
    })
    proto = build_filesystem_test_protocol(path, repair=False)
    return path, proto


@pytest.fixture
def md5_spy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every file whose content gets hashed."""
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
    return hashed


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
    md5_spy: list[str],
) -> None:
    """Repeat runs keep the no-hash fast path for unchanged files."""
    # Given a resource with an up-to-date manifest and recorded file states
    path, _proto = dvc_proto_fixture
    cli_manage(["resource-manifest", "-R", str(path), "-r", "one"])
    md5_spy.clear()

    # When nothing changed and the manifest is rebuilt
    cli_manage(["resource-manifest", "-R", str(path), "-r", "one"])

    # Then no file is hashed again
    assert md5_spy == []


@pytest.mark.parametrize("dvc_args", [["--without-dvc"], ["-D"]])
def test_without_dvc_hashes_content_and_ignores_the_sidecar(
    stale_dvc_proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
    dvc_args: list[str],
) -> None:
    # Given a resource whose .dvc sidecar disagrees with the file on disk
    path, proto = stale_dvc_proto_fixture

    # When the manifest is built with '--without-dvc' / '-D'
    cli_manage([
        "resource-manifest", "-R", str(path), "-r", "one", *dvc_args])

    # Then the manifest carries the md5 of the file's actual bytes
    res = proto.get_resource("one")
    manifest = proto.load_manifest(res)
    assert manifest["data.txt"].md5 == md5_of(TAMPERED_DATA)
    assert manifest["data.txt"].size == size_of(TAMPERED_DATA)


@pytest.mark.parametrize("dvc_args", [[], ["--with-dvc"], ["-D"]])
def test_materialised_file_md5_always_comes_from_its_content(
    dvc_proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
    md5_spy: list[str],
    dvc_args: list[str],
) -> None:
    """A materialised file is hashed; its sidecar md5 is never taken as fact.

    A `.dvc` sidecar cannot be confirmed without reading the file's bytes, so
    for a file that IS on disk the md5 is always derived from content - with
    or without ``--with-dvc``.
    """
    # Given a stateless resource, a materialised file and a truthful sidecar
    path, proto = dvc_proto_fixture

    # When the manifest is built
    cli_manage([
        "resource-manifest", "-R", str(path), "-r", "one", *dvc_args])

    # Then the file's content was hashed, whatever the dvc option
    assert "data.txt" in md5_spy

    res = proto.get_resource("one")
    manifest = proto.load_manifest(res)
    assert manifest["data.txt"].md5 == md5_of(ORIGINAL_DATA)


def test_without_dvc_rehashes_content_even_when_a_state_exists(
    dvc_proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
    md5_spy: list[str],
) -> None:
    """`-D` is the audit mode: it bypasses the size/timestamp fast path.

    Every real GRR has recorded file states. If `-D` consulted them, it would
    verify nothing at all.
    """
    # Given a resource with recorded file states (a normal GRR)
    path, proto = dvc_proto_fixture
    cli_manage(["resource-manifest", "-R", str(path), "-r", "one"])
    res = proto.get_resource("one")
    assert proto.load_resource_file_state(res, "data.txt") is not None
    md5_spy.clear()

    # When the manifest is checked with '-D'
    cli_manage(["resource-manifest", "-R", str(path), "-r", "one", "-D"])

    # Then the file's content is hashed despite the matching state
    assert "data.txt" in md5_spy

    # ... and the default mode still skips the hashing
    md5_spy.clear()
    cli_manage(["resource-manifest", "-R", str(path), "-r", "one"])
    assert md5_spy == []


def test_size_preserving_edit_with_stale_sidecar_is_caught(
    stale_same_size_dvc_proto_fixture: tuple[
        pathlib.Path, FsspecReadWriteProtocol],
) -> None:
    """#251, the headline case: an in-place edit that preserves the size.

    On a fresh clone with no recorded state, a sidecar-derived md5 paired
    with a matching size would certify the tampered bytes clean forever.
    """
    # Given a stateless GRR whose file was edited in place, size unchanged
    path, proto = stale_same_size_dvc_proto_fixture

    # When the resource is repaired
    cli_manage(["resource-repair", "-R", str(path), "-r", "one"])

    # Then the manifest describes the bytes actually on disk
    res = proto.get_resource("one")
    manifest = proto.load_manifest(res)
    assert manifest["data.txt"].md5 == md5_of(SAME_SIZE_TAMPERED_DATA)
    assert manifest["data.txt"].md5 != md5_of(ORIGINAL_DATA)

    # ... and the persisted state never pairs an md5 with a size that was
    # not derived from the same bytes
    state = proto.load_resource_file_state(res, "data.txt")
    assert state is not None
    assert state.md5 == md5_of(SAME_SIZE_TAMPERED_DATA)
    assert state.size == size_of(SAME_SIZE_TAMPERED_DATA)
    # a persisted timestamp is rounded, hence the same 1e-2 tolerance the
    # fast path itself compares with
    assert state.timestamp == pytest.approx(
        (path / "one" / "data.txt").stat().st_mtime, abs=1e-2)


@pytest.mark.parametrize("dvc_args", [[], ["--with-dvc"], ["-D"]])
def test_repo_repair_never_drops_pointer_only_entries(
    pointer_only_repo_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
    dvc_args: list[str],
) -> None:
    """A pointer-only entry has no bytes to hash; the sidecar is all there is.

    `--without-dvc` must NOT delete such entries from the manifest - that
    would gut every manifest of the `.dvc`-only clone the `grr` pipeline
    builds from.
    """
    # Given a `.dvc`-only clone with correct manifests
    path, proto = pointer_only_repo_fixture
    cli_manage(["repo-repair", "-R", str(path)])

    # When repo-repair is run again, with or without dvc
    cli_manage(["repo-repair", "-R", str(path), *dvc_args])

    # Then the pointer-only entries are still there, taken from the sidecars
    one = proto.load_manifest(proto.get_resource("one"))
    assert "big.bw" in one
    assert one["big.bw"].md5 == md5_of(ORIGINAL_DATA)
    assert one["big.bw"].size == size_of(ORIGINAL_DATA)

    two = proto.load_manifest(proto.get_resource("two"))
    assert "scores.bw" in two
    assert two["scores.bw"].md5 == md5_of(TAMPERED_DATA)
    assert two["scores.bw"].size == size_of(TAMPERED_DATA)


@pytest.mark.parametrize("dvc_args", [[], ["-D"]])
def test_pointer_only_data_file_is_never_hashed(
    pointer_only_repo_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
    md5_spy: list[str],
    dvc_args: list[str],
) -> None:
    # Given a `.dvc`-only clone
    path, _proto = pointer_only_repo_fixture

    # When the repository is repaired
    cli_manage(["repo-repair", "-R", str(path), *dvc_args])

    # Then the absent data files are never hashed
    assert "big.bw" not in md5_spy
    assert "scores.bw" not in md5_spy


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


# ---------------------------------------------------------------------------
# The PRODUCTION layout: `dvc add <file>` gitignores the data file
# ---------------------------------------------------------------------------
# `dvc add data.txt` writes `/data.txt` into the resource's `.gitignore` and
# drops a sibling `data.txt.dvc` pointer next to it. That is the shape every
# real GRR has once its data is `dvc pull`ed, and the shape #251 occurs in:
# the materialised data file reaches the manifest *only* through
# `FsspecReadWriteProtocol._is_dvc_managed_leaf`'s gitignore exemption.


def setup_gitignored_dvc_grr(
    path: pathlib.Path, data: str, *, sidecar_data: str | None = None,
) -> None:
    """Set up a GRR in the production `dvc add <file>` layout."""
    if sidecar_data is None:
        sidecar_data = data
    setup_directories(path, {
        "one": {
            "genomic_resource.yaml": "",
            ".gitignore": "/data.txt\n",
            "data.txt": data,
            "data.txt.dvc": dvc_sidecar("data.txt", sidecar_data),
        },
    })


def resource_states(path: pathlib.Path) -> dict[str, bytes]:
    """Return the raw content of every recorded resource file state."""
    return {
        state.name: state.read_bytes()
        for state in sorted((path / "one" / ".grr").glob("*.state"))
    }


def tamper(
    file_path: pathlib.Path, content: str, *, keep_timestamp: bool = False,
) -> None:
    """Rewrite a file in place, optionally preserving its timestamp.

    The timestamp is always set explicitly - to the file's own previous one,
    or a full second past it - so that the size+timestamp fast path is
    exercised deterministically and not against a wall-clock race.
    """
    stat = file_path.stat()
    file_path.write_text(content, encoding="utf8")
    mtime = stat.st_mtime if keep_timestamp else stat.st_mtime + 1.0
    os.utime(file_path, (stat.st_atime, mtime))


@pytest.fixture
def gitignored_dvc_proto_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[pathlib.Path, FsspecReadWriteProtocol]:
    """Build a GRR in the production layout, sidecar and file in agreement."""
    path = tmp_path_factory.mktemp("cli_dvc_gitignored")
    setup_gitignored_dvc_grr(path, ORIGINAL_DATA)
    proto = build_filesystem_test_protocol(path, repair=False)
    return path, proto


@pytest.fixture
def gitignored_stale_dvc_proto_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[pathlib.Path, FsspecReadWriteProtocol]:
    """Build a GRR in the production layout, tampered before any state.

    The data file was edited in place - preserving its size - and the sidecar
    still describes the original bytes. This is #251 exactly as it occurs in
    a real GRR.
    """
    path = tmp_path_factory.mktemp("cli_dvc_gitignored_stale")
    setup_gitignored_dvc_grr(
        path, SAME_SIZE_TAMPERED_DATA, sidecar_data=ORIGINAL_DATA)
    proto = build_filesystem_test_protocol(path, repair=False)
    return path, proto


def test_gitignored_dvc_leaf_is_hashed_from_its_content(
    gitignored_dvc_proto_fixture: tuple[
        pathlib.Path, FsspecReadWriteProtocol],
    md5_spy: list[str],
) -> None:
    # Given a GRR in the production `dvc add <file>` layout
    path, proto = gitignored_dvc_proto_fixture

    # When its manifest is built
    cli_manage(["repo-repair", "-R", str(path)])

    # Then the gitignored data file is in the manifest, hashed from content
    assert "data.txt" in md5_spy

    manifest = proto.load_manifest(proto.get_resource("one"))
    assert manifest["data.txt"].md5 == md5_of(ORIGINAL_DATA)
    assert manifest["data.txt"].size == size_of(ORIGINAL_DATA)


def test_gitignored_dvc_leaf_is_not_rehashed_on_a_repeat_run(
    gitignored_dvc_proto_fixture: tuple[
        pathlib.Path, FsspecReadWriteProtocol],
    md5_spy: list[str],
) -> None:
    """The fast path holds for the production layout: a repeat run is free."""
    # Given a repaired GRR in the production layout
    path, _proto = gitignored_dvc_proto_fixture
    cli_manage(["repo-repair", "-R", str(path)])

    manifest_before = (path / "one" / ".MANIFEST").read_bytes()
    states_before = resource_states(path)
    assert states_before
    md5_spy.clear()

    # When nothing changed and the GRR is repaired again
    cli_manage(["repo-repair", "-R", str(path)])

    # Then nothing is hashed and nothing is rewritten
    assert md5_spy == []
    assert (path / "one" / ".MANIFEST").read_bytes() == manifest_before
    assert resource_states(path) == states_before


def test_gitignored_dvc_leaf_tampered_before_any_state_is_caught(
    gitignored_stale_dvc_proto_fixture: tuple[
        pathlib.Path, FsspecReadWriteProtocol],
) -> None:
    """#251 in the shape it actually occurs in: a gitignored DVC leaf."""
    # Given an untracked GRR whose data file was edited in place, size intact
    path, proto = gitignored_stale_dvc_proto_fixture

    # When the GRR is repaired
    cli_manage(["repo-repair", "-R", str(path)])

    # Then the manifest describes the bytes on disk, not the sidecar's claim
    manifest = proto.load_manifest(proto.get_resource("one"))
    assert manifest["data.txt"].md5 == md5_of(SAME_SIZE_TAMPERED_DATA)
    assert manifest["data.txt"].md5 != md5_of(ORIGINAL_DATA)
    assert manifest["data.txt"].size == size_of(SAME_SIZE_TAMPERED_DATA)


def test_gitignored_dvc_leaf_tampered_in_place_is_caught(
    gitignored_dvc_proto_fixture: tuple[
        pathlib.Path, FsspecReadWriteProtocol],
) -> None:
    # Given a repaired GRR in the production layout
    path, proto = gitignored_dvc_proto_fixture
    cli_manage(["repo-repair", "-R", str(path)])

    # When the data file is edited in place, its size unchanged
    tamper(path / "one" / "data.txt", SAME_SIZE_TAMPERED_DATA)

    cli_manage(["repo-repair", "-R", str(path)])

    # Then the manifest and the state describe the bytes on disk
    res = proto.get_resource("one")
    manifest = proto.load_manifest(res)
    assert manifest["data.txt"].md5 == md5_of(SAME_SIZE_TAMPERED_DATA)

    state = proto.load_resource_file_state(res, "data.txt")
    assert state is not None
    assert state.md5 == md5_of(SAME_SIZE_TAMPERED_DATA)
    assert state.size == size_of(SAME_SIZE_TAMPERED_DATA)


def test_without_dvc_catches_a_timestamp_preserving_tamper(
    gitignored_dvc_proto_fixture: tuple[
        pathlib.Path, FsspecReadWriteProtocol],
) -> None:
    """What `-D` is for: neither size nor timestamp can see this edit."""
    # Given a repaired GRR whose data file was rewritten with identical size
    # and timestamp - invisible to the fast path
    path, proto = gitignored_dvc_proto_fixture
    cli_manage(["repo-repair", "-R", str(path)])
    tamper(
        path / "one" / "data.txt", SAME_SIZE_TAMPERED_DATA,
        keep_timestamp=True)

    # When the GRR is audited with '-D'
    cli_manage(["repo-repair", "-R", str(path), "-D"])

    # Then the tampering is caught
    manifest = proto.load_manifest(proto.get_resource("one"))
    assert manifest["data.txt"].md5 == md5_of(SAME_SIZE_TAMPERED_DATA)


# ---------------------------------------------------------------------------
# Exactly one hashing pass per file per command
# ---------------------------------------------------------------------------


def test_without_dvc_hashes_every_file_exactly_once(
    gitignored_dvc_proto_fixture: tuple[
        pathlib.Path, FsspecReadWriteProtocol],
    md5_spy: list[str],
) -> None:
    """`-D` re-reads the whole GRR; it must not read it twice.

    ``check_update_manifest`` already returns the updated manifest, with every
    materialised entry's md5 derived from the file's bytes. Deriving it a
    second time through ``update_manifest`` / ``build_manifest`` doubled the
    read of every file - free in the default mode, where the states just
    persisted make the fast path hit, but a full second pass under `-D`.
    """
    # Given a repaired GRR in the production layout whose data file was then
    # tampered with - so the audit finds an entry to update and takes the
    # manifest-writing path
    path, _proto = gitignored_dvc_proto_fixture
    cli_manage(["repo-repair", "-R", str(path)])
    tamper(
        path / "one" / "data.txt", SAME_SIZE_TAMPERED_DATA,
        keep_timestamp=True)
    md5_spy.clear()

    # When the GRR is audited with '-D'
    cli_manage(["repo-repair", "-R", str(path), "-D"])

    # Then every materialised file is hashed exactly once
    counts = Counter(md5_spy)
    assert counts["data.txt"] == 1
    assert set(counts.values()) == {1}, counts


def test_without_dvc_hashes_every_file_exactly_once_when_up_to_date(
    gitignored_dvc_proto_fixture: tuple[
        pathlib.Path, FsspecReadWriteProtocol],
    md5_spy: list[str],
) -> None:
    # Given a repaired GRR in the production layout
    path, _proto = gitignored_dvc_proto_fixture
    cli_manage(["repo-repair", "-R", str(path)])
    md5_spy.clear()

    # When the unchanged GRR is audited with '-D'
    cli_manage(["repo-repair", "-R", str(path), "-D"])

    # Then every materialised file is hashed exactly once
    counts = Counter(md5_spy)
    assert counts["data.txt"] == 1
    assert set(counts.values()) == {1}, counts


def test_without_dvc_force_hashes_every_file_exactly_once(
    gitignored_dvc_proto_fixture: tuple[
        pathlib.Path, FsspecReadWriteProtocol],
    md5_spy: list[str],
) -> None:
    # Given a GRR in the production layout
    path, _proto = gitignored_dvc_proto_fixture

    # When its manifest is force-rebuilt with '-D'
    cli_manage([
        "resource-manifest", "-R", str(path), "-r", "one", "-D", "--force"])

    # Then every materialised file is hashed exactly once
    counts = Counter(md5_spy)
    assert counts["data.txt"] == 1
    assert set(counts.values()) == {1}, counts


def test_force_rebuild_does_not_rehash_unchanged_files(
    gitignored_dvc_proto_fixture: tuple[
        pathlib.Path, FsspecReadWriteProtocol],
    md5_spy: list[str],
) -> None:
    """`--force` rebuilds the manifest; it still trusts a matching state."""
    # Given a repaired GRR in the production layout
    path, _proto = gitignored_dvc_proto_fixture
    cli_manage(["repo-repair", "-R", str(path)])
    md5_spy.clear()

    # When the manifest is force-rebuilt with nothing changed
    cli_manage([
        "resource-manifest", "-R", str(path), "-r", "one", "--force"])

    # Then no file is hashed again
    assert md5_spy == []


# ---------------------------------------------------------------------------
# A malformed `.dvc` sidecar must never abort the CLI
# ---------------------------------------------------------------------------
# `_is_dvc_managed_leaf` treats any `.dvc` it cannot parse as "not a pointer"
# - "the scan must never abort on stray content". `collect_dvc_entries` must
# be exactly as tolerant, or the two classify differently and `grr_manage`
# dies with a raw traceback on a repository the scanner handles happily.

MALFORMED_SIDECARS: dict[str, str | bytes] = {
    "empty": "",
    "no-outs": "meta:\n  nothing: true\n",
    "outs-not-a-list": "outs: nonsense\n",
    "outs-is-null": "outs:\n",
    "not-a-mapping": "- just\n- a\n- list\n",
    "out-without-path": "outs:\n- md5: 0123456789abcdef0123456789abcdef\n"
                        "  size: 25\n",
    "out-without-md5": "outs:\n- size: 25\n  path: data.txt\n",
    "out-with-null-md5": "outs:\n- md5:\n  size: 25\n  path: data.txt\n",
    "out-without-size": "outs:\n- md5: 0123456789abcdef0123456789abcdef\n"
                        "  path: data.txt\n",
    "binary": b"\xff\xfe\x00",
}


@pytest.mark.parametrize("shape", sorted(MALFORMED_SIDECARS))
def test_malformed_dvc_sidecar_does_not_abort_the_cli(
    tmp_path_factory: pytest.TempPathFactory,
    shape: str,
) -> None:
    # Given a materialised resource file with an unusable `.dvc` sidecar
    path = tmp_path_factory.mktemp("cli_dvc_malformed")
    setup_directories(path, {
        "one": {
            "genomic_resource.yaml": "",
            "data.txt": ORIGINAL_DATA,
            "data.txt.dvc": MALFORMED_SIDECARS[shape],
        },
    })
    proto = build_filesystem_test_protocol(path, repair=False)

    # When the resource is repaired - it must not raise
    cli_manage(["resource-repair", "-R", str(path), "-r", "one"])

    # Then the sidecar is ignored and the file's own bytes are described
    manifest = proto.load_manifest(proto.get_resource("one"))
    assert manifest["data.txt"].md5 == md5_of(ORIGINAL_DATA)
    assert manifest["data.txt"].size == size_of(ORIGINAL_DATA)


@pytest.mark.parametrize(
    "shape", ["out-without-md5", "out-with-null-md5", "out-without-size"])
def test_dvc_sidecar_without_md5_yields_no_pointer_only_entry(
    tmp_path_factory: pytest.TempPathFactory,
    shape: str,
) -> None:
    """An unusable sidecar is skipped, never propagated as `md5: null`."""
    # Given a resource whose data file is not materialised and whose sidecar
    # declares no usable md5 sum and size
    path = tmp_path_factory.mktemp("cli_dvc_null_md5")
    setup_directories(path, {
        "one": {
            "genomic_resource.yaml": "",
            "data.txt.dvc": MALFORMED_SIDECARS[shape],
        },
    })
    proto = build_filesystem_test_protocol(path, repair=False)

    # When the manifest is built
    cli_manage(["resource-repair", "-R", str(path), "-r", "one"])

    # Then no entry is invented for it - and no `md5: null` is written
    manifest_content = (path / "one" / ".MANIFEST").read_text(encoding="utf8")
    assert "null" not in manifest_content, manifest_content

    manifest = proto.load_manifest(proto.get_resource("one"))
    assert "data.txt" not in manifest
    assert "data.txt.dvc" in manifest
