"""The freshness gate of a score's stored chromosome lengths (gain#1576).

``GenomicScoreImplementation.derived_files_state(grr)`` answers whether
``statistics/chrom_lengths.json`` describes the score as it is now, and
when it does not, whether this checkout can rebuild it -- three states,
decided from the stored key, the manifest and the presence of the table
files, with no table opened.
"""

import hashlib
import pathlib
import textwrap

import pytest_mock
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.genomic_scores import GenomicScore
from gain.genomic_resources.implementations.genomic_scores_impl import (
    GenomicScoreImplementation,
    build_score_implementation_from_resource,
)
from gain.genomic_resources.repository import (
    GenomicResourceProtocolRepo,
    GenomicResourceRepo,
)
from gain.genomic_resources.resource_implementation import (
    DerivedFilesState,
)
from gain.genomic_resources.testing import build_filesystem_test_protocol

from .test_genomic_scores_impl_chrom_lengths import (
    a_labelled_tabix_score_grr,
    set_label,
)


def _a_repaired_labelled_score(
    tmp_path: pathlib.Path,
) -> tuple[GenomicScoreImplementation, GenomicResourceRepo]:
    """The labelled tabix score, repaired, and a fresh view of its repo."""
    a_labelled_tabix_score_grr().build_repo(tmp_path)
    cli_manage([
        "resource-stats", "-r", "score", "-R", str(tmp_path), "-j", "1"])
    return _resynced(tmp_path)


def _resynced(
    tmp_path: pathlib.Path,
) -> tuple[GenomicScoreImplementation, GenomicResourceRepo]:
    """A fresh implementation over the resource as it is on disk now.

    The manifest is brought up to date the way ``grr_manage`` does it,
    sidecars included; the protocol is then built WITHOUT the test
    factory's own repair, which would manifest the resource again
    without them.
    """
    cli_manage(["resource-manifest", "-r", "score", "-R", str(tmp_path)])
    repo = GenomicResourceProtocolRepo(
        build_filesystem_test_protocol(tmp_path, repair=False))
    return build_score_implementation_from_resource(
        repo.get_resource("score")), repo


def test_a_repaired_score_is_current(tmp_path: pathlib.Path) -> None:
    impl, repo = _a_repaired_labelled_score(tmp_path)

    assert impl.derived_files_state(repo) is DerivedFilesState.CURRENT


def test_the_gate_opens_no_table(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
) -> None:
    """Compares keys; on a tabix score the probe is the cost spared."""
    impl, repo = _a_repaired_labelled_score(tmp_path)
    opened = mocker.spy(GenomicScore, "open")

    impl.derived_files_state(repo)

    opened.assert_not_called()


def test_a_repointed_label_makes_the_stored_lengths_stale(
    tmp_path: pathlib.Path,
) -> None:
    """The key names the label the genome resolved from."""
    _a_repaired_labelled_score(tmp_path)
    set_label(tmp_path, "score", "reference_genome", "other_genome")

    impl, repo = _resynced(tmp_path)

    assert impl.derived_files_state(repo) is DerivedFilesState.STALE


def test_a_replaced_table_file_makes_the_stored_lengths_stale(
    tmp_path: pathlib.Path,
) -> None:
    """The key holds the manifest md5 of every table file; new bytes,
    new md5."""
    _a_repaired_labelled_score(tmp_path)
    index = tmp_path / "score" / "data.txt.gz.tbi"
    index.write_bytes(index.read_bytes() + b"\0")

    impl, repo = _resynced(tmp_path)

    assert impl.derived_files_state(repo) is DerivedFilesState.STALE


def _left_as_a_pointer(tmp_path: pathlib.Path, file_name: str) -> None:
    """Replace one table file with the ``.dvc`` sidecar that describes
    it: an unpulled DVC checkout of the same resource."""
    payload = tmp_path / "score" / file_name
    content = payload.read_bytes()
    md5 = hashlib.md5(  # ruff: ignore[hashlib-insecure-hash-function]
        content).hexdigest()
    payload.with_name(payload.name + ".dvc").write_text(textwrap.dedent(f"""
        outs:
        - md5: {md5}
          size: {len(content)}
          path: {payload.name}
    """))
    payload.unlink()


def test_a_current_file_stays_current_when_the_payload_is_a_pointer(
    tmp_path: pathlib.Path,
) -> None:
    """The sidecar supplies the manifest md5 the key compares against."""
    _a_repaired_labelled_score(tmp_path)
    _left_as_a_pointer(tmp_path, "data.txt.gz")

    impl, repo = _resynced(tmp_path)

    assert impl.derived_files_state(repo) is DerivedFilesState.CURRENT


def test_a_stale_file_whose_payload_is_a_pointer_cannot_be_rebuilt_here(
    tmp_path: pathlib.Path,
) -> None:
    """The bytes are somewhere -- the sidecar says so -- just not here."""
    _a_repaired_labelled_score(tmp_path)
    (tmp_path / "score" / "statistics" / "chrom_lengths.json").unlink()
    _left_as_a_pointer(tmp_path, "data.txt.gz")

    impl, repo = _resynced(tmp_path)

    assert impl.derived_files_state(repo) is \
        DerivedFilesState.PAYLOAD_ABSENT


def test_a_table_file_missing_without_a_sidecar_is_not_an_unpulled_payload(
    tmp_path: pathlib.Path,
) -> None:
    """Nothing says the bytes exist anywhere: the resource is broken,
    and the rebuild is what fails it, as any read of it would."""
    _a_repaired_labelled_score(tmp_path)
    (tmp_path / "score" / "statistics" / "chrom_lengths.json").unlink()
    (tmp_path / "score" / "data.txt.gz").unlink()

    impl, repo = _resynced(tmp_path)

    assert impl.derived_files_state(repo) is DerivedFilesState.STALE
