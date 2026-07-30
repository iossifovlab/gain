# pylint: disable=W0621,C0114,C0116,W0212,W0613
import hashlib
import logging
import os
import pathlib
import textwrap

import pytest
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.testing import setup_directories

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
