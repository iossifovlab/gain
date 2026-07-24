# pylint: disable=W0621,C0114,C0116,W0212,W0613
import hashlib
import logging
import os
import pathlib
import textwrap
from collections import Counter

import pytest
from gain.genomic_resources import cli as cli_module
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.fsspec_protocol import FsspecReadWriteProtocol
from gain.genomic_resources.repository import (
    GR_GENERATED_INFO_PAGES,
    GR_INDEX_FILE_NAME,
    GR_STATISTICS_INDEX_FILE_NAME,
    GenomicResource,
    ReadWriteRepositoryProtocol,
    ResourceFileState,
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
def drifted_repo_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[pathlib.Path, FsspecReadWriteProtocol]:
    """Build a GRR with drifted files in TWO resources, and a clean one.

    Every drifted file was edited in place preserving its size, so only
    hashing its content can tell it apart from what its sidecar declares.
    """
    path = tmp_path_factory.mktemp("cli_dvc_drifted_repo")
    setup_directories(path, {
        "one": {
            "genomic_resource.yaml": "",
            "first.txt": SAME_SIZE_TAMPERED_DATA,
            "first.txt.dvc": dvc_sidecar("first.txt", ORIGINAL_DATA),
            "second.txt": SAME_SIZE_TAMPERED_DATA,
            "second.txt.dvc": dvc_sidecar("second.txt", ORIGINAL_DATA),
        },
        "two": {
            "genomic_resource.yaml": "",
            "third.txt": SAME_SIZE_TAMPERED_DATA,
            "third.txt.dvc": dvc_sidecar("third.txt", ORIGINAL_DATA),
        },
        "clean": {
            "genomic_resource.yaml": "",
            "ok.txt": ORIGINAL_DATA,
            "ok.txt.dvc": dvc_sidecar("ok.txt", ORIGINAL_DATA),
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


def test_default_repair_keeps_the_sidecar_md5_of_an_edited_file(
    dvc_proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
) -> None:
    """The accepted consequence of trusting the sidecar (#373).

    Editing a DVC-managed file in place without `dvc add` leaves the default
    manifest certifying the sidecar's md5 sum - which is the md5 sum of the
    DVC cache object clients actually download and verify. A working tree
    that has drifted from DVC is a workflow error, reported by `dvc status`
    and hunted by `grr_manage -D`.
    """
    # Given a resource whose manifest agrees with its .dvc sidecar
    path, proto = dvc_proto_fixture
    cli_manage(["resource-manifest", "-R", str(path), "-r", "one"])

    res = proto.get_resource("one")
    manifest = proto.load_manifest(res)
    assert manifest["data.txt"].md5 == md5_of(ORIGINAL_DATA)

    # When the data file is edited in place, leaving the sidecar stale
    (path / "one" / "data.txt").write_text(TAMPERED_DATA, encoding="utf8")

    cli_manage(["resource-repair", "-R", str(path), "-r", "one"])

    # Then the manifest still carries what the sidecar declares
    res = proto.get_resource("one")
    manifest = proto.load_manifest(res)
    assert manifest["data.txt"].md5 == md5_of(ORIGINAL_DATA)
    assert manifest["data.txt"].size == size_of(ORIGINAL_DATA)


def test_force_manifest_rebuild_still_takes_the_sidecar_md5(
    dvc_proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
    md5_spy: list[str],
) -> None:
    # Given a resource whose manifest agrees with its .dvc sidecar
    path, proto = dvc_proto_fixture
    cli_manage(["resource-manifest", "-R", str(path), "-r", "one"])

    # When the data file is edited in place and the manifest is rebuilt
    (path / "one" / "data.txt").write_text(TAMPERED_DATA, encoding="utf8")
    md5_spy.clear()

    cli_manage([
        "resource-manifest", "-R", str(path), "-r", "one", "--force"])

    # Then the file is still not hashed and the sidecar still wins
    assert "data.txt" not in md5_spy
    res = proto.get_resource("one")
    manifest = proto.load_manifest(res)
    assert manifest["data.txt"].md5 == md5_of(ORIGINAL_DATA)
    assert manifest["data.txt"].size == size_of(ORIGINAL_DATA)

    # ... and no state is recorded for an md5 sum GAIn did not compute
    assert proto.load_resource_file_state(res, "data.txt") is None


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
def test_without_dvc_fails_on_a_file_that_drifted_from_its_sidecar(
    stale_dvc_proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
    dvc_args: list[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`-D` is the verifier: it hunts drift and fails the run on it."""
    # Given a resource whose .dvc sidecar disagrees with the file on disk
    path, _proto = stale_dvc_proto_fixture

    # When the manifest is built with '--without-dvc' / '-D'
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as excinfo:
        cli_manage([
            "resource-manifest", "-R", str(path), "-r", "one", *dvc_args])

    # Then the run fails, names the drifted file, and writes no manifest
    assert excinfo.value.code == 1
    assert "data.txt" in caplog.text
    assert md5_of(TAMPERED_DATA) in caplog.text
    assert md5_of(ORIGINAL_DATA) in caplog.text
    assert not (path / "one" / ".MANIFEST").exists()


@pytest.mark.parametrize("dvc_args", [[], ["--with-dvc"]])
def test_a_materialised_file_takes_its_md5_from_its_sidecar(
    dvc_proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
    md5_spy: list[str],
    dvc_args: list[str],
) -> None:
    """The default mode never hashes a DVC-managed file (#373).

    `dvc add` computed the sidecar's md5 sum from the very bytes it stored,
    and what a client downloads is that cache object - so the sidecar is the
    md5 sum of the file, and re-deriving it costs a full read of the GRR.
    """
    # Given a stateless resource, a materialised file and its sidecar
    path, proto = dvc_proto_fixture

    # When the manifest is built
    cli_manage([
        "resource-manifest", "-R", str(path), "-r", "one", *dvc_args])

    # Then the file's content was never hashed
    assert "data.txt" not in md5_spy

    # ... its manifest entry carries the sidecar's md5 sum AND size
    res = proto.get_resource("one")
    manifest = proto.load_manifest(res)
    assert manifest["data.txt"].md5 == md5_of(ORIGINAL_DATA)
    assert manifest["data.txt"].size == size_of(ORIGINAL_DATA)

    # ... and no state is written: a state means "GAIn hashed these bytes"
    assert proto.load_resource_file_state(res, "data.txt") is None
    assert not (path / "one" / ".grr" / "data.txt.state").exists()


def test_without_dvc_hashes_a_clean_file_and_persists_its_state(
    dvc_proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
    md5_spy: list[str],
) -> None:
    """`-D` on a GRR that agrees with its sidecars succeeds, and records it."""
    # Given a stateless resource whose sidecar tells the truth
    path, proto = dvc_proto_fixture

    # When the manifest is built with '-D'
    cli_manage(["resource-manifest", "-R", str(path), "-r", "one", "-D"])

    # Then the file was hashed, and the content-derived state persisted
    assert "data.txt" in md5_spy
    res = proto.get_resource("one")
    state = proto.load_resource_file_state(res, "data.txt")
    assert state is not None
    assert state.md5 == md5_of(ORIGINAL_DATA)
    assert state.size == size_of(ORIGINAL_DATA)
    assert proto.load_manifest(res)["data.txt"].md5 == md5_of(ORIGINAL_DATA)


def test_without_dvc_rehashes_content_even_when_a_state_exists(
    dvc_proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
    md5_spy: list[str],
) -> None:
    """`-D` is the audit mode: it bypasses the size/timestamp fast path.

    Every real GRR has recorded file states. If `-D` consulted them, it would
    verify nothing at all.
    """
    # Given a resource with a recorded file state (what a `-D` run leaves)
    path, proto = dvc_proto_fixture
    cli_manage(["resource-manifest", "-R", str(path), "-r", "one", "-D"])
    res = proto.get_resource("one")
    assert proto.load_resource_file_state(res, "data.txt") is not None
    md5_spy.clear()

    # When the manifest is checked with '-D' again
    cli_manage(["resource-manifest", "-R", str(path), "-r", "one", "-D"])

    # Then the file's content is hashed despite the matching state
    assert "data.txt" in md5_spy

    # ... and the default mode still skips the hashing
    md5_spy.clear()
    cli_manage(["resource-manifest", "-R", str(path), "-r", "one"])
    assert md5_spy == []


def test_a_sidecar_that_disagrees_about_the_size_warns_and_still_wins(
    stale_dvc_proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Drift the default mode notices for free is warned about (#373).

    The scan already stats every file, so comparing that size with the one
    the sidecar declares costs nothing. The sidecar still wins both fields -
    only `-D` may overrule it, and only by reading the bytes.
    """
    # Given a resource whose sidecar declares a different size
    path, proto = stale_dvc_proto_fixture
    assert size_of(TAMPERED_DATA) != size_of(ORIGINAL_DATA)

    # When the manifest is built in the default mode
    with caplog.at_level(logging.WARNING):
        cli_manage(["resource-manifest", "-R", str(path), "-r", "one"])

    # Then a warning names the file and the remedy
    warnings = [
        record.getMessage() for record in caplog.records
        if record.levelno == logging.WARNING
    ]
    message = next(
        (msg for msg in warnings if "data.txt" in msg and "dvc add" in msg),
        None)
    assert message is not None, warnings
    assert "dvc commit" in message

    # ... and the sidecar still supplies both the md5 sum and the size
    res = proto.get_resource("one")
    manifest = proto.load_manifest(res)
    assert manifest["data.txt"].md5 == md5_of(ORIGINAL_DATA)
    assert manifest["data.txt"].size == size_of(ORIGINAL_DATA)


def test_size_preserving_edit_is_only_caught_by_the_verifier(
    stale_same_size_dvc_proto_fixture: tuple[
        pathlib.Path, FsspecReadWriteProtocol],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An in-place edit that preserves the size: invisible without hashing.

    The default mode cannot see it and does not pretend to - it certifies
    what DVC published. `-D` reads the bytes, and fails.
    """
    # Given a stateless GRR whose file was edited in place, size unchanged
    path, proto = stale_same_size_dvc_proto_fixture

    # When the resource is repaired in the default mode
    cli_manage(["resource-repair", "-R", str(path), "-r", "one"])

    # Then the manifest describes what DVC published
    res = proto.get_resource("one")
    manifest = proto.load_manifest(res)
    assert manifest["data.txt"].md5 == md5_of(ORIGINAL_DATA)

    # When the same GRR is verified with '-D'
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as excinfo:
        cli_manage([
            "resource-repair", "-R", str(path), "-r", "one", "-D"])

    # Then the edit is caught and the run fails
    assert excinfo.value.code == 1
    assert "data.txt" in caplog.text
    assert md5_of(SAME_SIZE_TAMPERED_DATA) in caplog.text


def test_without_dvc_reports_every_drifted_file(
    drifted_repo_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The verifier collects; it does not stop at the first drifted file."""
    # Given a GRR with two drifted files in <one> and one in <two>
    path, _proto = drifted_repo_fixture

    # When the whole repository is verified
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as excinfo:
        cli_manage(["repo-repair", "-R", str(path), "-D"])

    # Then the run fails naming EVERY drifted file...
    assert excinfo.value.code == 1
    for name in ("first.txt", "second.txt", "third.txt"):
        assert name in caplog.text, caplog.text

    # ... and both affected resources, in the run summary
    assert "one" in caplog.text
    assert "two" in caplog.text

    # ... no manifest is written for either of them
    assert not (path / "one" / ".MANIFEST").exists()
    assert not (path / "two" / ".MANIFEST").exists()

    # ... while the resource that agrees with its sidecars is repaired
    assert (path / "clean" / ".MANIFEST").exists()


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
# real GRR has once its data is `dvc pull`ed, and the shape the whole DVC
# policy is about: the materialised data file reaches the manifest *only*
# through `FsspecReadWriteProtocol._is_dvc_managed_leaf`'s gitignore
# exemption, and its md5 sum comes from the sidecar beside it.


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


def test_gitignored_dvc_leaf_takes_its_md5_from_its_sidecar(
    gitignored_dvc_proto_fixture: tuple[
        pathlib.Path, FsspecReadWriteProtocol],
    md5_spy: list[str],
) -> None:
    """The production shape, and the whole point of #373: zero md5 sums."""
    # Given a GRR in the production `dvc add <file>` layout
    path, proto = gitignored_dvc_proto_fixture

    # When its manifest is built
    cli_manage(["repo-repair", "-R", str(path)])

    # Then the gitignored data file is in the manifest and was never hashed
    assert "data.txt" not in md5_spy

    manifest = proto.load_manifest(proto.get_resource("one"))
    assert manifest["data.txt"].md5 == md5_of(ORIGINAL_DATA)
    assert manifest["data.txt"].size == size_of(ORIGINAL_DATA)


def test_without_dvc_on_a_clean_grr_persists_content_derived_states(
    gitignored_dvc_proto_fixture: tuple[
        pathlib.Path, FsspecReadWriteProtocol],
    md5_spy: list[str],
) -> None:
    # Given a GRR in the production layout that agrees with its sidecars
    path, proto = gitignored_dvc_proto_fixture

    # When the whole repository is verified - it must not fail
    cli_manage(["repo-repair", "-R", str(path), "-D"])

    # Then every materialised file was hashed and its state persisted
    assert "data.txt" in md5_spy
    res = proto.get_resource("one")
    state = proto.load_resource_file_state(res, "data.txt")
    assert state is not None
    assert state.md5 == md5_of(ORIGINAL_DATA)
    assert proto.load_manifest(res)["data.txt"].md5 == md5_of(ORIGINAL_DATA)


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


def test_ancestor_gitignored_dvc_leaf_takes_its_md5_from_its_sidecar(
    tmp_path_factory: pytest.TempPathFactory,
    md5_spy: list[str],
) -> None:
    """#369 x #373: a `dvc add`-ed file matched by an *ancestor* `.gitignore`.

    The data file is gitignored only by a rule sitting ABOVE the resource
    directory (at the GRR root), never by the resource's own `.gitignore`.
    It must still reach the manifest through the `_is_dvc_managed_leaf`
    exemption -- the same as a resource-level `dvc add` layout -- so the
    default repair trusts its sidecar md5 and never hashes it (#373), while a
    plain sibling matched by the same ancestor rule is dropped.
    """
    # Given a GRR whose ROOT .gitignore ignores every `*.txt`, and a resource
    # holding a `dvc add`-ed `data.txt` plus a plain `notes.txt`.
    path = tmp_path_factory.mktemp("cli_dvc_ancestor_gitignore")
    setup_directories(path, {
        ".gitignore": "*.txt\n",
        "one": {
            "genomic_resource.yaml": "",
            "data.txt": ORIGINAL_DATA,
            "data.txt.dvc": dvc_sidecar("data.txt", ORIGINAL_DATA),
            "notes.txt": "just a note\n",
        },
    })
    proto = build_filesystem_test_protocol(path, repair=False)

    # When its manifest is built
    cli_manage(["repo-repair", "-R", str(path)])

    # Then the DVC-managed data file is kept, from its sidecar, never hashed,
    assert "data.txt" not in md5_spy
    manifest = proto.load_manifest(proto.get_resource("one"))
    assert manifest["data.txt"].md5 == md5_of(ORIGINAL_DATA)
    assert manifest["data.txt"].size == size_of(ORIGINAL_DATA)
    # while the plain sibling matched by the same ancestor rule is dropped.
    assert "notes.txt" not in manifest


def test_gitignored_dvc_leaf_tampered_before_any_state_keeps_the_sidecar(
    gitignored_stale_dvc_proto_fixture: tuple[
        pathlib.Path, FsspecReadWriteProtocol],
) -> None:
    """The production shape of the accepted consequence (#373)."""
    # Given an untracked GRR whose data file was edited in place, size intact
    path, proto = gitignored_stale_dvc_proto_fixture

    # When the GRR is repaired in the default mode
    cli_manage(["repo-repair", "-R", str(path)])

    # Then the manifest describes what DVC published
    manifest = proto.load_manifest(proto.get_resource("one"))
    assert manifest["data.txt"].md5 == md5_of(ORIGINAL_DATA)
    assert manifest["data.txt"].size == size_of(ORIGINAL_DATA)


def test_gitignored_dvc_leaf_tampered_in_place_keeps_the_sidecar(
    gitignored_dvc_proto_fixture: tuple[
        pathlib.Path, FsspecReadWriteProtocol],
) -> None:
    # Given a repaired GRR in the production layout
    path, proto = gitignored_dvc_proto_fixture
    cli_manage(["repo-repair", "-R", str(path)])

    # When the data file is edited in place, its size unchanged
    tamper(path / "one" / "data.txt", SAME_SIZE_TAMPERED_DATA)

    cli_manage(["repo-repair", "-R", str(path)])

    # Then the manifest still carries what the sidecar declares, and no
    # state was invented for an md5 sum GAIn never computed
    res = proto.get_resource("one")
    manifest = proto.load_manifest(res)
    assert manifest["data.txt"].md5 == md5_of(ORIGINAL_DATA)
    assert proto.load_resource_file_state(res, "data.txt") is None


def test_without_dvc_catches_a_timestamp_preserving_tamper(
    gitignored_dvc_proto_fixture: tuple[
        pathlib.Path, FsspecReadWriteProtocol],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """What `-D` is for: neither size nor timestamp can see this edit."""
    # Given a repaired GRR whose data file was rewritten with identical size
    # and timestamp - invisible to the fast path
    path, proto = gitignored_dvc_proto_fixture
    cli_manage(["repo-repair", "-R", str(path)])
    manifest_before = (path / "one" / ".MANIFEST").read_bytes()
    tamper(
        path / "one" / "data.txt", SAME_SIZE_TAMPERED_DATA,
        keep_timestamp=True)

    # When the GRR is audited with '-D'
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as excinfo:
        cli_manage(["repo-repair", "-R", str(path), "-D"])

    # Then the tampering is caught, the run fails and the manifest, a
    # committed artefact, is left exactly as `dvc add` last described it
    assert excinfo.value.code == 1
    assert "data.txt" in caplog.text
    assert (path / "one" / ".MANIFEST").read_bytes() == manifest_before
    manifest = proto.load_manifest(proto.get_resource("one"))
    assert manifest["data.txt"].md5 == md5_of(ORIGINAL_DATA)


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
    # tampered with - so the audit finds drift and fails the run
    path, _proto = gitignored_dvc_proto_fixture
    cli_manage(["repo-repair", "-R", str(path)])
    tamper(
        path / "one" / "data.txt", SAME_SIZE_TAMPERED_DATA,
        keep_timestamp=True)
    md5_spy.clear()

    # When the GRR is audited with '-D'
    with pytest.raises(SystemExit):
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
    """`--force` rebuilds the manifest; it still hashes nothing it need not.

    A file with a matching state keeps it; a DVC-managed one keeps its
    sidecar's md5 sum.
    """
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


# ---------------------------------------------------------------------------
# `dvc add <dir>`: a DVC-managed DIRECTORY is REFUSED (#255)
# ---------------------------------------------------------------------------
# `dvc add chunks` writes `/chunks` into `.gitignore` and drops a sibling
# `chunks.dvc` whose single out has a `.dir` md5 sum over the whole subtree.
# That md5 sum is the hash of a DVC cache object listing the children - GAIn
# can never recompute it from the resource, so it can never verify it.
#
# GAIn does not support `dvc add <dir>`. Writing the `.dir` md5 sum into the
# manifest would be a false clean bill of health (#255: a tamper inside such a
# directory was certified clean, `--without-dvc` included), and silently
# skipping the directory would leave its data unmanifested and unverified.
# `grr_manage` therefore REFUSES the resource outright, materialised or not,
# with a non-zero exit - and says to `dvc add <file>` the individual files.

CHUNK_A = "chunk A - original bytes\n"
CHUNK_B = "chunk B - original bytes\n"

# A `.dir` md5 sum no one can verify.
DIR_MD5 = "1234567890abcdef1234567890abcdef.dir"
# A plain (non-`.dir`) md5 sum: the `nfiles` count is then the only signal.
PLAIN_MD5 = "1234567890abcdef1234567890abcdef"


def dvc_dir_sidecar(
    path: str, *, size: int,
    md5: str = DIR_MD5, nfiles: int | None = 2,
) -> str:
    """Render a `dvc add <dir>` pointer.

    A real one declares BOTH signals: a `.dir`-suffixed md5 sum and an
    `nfiles` count. Either alone must be enough to recognise it.
    """
    nfiles_line = "" if nfiles is None else f"  nfiles: {nfiles}\n"
    return (
        "\nouts:\n"
        f"- md5: {md5}\n"
        f"  size: {size}\n"
        f"{nfiles_line}"
        f"  path: {path}\n"
    )


# The three shapes a directory output can present itself in. `nfiles-only` is
# not something DVC writes, but recognising a directory must not hinge on a
# single signal - and `md5` is the one an unverifiable entry would be built
# from, so a `.dir` md5 sum alone is refused too.
DIR_SIDECARS: dict[str, str] = {
    "dir-md5-and-nfiles": dvc_dir_sidecar(
        "chunks", size=size_of(CHUNK_A) + size_of(CHUNK_B)),
    "dir-md5-only": dvc_dir_sidecar(
        "chunks", size=size_of(CHUNK_A) + size_of(CHUNK_B), nfiles=None),
    "nfiles-only": dvc_dir_sidecar(
        "chunks", size=size_of(CHUNK_A) + size_of(CHUNK_B), md5=PLAIN_MD5),
}


def setup_dvc_directory_grr(
    path: pathlib.Path, *, materialised: bool,
    sidecar: str = DIR_SIDECARS["dir-md5-and-nfiles"],
) -> None:
    """Set up a GRR holding a `dvc add <dir>` output."""
    one: dict = {
        "genomic_resource.yaml": "",
        ".gitignore": "/chunks\n",
        "chunks.dvc": sidecar,
    }
    if materialised:
        one["chunks"] = {"a.txt": CHUNK_A, "b.txt": CHUNK_B}
    setup_directories(path, {"one": one})


@pytest.mark.parametrize("materialised", [True, False])
@pytest.mark.parametrize("dvc_args", [[], ["-D"]])
def test_dvc_directory_output_is_refused(
    tmp_path_factory: pytest.TempPathFactory,
    md5_spy: list[str],
    materialised: bool,
    dvc_args: list[str],
) -> None:
    """A `dvc add <dir>` output aborts the command, in every mode.

    A pointer-only `.dir` sidecar is refused just like a materialised one:
    accepting it would write an md5 sum GAIn can never verify into the
    manifest.
    """
    # Given a GRR holding a `dvc add <dir>` output
    path = tmp_path_factory.mktemp("cli_dvc_directory")
    setup_dvc_directory_grr(path, materialised=materialised)
    build_filesystem_test_protocol(path, repair=False)

    # When the GRR is repaired
    with pytest.raises(SystemExit) as excinfo:
        cli_manage(["repo-repair", "-R", str(path), *dvc_args])

    # Then the command fails
    assert excinfo.value.code == 1

    # ... nothing was hashed on the directory's behalf, and no manifest
    # certifying it was written
    assert not any(
        name == "chunks" or name.startswith("chunks/")
        for name in md5_spy), md5_spy
    assert not (path / "one" / ".MANIFEST").exists()


@pytest.mark.parametrize("shape", sorted(DIR_SIDECARS))
def test_dvc_directory_output_is_recognised_by_either_signal(
    tmp_path_factory: pytest.TempPathFactory,
    shape: str,
) -> None:
    """`.dir` md5 sum OR `nfiles`: either is enough to refuse."""
    # Given a `dvc add <dir>` output declaring only some of the signals
    path = tmp_path_factory.mktemp("cli_dvc_directory_shape")
    setup_dvc_directory_grr(
        path, materialised=True, sidecar=DIR_SIDECARS[shape])
    build_filesystem_test_protocol(path, repair=False)

    # When the GRR is repaired - it is refused
    with pytest.raises(SystemExit) as excinfo:
        cli_manage(["repo-repair", "-R", str(path)])
    assert excinfo.value.code == 1


@pytest.mark.parametrize("materialised", [True, False])
def test_dvc_directory_refusal_names_the_resource_and_the_sidecar(
    tmp_path_factory: pytest.TempPathFactory,
    caplog: pytest.LogCaptureFixture,
    materialised: bool,
) -> None:
    # Given a GRR holding a `dvc add <dir>` output
    path = tmp_path_factory.mktemp("cli_dvc_directory_message")
    setup_dvc_directory_grr(path, materialised=materialised)
    build_filesystem_test_protocol(path, repair=False)

    # When the GRR is repaired
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit):
        cli_manage(["repo-repair", "-R", str(path)])

    # Then the error names the resource and the offending `.dvc` file, says
    # what is not supported, and says what to do instead
    message = caplog.text
    assert "one" in message
    assert "chunks.dvc" in message
    assert "dvc add <dir>" in message
    assert "not supported" in message
    assert "dvc add <file>" in message


@pytest.mark.parametrize("subcommand", DVC_SUBCOMMANDS)
def test_every_manifest_subcommand_refuses_a_dvc_directory(
    tmp_path_factory: pytest.TempPathFactory,
    subcommand: str,
) -> None:
    """Every subcommand that builds or checks a manifest hits the gate."""
    # Given a GRR holding a materialised `dvc add <dir>` output
    path = tmp_path_factory.mktemp("cli_dvc_directory_subcommands")
    setup_dvc_directory_grr(path, materialised=True)
    build_filesystem_test_protocol(path, repair=False)

    args = [subcommand, "-R", str(path)]
    if subcommand.startswith("resource-"):
        args.extend(["-r", "one"])

    # When it is run - the command fails
    with pytest.raises(SystemExit) as excinfo:
        cli_manage(args)
    assert excinfo.value.code == 1
    assert not (path / "one" / ".MANIFEST").exists()


def test_a_per_file_dvc_resource_is_not_refused(
    gitignored_dvc_proto_fixture: tuple[
        pathlib.Path, FsspecReadWriteProtocol],
) -> None:
    """The refusal is for `dvc add <dir>` only - `dvc add <file>` is fine."""
    # Given a GRR in the production `dvc add <file>` layout
    path, proto = gitignored_dvc_proto_fixture

    # When the GRR is repaired - it does not raise
    cli_manage(["repo-repair", "-R", str(path)])

    # Then the manifest describes the file's own bytes
    manifest = proto.load_manifest(proto.get_resource("one"))
    assert manifest["data.txt"].md5 == md5_of(ORIGINAL_DATA)


# ---------------------------------------------------------------------------
# The other scan exclusion: the pages `resource-info` generates (#373)
# ---------------------------------------------------------------------------
# `collect_resource_entries` skips exactly `index.html` and
# `statistics/index.html` - the two pages GAIn writes itself, regenerated on
# every `resource-info` run. Every other file is resource data and is
# manifested, whatever its extension and whether or not DVC manages it.

REPORT_HTML = "<html>ORIGINAL REPORT</html>\n"
TAMPERED_REPORT_HTML = "<html>TAMPERED REPORT!</html>\n"


def test_a_plain_html_data_file_is_manifested(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """An html file a resource carries as DATA is not a generated page."""
    # Given a resource with an html file at a path GAIn never generates,
    # and no `.dvc` sidecar anywhere
    path = tmp_path_factory.mktemp("cli_plain_html_data")
    setup_directories(path, {
        "one": {
            "genomic_resource.yaml": "",
            "data.txt": ORIGINAL_DATA,
            "report.html": REPORT_HTML,
            "docs": {
                "index.html": REPORT_HTML,
            },
        },
    })
    proto = build_filesystem_test_protocol(path, repair=False)

    # When the GRR is repaired
    cli_manage(["repo-repair", "-R", str(path)])

    # Then it is manifested, from its own bytes
    manifest = proto.load_manifest(proto.get_resource("one"))
    assert manifest["report.html"].md5 == md5_of(REPORT_HTML)
    assert manifest["report.html"].size == size_of(REPORT_HTML)
    # ... and so is an `index.html` at any path other than the two generated
    assert manifest["docs/index.html"].md5 == md5_of(REPORT_HTML)


def test_materialised_dvc_html_takes_its_md5_from_its_sidecar(
    tmp_path_factory: pytest.TempPathFactory,
    md5_spy: list[str],
) -> None:
    """A DVC-managed html file is data - and its sidecar is its md5 sum."""
    # Given a materialised, DVC-managed `report.html`
    path = tmp_path_factory.mktemp("cli_dvc_html")
    setup_directories(path, {
        "one": {
            "genomic_resource.yaml": "",
            ".gitignore": "/report.html\n",
            "report.html": REPORT_HTML,
            "report.html.dvc": dvc_sidecar("report.html", REPORT_HTML),
        },
    })
    proto = build_filesystem_test_protocol(path, repair=False)

    # When the GRR is repaired
    cli_manage(["repo-repair", "-R", str(path)])

    # Then it is manifested from its sidecar, and never hashed
    manifest = proto.load_manifest(proto.get_resource("one"))
    assert manifest["report.html"].md5 == md5_of(REPORT_HTML)
    assert "report.html" not in md5_spy


@pytest.mark.parametrize("page", ["index.html", "statistics/index.html"])
def test_generated_info_pages_are_kept_out_of_the_manifest(
    tmp_path_factory: pytest.TempPathFactory,
    page: str,
) -> None:
    # Given a resource with both generated pages already written
    path = tmp_path_factory.mktemp("cli_plain_html")
    setup_directories(path, {
        "one": {
            "genomic_resource.yaml": "",
            "data.txt": ORIGINAL_DATA,
            "index.html": REPORT_HTML,
            "statistics": {
                "index.html": REPORT_HTML,
            },
        },
    })
    proto = build_filesystem_test_protocol(path, repair=False)

    # When the GRR is repaired
    cli_manage(["repo-repair", "-R", str(path)])

    # Then the generated page is not in the manifest
    manifest = proto.load_manifest(proto.get_resource("one"))
    assert page not in manifest
    assert "data.txt" in manifest


@pytest.mark.parametrize("page", ["index.html", "statistics/index.html"])
def test_a_dvc_managed_generated_page_is_kept_out_of_the_manifest(
    tmp_path_factory: pytest.TempPathFactory,
    page: str,
) -> None:
    """A sidecar cannot smuggle a generated page into the manifest.

    Not even through the merge of the entries the scan did not yield: a page
    GAIn regenerates on every run is a build artefact whoever `dvc add`ed it.
    """
    # Given a resource whose generated pages are both DVC-managed
    path = tmp_path_factory.mktemp("cli_dvc_generated_html")
    setup_directories(path, {
        "one": {
            "genomic_resource.yaml": "",
            ".gitignore": "/index.html\nstatistics/index.html\n",
            "index.html": REPORT_HTML,
            "index.html.dvc": dvc_sidecar("index.html", REPORT_HTML),
            "data.txt": ORIGINAL_DATA,
            "statistics": {
                "index.html": REPORT_HTML,
                "index.html.dvc": dvc_sidecar("index.html", REPORT_HTML),
            },
        },
    })
    proto = build_filesystem_test_protocol(path, repair=False)

    # When the GRR is repaired
    cli_manage(["repo-repair", "-R", str(path)])

    # Then the generated page is still not in the manifest
    manifest = proto.load_manifest(proto.get_resource("one"))
    assert page not in manifest
    assert "data.txt" in manifest


def test_rebuilding_the_info_pages_does_not_dirty_the_manifest(
    gitignored_dvc_proto_fixture: tuple[
        pathlib.Path, FsspecReadWriteProtocol],
) -> None:
    """The generated pages are out of the manifest, so rebuilding is free."""
    # Given a repaired GRR whose info pages have been generated
    path, _proto = gitignored_dvc_proto_fixture
    cli_manage(["repo-repair", "-R", str(path)])
    assert (path / "one" / "index.html").exists()
    assert (path / "one" / "statistics" / "index.html").exists()
    manifest_before = (path / "one" / ".MANIFEST").read_bytes()

    # When the info pages are rebuilt from scratch
    (path / "one" / "index.html").unlink()
    (path / "one" / "statistics" / "index.html").unlink()
    cli_manage(["resource-info", "-R", str(path), "-r", "one"])

    # Then the pages are back and the manifest is byte-for-byte unchanged
    assert (path / "one" / "index.html").exists()
    assert (path / "one" / "statistics" / "index.html").exists()
    assert (path / "one" / ".MANIFEST").read_bytes() == manifest_before


# ---------------------------------------------------------------------------
# A per-file `dvc add <file>` resource: the manifest bytes MUST NOT change
# ---------------------------------------------------------------------------
# Every one of the 344 `.dvc` sidecars in `iossifovlab/grr` is a per-file
# `dvc add <file>` output (0 `.dir` md5s, 0 `*html`). Every change to the DVC
# policy must therefore leave every production manifest byte-for-byte
# identical - and the materialised clone must manifest exactly like the
# pointer-only one, since both describe the same published bytes. These two
# goldens pin exactly that.

PER_FILE_DVC_MANIFEST = (
    "- md5: da31a566ae33edd55b96b3dfeae4fcf0\n"
    "  name: data.txt\n"
    "  size: 25\n"
    "- md5: 2dac74bca9bee507b6809c4800d0dff2\n"
    "  name: data.txt.dvc\n"
    "  size: 75\n"
    "- md5: d41d8cd98f00b204e9800998ecf8427e\n"
    "  name: genomic_resource.yaml\n"
    "  size: 0\n"
    "- md5: 6a99c575ab87f8c7d1ed1e52e7e349ce\n"
    "  name: statistics/stats_hash\n"
    "  size: 11\n"
)


def test_per_file_dvc_manifest_is_byte_identical(
    gitignored_dvc_proto_fixture: tuple[
        pathlib.Path, FsspecReadWriteProtocol],
) -> None:
    # Given a GRR in the production `dvc add <file>` layout, materialised
    path, _proto = gitignored_dvc_proto_fixture

    # When it is repaired
    cli_manage(["repo-repair", "-R", str(path)])

    # Then its manifest is byte-for-byte what it has always been
    text = (path / "one" / ".MANIFEST").read_text(encoding="utf8")
    assert text == PER_FILE_DVC_MANIFEST, repr(text)


def test_pointer_only_per_file_dvc_manifest_is_byte_identical(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    # Given the same resource as a `.dvc`-only clone (no data file)
    path = tmp_path_factory.mktemp("cli_dvc_golden_pointer_only")
    setup_directories(path, {
        "one": {
            "genomic_resource.yaml": "",
            ".gitignore": "/data.txt\n",
            "data.txt.dvc": dvc_sidecar("data.txt", ORIGINAL_DATA),
        },
    })

    # When it is repaired
    cli_manage(["repo-repair", "-R", str(path)])

    # Then its manifest is byte-for-byte what it has always been - and
    # identical to the materialised one: a per-file `dvc add` output manifests
    # the same whether or not its bytes have been pulled
    text = (path / "one" / ".MANIFEST").read_text(encoding="utf8")
    assert text == PER_FILE_DVC_MANIFEST, repr(text)


def _record_state_as_an_earlier_gain_would(
    proto: ReadWriteRepositoryProtocol, path: pathlib.Path,
) -> None:
    """Write the state an earlier GAIn leaves behind for a tampered file.

    Before #251, a materialised file's md5 was taken from its `.dvc`
    sidecar while its size and timestamp were read from the file on disk.
    For a file edited in place that pairs a *stale* md5 with the *current*
    bytes' size and timestamp.
    """
    (path / "one" / "data.txt").write_text(TAMPERED_DATA, encoding="utf8")
    res = proto.get_resource("one")
    proto.save_resource_file_state(res, ResourceFileState(
        "data.txt",
        proto.get_resource_file_size(res, "data.txt"),
        proto.get_resource_file_timestamp(res, "data.txt"),
        md5_of(ORIGINAL_DATA),
    ))


def test_a_content_derived_state_wins_over_the_sidecar(
    tmp_path_factory: pytest.TempPathFactory,
    md5_spy: list[str],
) -> None:
    """A state means "GAIn hashed these bytes", and it outranks a sidecar.

    The sidecar is consulted only when no state describes the file as it is
    now - so a file GAIn has already hashed is neither rehashed nor
    re-described from a `.dvc` that contradicts it (#373).
    """
    # Given a resource whose file was hashed before any sidecar existed
    path = tmp_path_factory.mktemp("cli_dvc_state_precedence")
    setup_directories(path, {
        "one": {
            "genomic_resource.yaml": "",
            "data.txt": ORIGINAL_DATA,
        },
    })
    proto = build_filesystem_test_protocol(path, repair=False)
    cli_manage(["resource-manifest", "-R", str(path), "-r", "one"])
    res = proto.get_resource("one")
    assert proto.load_resource_file_state(res, "data.txt") is not None

    # When a sidecar appears claiming the file is something else
    (path / "one" / "data.txt.dvc").write_text(
        dvc_sidecar("data.txt", TAMPERED_DATA), encoding="utf8")
    md5_spy.clear()

    cli_manage(["resource-manifest", "-R", str(path), "-r", "one"])

    # Then the state wins, and the file is not read again
    assert "data.txt" not in md5_spy
    manifest = proto.load_manifest(proto.get_resource("one"))
    assert manifest["data.txt"].md5 == md5_of(ORIGINAL_DATA)
    assert manifest["data.txt"].size == size_of(ORIGINAL_DATA)


def test_a_state_written_by_an_earlier_gain_is_trusted_whatever_wrote_it(
    dvc_proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
    md5_spy: list[str],
) -> None:
    """A DVC-declared md5 and a content-derived one are equivalent.

    This is a deliberate design decision, not an oversight.
    `ResourceFileState` does not record how its md5 was derived, and GAIn
    does not distinguish: `dvc add` computes the md5 from the very bytes it
    stores. So a state written by an earlier GAIn keeps its md5 while its
    size and timestamp match the file, and the file is NOT rehashed on
    upgrade -- no GRR pays a full re-verification pass.

    The accepted consequence, pinned here: an in-place edit that an earlier
    GAIn already baked into a state is not re-detected. `--without-dvc` is
    the escape hatch -- see the sibling test. Do not "fix" this without
    reading the upgrade note in `docs/source/changes.rst`.
    """
    path, proto = dvc_proto_fixture
    cli_manage(["resource-manifest", "-R", str(path), "-r", "one"])
    _record_state_as_an_earlier_gain_would(proto, path)

    md5_spy.clear()
    cli_manage(["resource-repair", "-R", str(path), "-r", "one"])

    # The recorded state is authoritative: no rehash, stale md5 kept.
    assert "data.txt" not in md5_spy
    manifest = proto.load_manifest(proto.get_resource("one"))
    assert manifest["data.txt"].md5 == md5_of(ORIGINAL_DATA)


def test_without_dvc_re_verifies_a_state_written_by_an_earlier_gain(
    dvc_proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
    md5_spy: list[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`--without-dvc` is the escape hatch for the case above.

    It ignores the state, reads the bytes - and, finding that they are
    neither what the state nor what the sidecar says, fails the run instead
    of recording a third answer of its own.
    """
    path, proto = dvc_proto_fixture
    cli_manage(["resource-manifest", "-R", str(path), "-r", "one"])
    _record_state_as_an_earlier_gain_would(proto, path)

    md5_spy.clear()
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as excinfo:
        cli_manage([
            "resource-repair", "-R", str(path), "-r", "one", "--without-dvc"])

    # Recorded state ignored; the drift from the sidecar is reported.
    assert "data.txt" in md5_spy
    assert excinfo.value.code == 1
    assert md5_of(TAMPERED_DATA) in caplog.text
    manifest = proto.load_manifest(proto.get_resource("one"))
    assert manifest["data.txt"].md5 == md5_of(ORIGINAL_DATA)

# ---------------------------------------------------------------------------
# What a FAILED verification is allowed to leave behind (#373)
# ---------------------------------------------------------------------------


@pytest.fixture
def unmanifested_drifted_repo_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[pathlib.Path, FsspecReadWriteProtocol]:
    """Build a GRR whose resource has NO `.MANIFEST` and a drifted file.

    Every newly added resource has this shape, so it is the common case
    rather than an exotic one. It also carries a pointer-only entry, whose
    sidecar is the only possible source of its manifest entry.
    """
    path = tmp_path_factory.mktemp("cli_dvc_unmanifested_drift")
    setup_directories(path, {
        "one": {
            "genomic_resource.yaml": "",
            "data.txt": SAME_SIZE_TAMPERED_DATA,
            "data.txt.dvc": dvc_sidecar("data.txt", ORIGINAL_DATA),
            "big.bw.dvc": dvc_sidecar("big.bw", ORIGINAL_DATA),
        },
    })
    proto = build_filesystem_test_protocol(path, repair=False)
    return path, proto


@pytest.mark.parametrize("command", ["repo-manifest", "repo-repair"])
def test_a_failed_verification_publishes_no_content_derived_md5(
    unmanifested_drifted_repo_fixture: tuple[
        pathlib.Path, FsspecReadWriteProtocol],
    command: str,
) -> None:
    """A `-D` run that failed must leave NOTHING derived from the bytes.

    Refusing to write the `.MANIFEST` is only half the promise. The run goes
    on to rebuild `.CONTENTS.json.gz` (and the repository index page) from
    every resource's manifest, and a resource that has no `.MANIFEST` yet
    falls through to the build-from-scratch fallback -- which hashes, writes
    a `ResourceFileState` and knows nothing of the `.dvc` sidecars. That
    published the very md5 sum the run had just refused to record, poisoned
    the next DEFAULT run through the state it left, and dropped the
    pointer-only entry on the way (#373).
    """
    # Given a resource with no manifest, a drifted file and a pointer-only one
    path, proto = unmanifested_drifted_repo_fixture

    # When the repository is verified
    with pytest.raises(SystemExit) as excinfo:
        cli_manage([command, "-R", str(path), "-D"])

    # Then the run fails and writes no manifest
    assert excinfo.value.code == 1
    assert not (path / "one" / ".MANIFEST").exists()

    # ... no content-derived state is left behind for the drifted file
    assert proto.load_resource_file_state(
        proto.get_resource("one"), "data.txt") is None
    assert not (path / "one" / ".grr" / "data.txt.state").exists()

    # ... and nothing the run refused to record is published either
    contents = (path / ".CONTENTS.json").read_text(encoding="utf8")
    assert md5_of(SAME_SIZE_TAMPERED_DATA) not in contents

    # ... so a later DEFAULT run still reads its md5 sums off the sidecars,
    # and still merges the pointer-only entry
    cli_manage([command, "-R", str(path)])
    manifest = proto.load_manifest(proto.get_resource("one"))
    assert manifest["data.txt"].md5 == md5_of(ORIGINAL_DATA)
    assert manifest["big.bw"].md5 == md5_of(ORIGINAL_DATA)


def test_a_failed_verification_leaves_the_published_manifest_alone(
    stale_same_size_dvc_proto_fixture: tuple[
        pathlib.Path, FsspecReadWriteProtocol],
) -> None:
    """A resource the run failed on keeps the manifest it already had.

    Nothing about the resource changed on disk, so the manifest a previous
    run committed is still the honest description of what DVC published: it
    is republished unchanged rather than replaced by a rebuilt-from-scratch
    one, and no content-derived state is left behind.
    """
    # Given a repaired GRR whose file then turns out to have drifted
    path, proto = stale_same_size_dvc_proto_fixture
    cli_manage(["repo-repair", "-R", str(path)])
    manifest_before = (path / "one" / ".MANIFEST").read_bytes()

    # When it is verified
    with pytest.raises(SystemExit) as excinfo:
        cli_manage(["repo-repair", "-R", str(path), "-D"])

    # Then the run fails, and neither the manifest nor the contents lose it
    assert excinfo.value.code == 1
    assert (path / "one" / ".MANIFEST").read_bytes() == manifest_before
    contents = (path / ".CONTENTS.json").read_text(encoding="utf8")
    assert md5_of(ORIGINAL_DATA) in contents
    assert md5_of(SAME_SIZE_TAMPERED_DATA) not in contents
    assert proto.load_resource_file_state(
        proto.get_resource("one"), "data.txt") is None


# ---------------------------------------------------------------------------
# A sidecar that lies about the SIZE: both modes must still agree (#373)
# ---------------------------------------------------------------------------

WRONG_DECLARED_SIZE = 999


@pytest.fixture
def wrong_size_dvc_proto_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[pathlib.Path, FsspecReadWriteProtocol]:
    """Build a GRR whose sidecar has the right md5 but the wrong size."""
    path = tmp_path_factory.mktemp("cli_dvc_wrong_size")
    assert size_of(ORIGINAL_DATA) != WRONG_DECLARED_SIZE
    setup_directories(path, {
        "one": {
            "genomic_resource.yaml": "",
            "data.txt": ORIGINAL_DATA,
            "data.txt.dvc": textwrap.dedent(f"""
                outs:
                - md5: {md5_of(ORIGINAL_DATA)}
                  size: {WRONG_DECLARED_SIZE}
                  path: data.txt
            """),
        },
    })
    proto = build_filesystem_test_protocol(path, repair=False)
    return path, proto


def test_both_modes_agree_when_the_sidecar_lies_about_the_size(
    wrong_size_dvc_proto_fixture: tuple[
        pathlib.Path, FsspecReadWriteProtocol],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A `.MANIFEST` must be a function of the bytes, not of the mode.

    A sidecar whose md5 sum is right and whose declared size is not is drift
    the verifier can see for free, exactly like the default mode can. Both
    warn about it and both let the sidecar win -- otherwise the same bytes
    would produce two different committed manifests depending on which flag
    the last run used (#373).
    """
    # Given a resource whose sidecar declares a size the file does not have
    path, proto = wrong_size_dvc_proto_fixture

    # When it is verified with `-D`
    with caplog.at_level(logging.WARNING):
        cli_manage(["resource-manifest", "-R", str(path), "-r", "one", "-D"])

    # Then the size mismatch is warned about, naming the file and the remedy
    warnings = [
        record.getMessage() for record in caplog.records
        if record.levelno == logging.WARNING
    ]
    message = next(
        (msg for msg in warnings if "data.txt" in msg and "dvc add" in msg),
        None)
    assert message is not None, warnings
    assert "dvc commit" in message

    # ... the sidecar still wins both fields
    manifest = proto.load_manifest(proto.get_resource("one"))
    assert manifest["data.txt"].md5 == md5_of(ORIGINAL_DATA)
    assert manifest["data.txt"].size == WRONG_DECLARED_SIZE
    verified = (path / "one" / ".MANIFEST").read_bytes()

    # ... and no content-derived state disagreeing with the sidecar is left
    assert proto.load_resource_file_state(
        proto.get_resource("one"), "data.txt") is None

    # ... and the default mode writes the identical manifest for the
    # identical bytes, this run and every run after it
    cli_manage(
        ["resource-manifest", "-R", str(path), "-r", "one", "--force"])
    assert (path / "one" / ".MANIFEST").read_bytes() == verified


# ---------------------------------------------------------------------------
# The generated pages: one source for the writer and for the excluder
# ---------------------------------------------------------------------------


def test_the_generated_pages_are_written_where_the_exclusion_looks(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The writer and the excluder must not be able to drift apart.

    Which paths `resource-info` writes and which paths the manifest excludes
    are the same question, so they are answered by the same constants (#373).
    """
    # Given the two pages the exclusion knows about, from one source
    assert set(GR_GENERATED_INFO_PAGES) == {
        GR_INDEX_FILE_NAME, GR_STATISTICS_INDEX_FILE_NAME,
    }

    # ... and a resource to render them for
    path = tmp_path_factory.mktemp("cli_generated_pages")
    setup_directories(path, {
        "one": {
            "genomic_resource.yaml": "",
            "data.txt": ORIGINAL_DATA,
        },
    })
    build_filesystem_test_protocol(path, repair=False)

    # When the generated-page paths are named something else
    monkeypatch.setattr(cli_module, "GR_INDEX_FILE_NAME", "generated.html")
    monkeypatch.setattr(
        cli_module, "GR_STATISTICS_INDEX_FILE_NAME",
        "generated-stats/index.html")
    cli_manage(["resource-info", "-R", str(path), "-r", "one"])

    # Then that is where `resource-info` writes them: the writer reads the
    # very constants the exclusion is built from
    assert (path / "one" / "generated.html").exists()
    assert (path / "one" / "generated-stats" / "index.html").exists()


def test_dry_run_writes_no_state(
    dvc_proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
) -> None:
    """`--dry-run` reports what would change; it changes nothing (#257).

    The recorded state is a derived artefact, but it is still a write into
    the resource directory - and a flag that only reports must leave the
    tree it inspects byte-identical.
    """
    # Given a stateless resource with no recorded state at all
    path, _proto = dvc_proto_fixture
    assert not (path / "one" / ".grr").exists()

    # When its manifest is checked with '--dry-run'
    with pytest.raises(SystemExit):
        cli_manage([
            "resource-manifest", "--dry-run", "-R", str(path), "-r", "one"])

    # Then nothing was recorded
    assert not (path / "one" / ".grr").exists()


def test_without_dvc_dry_run_verifies_but_records_nothing(
    dvc_proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
    md5_spy: list[str],
) -> None:
    """`-D --dry-run` still audits every file; it just keeps no receipt.

    `-D` bypasses the fast path, so it hashes every materialised file - the
    one mode where a dry run would otherwise record a state for the whole
    resource (#257).
    """
    # Given a stateless resource whose sidecar tells the truth
    path, proto = dvc_proto_fixture

    # When its manifest is checked with '-D --dry-run'
    with pytest.raises(SystemExit):
        cli_manage([
            "resource-manifest", "--dry-run", "-D",
            "-R", str(path), "-r", "one"])

    # Then the verification still happened
    assert "data.txt" in md5_spy

    # ... and still nothing was recorded
    res = proto.get_resource("one")
    assert proto.load_resource_file_state(res, "data.txt") is None
    assert not (path / "one" / ".grr").exists()


def test_dry_run_leaves_a_stale_state_alone(
    tmp_path_factory: pytest.TempPathFactory,
    md5_spy: list[str],
) -> None:
    """A dry run does not repair the state it finds out of date (#257).

    Rewriting it would be the most surprising kind of side effect: the run
    that promised to change nothing is the one that quietly re-certifies a
    file whose bytes have moved on.
    """
    # Given a resource whose recorded state no longer describes its file
    path = tmp_path_factory.mktemp("cli_dvc_dry_run_stale_state")
    setup_directories(path, {
        "one": {
            "genomic_resource.yaml": "",
            "data.txt": ORIGINAL_DATA,
        },
    })
    proto = build_filesystem_test_protocol(path, repair=False)
    cli_manage(["resource-manifest", "-R", str(path), "-r", "one"])
    res = proto.get_resource("one")
    assert proto.load_resource_file_state(res, "data.txt") is not None

    (path / "one" / "data.txt").write_text(TAMPERED_DATA, encoding="utf8")
    md5_spy.clear()

    # When the manifest is checked with '--dry-run'
    with pytest.raises(SystemExit):
        cli_manage([
            "resource-manifest", "--dry-run", "-R", str(path), "-r", "one"])

    # Then the new bytes were hashed to answer the question
    assert "data.txt" in md5_spy

    # ... and the state on disk still describes the bytes GAIn actually
    # hashed, rather than being quietly advanced to the new ones
    state = proto.load_resource_file_state(res, "data.txt")
    assert state is not None
    assert state.md5 == md5_of(ORIGINAL_DATA)
    assert state.size == size_of(ORIGINAL_DATA)


def test_without_dvc_dry_run_still_reports_drift_and_fails(
    stale_dvc_proto_fixture: tuple[pathlib.Path, FsspecReadWriteProtocol],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Recording nothing must not soften the verdict a dry run reaches.

    `-D --dry-run` is how a pipeline asks "would this GRR pass?" - it has to
    answer, and answer non-zero, without touching the tree (#257).
    """
    # Given a resource whose .dvc sidecar disagrees with the file on disk
    path, _proto = stale_dvc_proto_fixture

    # When it is verified with '-D --dry-run'
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as excinfo:
        cli_manage([
            "resource-manifest", "--dry-run", "-D",
            "-R", str(path), "-r", "one"])

    # Then the run still fails and still names the drifted file
    assert excinfo.value.code != 0
    assert "data.txt" in caplog.text
    assert md5_of(TAMPERED_DATA) in caplog.text

    # ... and it wrote neither a manifest nor a state
    assert not (path / "one" / ".MANIFEST").exists()
    assert not (path / "one" / ".grr").exists()


def _snapshot_tree(path: pathlib.Path) -> dict[str, tuple[bytes, int]]:
    """Capture every file under ``path`` with its bytes and mtime."""
    return {
        str(entry.relative_to(path)): (
            entry.read_bytes(), entry.stat().st_mtime_ns,
        )
        for entry in sorted(path.rglob("*")) if entry.is_file()
    }


def test_repo_repair_dry_run_leaves_the_repository_byte_identical(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The whole point of `-n`, stated as a property (#257).

    This is the shape `iossifovlab/grr`'s pipeline runs over a materialised
    clone: a check that must be able to run on a tree it does not own.
    """
    # Given a repository that has never been manifested
    path = tmp_path_factory.mktemp("cli_dvc_dry_run_byte_identical")
    setup_directories(path, {
        "one": {
            "genomic_resource.yaml": "",
            "data.txt": ORIGINAL_DATA,
            "data.txt.dvc": dvc_sidecar("data.txt", ORIGINAL_DATA),
        },
        "two": {
            "genomic_resource.yaml": "",
            "plain.txt": ORIGINAL_DATA,
        },
    })
    build_filesystem_test_protocol(path, repair=False)
    before = _snapshot_tree(path)

    # When the whole repository is checked with '--dry-run'
    with pytest.raises(SystemExit):
        cli_manage(["repo-repair", "--dry-run", "-R", str(path), "-j", "1"])

    # Then not one byte of it moved
    assert _snapshot_tree(path) == before
