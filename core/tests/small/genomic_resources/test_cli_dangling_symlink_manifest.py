# pylint: disable=W0621,C0114,C0116,W0212,W0613
import hashlib
import logging
import os
import pathlib
import textwrap

import pytest
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.repository import UnreadableResourceFilesError
from gain.genomic_resources.testing import (
    build_filesystem_test_protocol,
    build_inmemory_test_protocol,
    setup_directories,
)

DATA = "chrom\tpos_begin\ts\n1\t1\t0.1\n"

# A link target that cannot exist. This is the shape a shared DVC cache
# leaves behind when its cache entry is garbage collected: the link is
# still committed, the bytes it names are gone (gain#503).
GONE = "/nonexistent/dvc-cache/ab/cdef0123456789"


def md5_of(content: str) -> str:
    return hashlib.md5(  # noqa: S324
        content.encode("utf8")).hexdigest()


def size_of(content: str) -> int:
    return len(content.encode("utf8"))


def dvc_sidecar(path: str, content: str) -> str:
    return textwrap.dedent(f"""
        outs:
        - md5: {md5_of(content)}
          size: {size_of(content)}
          path: {path}
    """)


@pytest.fixture
def dangling_repo(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    """A GRR whose middle resource carries a dangling symlink.

    The resource ids sort around the offending one on purpose: `aaa` is
    manifested before it is reached and `zzz` only after, so a run that
    aborts on the dangling link is visible as a resource that never got a
    manifest at all (gain#503).
    """
    path = tmp_path_factory.mktemp("dangling_symlink")
    setup_directories(path, {
        "aaa": {
            "genomic_resource.yaml": "",
            "data.txt": DATA,
        },
        "mid": {
            "genomic_resource.yaml": "",
            "data.txt": DATA,
        },
        "zzz": {
            "genomic_resource.yaml": "",
            "data.txt": DATA,
        },
    })
    os.symlink(GONE, path / "mid" / "dangling.bin")
    return path


def _make_broken_link(directory: pathlib.Path, shape: str) -> None:
    """Create a link under ``directory`` that cannot be stat'ed.

    Every shape here is indistinguishable from the others to a user --
    `Path.exists()` is False for all of them -- but the errno differs, and
    only ENOENT used to be handled (gain#503). ELOOP and ENOTDIR are used
    rather than EACCES because they behave identically for root, which CI
    may well be.
    """
    if shape == "enoent":
        os.symlink(GONE, directory / "broken.bin")
    elif shape == "eloop":
        os.symlink("loop_b", directory / "loop_a")
        os.symlink("loop_a", directory / "loop_b")
    elif shape == "enotdir":
        # The target's PARENT is a regular file, so resolving it is not
        # 'missing' but 'not a directory'.
        (directory / "afile").write_text("x", encoding="utf8")
        os.symlink(directory / "afile" / "nope", directory / "broken.bin")
    else:
        raise AssertionError(shape)


@pytest.mark.parametrize("shape", ["enoent", "eloop", "enotdir"])
def test_any_unreadable_link_shape_fails_only_its_resource(
    tmp_path_factory: pytest.TempPathFactory,
    caplog: pytest.LogCaptureFixture,
    shape: str,
) -> None:
    """Not just ENOENT: any link that cannot be stat'ed (gain#503).

    A symlink into a shared DVC cache fails to resolve for more reasons
    than a garbage-collected cache object -- a loop, a target whose parent
    is not a directory, a cache directory the run cannot traverse. All of
    them used to abort the whole repository run.
    """
    # Given a repository whose middle resource carries a broken link
    path = tmp_path_factory.mktemp(f"broken_{shape}")
    setup_directories(path, {
        "aaa": {"genomic_resource.yaml": "", "data.txt": DATA},
        "mid": {"genomic_resource.yaml": "", "data.txt": DATA},
        "zzz": {"genomic_resource.yaml": "", "data.txt": DATA},
    })
    _make_broken_link(path / "mid", shape)

    # When the whole repository is manifested
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as excinfo:
        cli_manage(["repo-manifest", "-R", str(path)])

    # Then only the offending resource fails ...
    assert excinfo.value.code == 1
    assert "mid" in caplog.text
    assert not (path / "mid" / ".MANIFEST").exists()

    # ... and the resources on both sides of it are still manifested
    assert (path / "aaa" / ".MANIFEST").exists()
    assert (path / "zzz" / ".MANIFEST").exists()


def test_build_manifest_refuses_to_drop_an_unreadable_file(
    dangling_repo: pathlib.Path,
) -> None:
    """`build_manifest` must fail rather than shrink the manifest.

    It is a writer in its own right (`build_inmemory_protocol` saves what
    it returns), so the guard has to hold here and not only on the
    `check_update_manifest` path the CLI happens to use (gain#503).
    """
    # Given a resource carrying a dangling symlink and no sidecar for it
    proto = build_filesystem_test_protocol(dangling_repo, repair=False)
    res = proto.get_resource("mid")

    # When its manifest is built directly
    with pytest.raises(UnreadableResourceFilesError) as excinfo:
        proto.build_manifest(res)

    # Then it fails naming the file, instead of returning a manifest that
    # silently omits it
    assert "dangling.bin" in str(excinfo.value)


def test_a_remote_protocol_never_swallows_an_unreadable_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a local filesystem has symlinks (gain#503).

    A remote store that lists a key and then cannot describe it is far
    likelier to be a transient fault than a steady state. Letting a '.dvc'
    sidecar answer for it would publish an md5 sum for an object that is
    not in the bucket, so the error must still propagate.
    """
    # Given a non-local protocol whose store fails to describe a file
    proto = build_inmemory_test_protocol({
        "one": {
            "genomic_resource.yaml": "",
            "data.txt": DATA,
        },
    })
    assert proto.scheme != "file"

    def explode(_path: str) -> int:
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(proto, "_get_filepath_size", explode)

    # When the resource is scanned, the failure is not swallowed
    with pytest.raises(FileNotFoundError):
        proto.scan_resource_entries(proto.get_resource("one"))


def test_listing_a_repository_survives_a_broken_resource(
    dangling_repo: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`list` builds a manifest lazily; it must not die doing so.

    A resource with no committed '.MANIFEST' has one built on demand
    during ENUMERATION, which is the one place this codebase refuses to
    raise from -- ADR 0003-resource-file-name-containment and the gain#464
    shape. Listing a repository must describe the resources it can and
    name the ones it cannot.
    """
    # Given a repository whose `mid` resource carries a dangling symlink
    # and where nothing has a committed manifest yet
    path = dangling_repo
    assert not (path / "mid" / ".MANIFEST").exists()

    # When the repository is listed
    with caplog.at_level(logging.ERROR):
        cli_manage(["list", "-R", str(path)])

    # Then the resources it can describe are listed ...
    listed = capsys.readouterr().out
    assert "aaa" in listed
    assert "zzz" in listed

    # ... and the one it cannot is named rather than fatal
    assert "mid" in caplog.text


@pytest.fixture
def dvc_dangling_repo(
    tmp_path_factory: pytest.TempPathFactory,
) -> pathlib.Path:
    """A GRR whose DVC-managed data file links into a collected cache.

    This is what DVC's ``symlink`` cache mode leaves behind once the
    shared cache is garbage collected: the sidecar still describes the
    file exactly, and the link beside it resolves to nothing (gain#503).
    """
    path = tmp_path_factory.mktemp("dvc_dangling_symlink")
    setup_directories(path, {
        "one": {
            "genomic_resource.yaml": "",
            ".gitignore": "/data.txt\n",
            "data.txt.dvc": dvc_sidecar("data.txt", DATA),
        },
    })
    os.symlink(GONE, path / "one" / "data.txt")
    return path


def test_dvc_managed_dangling_symlink_is_taken_from_the_sidecar(
    dvc_dangling_repo: pathlib.Path,
) -> None:
    """The sidecar describes the file; the missing bytes are not needed."""
    # Given a DVC-managed data file whose cache entry is gone
    path = dvc_dangling_repo
    assert (path / "one" / "data.txt").is_symlink()
    assert not (path / "one" / "data.txt").exists()

    # When the repository is manifested
    cli_manage(["repo-manifest", "-R", str(path)])

    # Then the run succeeds and the entry comes from the sidecar, exactly
    # as it does for a pointer-only clone that never materialised at all
    manifest = (path / "one" / ".MANIFEST").read_text(encoding="utf8")
    assert md5_of(DATA) in manifest
    assert "data.txt" in manifest


def test_a_rescued_file_is_reported_once_and_accurately(
    dvc_dangling_repo: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One message per file, and it must be true when it is written.

    A resource is scanned more than once per command, so a message emitted
    from the scan is emitted repeatedly -- and the scan cannot yet know
    whether a sidecar will answer for the file, so anything it says about
    that is a guess (gain#503).
    """
    # Given a DVC-managed data file whose cache entry is gone
    path = dvc_dangling_repo

    # When the repository is manifested
    with caplog.at_level(logging.DEBUG):
        cli_manage(["repo-manifest", "-R", str(path)])

    # Then exactly one warning names it, and says what actually happened
    warnings = [
        record.getMessage() for record in caplog.records
        if record.levelno == logging.WARNING
        and "could not be read" in record.getMessage()
    ]
    assert len(warnings) == 1, warnings
    assert "sidecar" in warnings[0]
    # ... including WHY it could not be read, which is the diagnosis
    assert GONE in warnings[0]


def test_dangling_dvc_link_manifests_as_if_it_were_absent(
    dvc_dangling_repo: pathlib.Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """A collected cache link and a never-pulled file are indistinguishable.

    Both are 'the sidecar is all there is', so both must produce the very
    same '.MANIFEST' -- a committed artefact must not record which machine
    happened to have a stale link lying around (gain#503).
    """
    # Given the same resource twice: once with a dangling link, once with
    # nothing where the data file would be
    dangling = dvc_dangling_repo
    absent = tmp_path_factory.mktemp("dvc_absent")
    setup_directories(absent, {
        "one": {
            "genomic_resource.yaml": "",
            ".gitignore": "/data.txt\n",
            "data.txt.dvc": dvc_sidecar("data.txt", DATA),
        },
    })

    # When both are manifested
    cli_manage(["repo-manifest", "-R", str(dangling)])
    cli_manage(["repo-manifest", "-R", str(absent)])

    # Then the manifests are byte-identical
    assert (dangling / "one" / ".MANIFEST").read_text(encoding="utf8") \
        == (absent / "one" / ".MANIFEST").read_text(encoding="utf8")


def test_a_working_symlink_is_an_ordinary_manifest_entry(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Only a BROKEN link is special; a working one is just a file.

    Guards the case gain#483 deliberately allows -- a file materialised as
    a symlink -- against being swept up by the dangling-link handling.
    """
    # Given a resource whose data file is a working symlink
    path = tmp_path_factory.mktemp("working_symlink")
    setup_directories(path, {
        "one": {
            "genomic_resource.yaml": "",
            "real.txt": DATA,
        },
    })
    os.symlink(path / "one" / "real.txt", path / "one" / "linked.txt")

    # When the repository is manifested
    cli_manage(["repo-manifest", "-R", str(path)])

    # Then the link is manifested with the size and md5 of what it resolves
    # to, exactly like the file beside it
    manifest = (path / "one" / ".MANIFEST").read_text(encoding="utf8")
    assert "linked.txt" in manifest
    assert f"size: {size_of(DATA)}" in manifest
    assert md5_of(DATA) in manifest


def test_dangling_symlink_fails_its_resource_by_name(
    dangling_repo: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A dangling link is a reported resource failure, not a traceback."""
    # Given a repository whose `mid` resource carries a dangling symlink
    path = dangling_repo

    # When the whole repository is manifested
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as excinfo:
        cli_manage(["repo-manifest", "-R", str(path)])

    # Then the run fails, naming both the resource and the file
    assert excinfo.value.code == 1
    assert "mid" in caplog.text
    assert "dangling.bin" in caplog.text

    # ... and no manifest is written for the resource that failed
    assert not (path / "mid" / ".MANIFEST").exists()


def test_one_dangling_symlink_does_not_abort_the_repository(
    dangling_repo: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The failure is scoped to its resource, not to the run (gain#503)."""
    # Given a repository whose `mid` resource carries a dangling symlink
    path = dangling_repo

    # When the whole repository is manifested
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit):
        cli_manage(["repo-manifest", "-R", str(path)])

    # Then the resources on BOTH sides of it are manifested -- `zzz` is
    # only reached after the offending resource, and used to be skipped
    # entirely because the exception aborted the loop.
    assert (path / "aaa" / ".MANIFEST").exists()
    assert (path / "zzz" / ".MANIFEST").exists()
