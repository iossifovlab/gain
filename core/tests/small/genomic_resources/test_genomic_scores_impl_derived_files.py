"""The freshness gate of a score's stored chromosome lengths (gain#1576).

``GenomicScoreImplementation.derived_files_state(grr)`` answers whether
``statistics/chrom_lengths.json`` describes the score as it is now, and
when it does not, whether this checkout can rebuild it -- three states,
decided from the stored key, the manifest and the presence of the table
files, with no table opened.
"""

import pathlib
import shutil

import pytest_mock
from gain.genomic_resources.genomic_scores import GenomicScore
from gain.genomic_resources.implementations.genomic_scores_impl import (
    GenomicScoreImplementation,
    build_score_implementation_from_resource,
)
from gain.genomic_resources.repository import (
    GenomicResourceProtocolRepo,
    GenomicResourceRepo,
)
from gain.genomic_resources.repository_factory import (
    build_resource_implementation,
)
from gain.genomic_resources.resource_implementation import (
    DerivedFilesState,
)
from gain.genomic_resources.testing import build_filesystem_test_protocol
from gain.genomic_resources.testing.builders import (
    a_basic_resource,
    a_reference_genome,
)

from .conftest import leave_as_a_pointer
from .test_cli_stats_chrom_lengths import (
    resource_stats,
    resync_the_manifest,
)
from .test_genomic_scores_impl_chrom_lengths import (
    CHR1_GENOME_LENGTH,
    a_labelled_tabix_score_grr,
    set_label,
)


def a_repaired_labelled_score(
    tmp_path: pathlib.Path, *, genome_id: str = "genome",
) -> tuple[GenomicScoreImplementation, GenomicResourceRepo]:
    """The labelled tabix score, repaired, and a fresh view of its repo."""
    a_labelled_tabix_score_grr(genome_id=genome_id).build_repo(tmp_path)
    resource_stats(tmp_path, "score")
    return resynced(tmp_path)


def resynced(
    tmp_path: pathlib.Path, resource_id: str = "score",
) -> tuple[GenomicScoreImplementation, GenomicResourceRepo]:
    """A fresh implementation over the resource as it is on disk now.

    The manifest is brought up to date the way ``grr_manage`` does it,
    sidecars included; the protocol is then built WITHOUT the test
    factory's own repair, which would manifest the resource again
    without them.
    """
    resync_the_manifest(tmp_path, resource_id)
    repo = GenomicResourceProtocolRepo(
        build_filesystem_test_protocol(tmp_path, repair=False))
    return build_score_implementation_from_resource(
        repo.get_resource(resource_id)), repo


def test_a_repaired_score_is_current(tmp_path: pathlib.Path) -> None:
    impl, repo = a_repaired_labelled_score(tmp_path)

    assert impl.derived_files_state(repo) is DerivedFilesState.CURRENT


def test_the_gate_opens_no_table(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
) -> None:
    """Compares keys; on a tabix score the probe is the cost spared."""
    impl, repo = a_repaired_labelled_score(tmp_path)
    opened = mocker.spy(GenomicScore, "open")

    impl.derived_files_state(repo)

    opened.assert_not_called()


def test_a_repointed_label_makes_the_stored_lengths_stale(
    tmp_path: pathlib.Path,
) -> None:
    """The key names the label the genome resolved from."""
    a_repaired_labelled_score(tmp_path)
    set_label(tmp_path, "score", "reference_genome", "other_genome")

    impl, repo = resynced(tmp_path)

    assert impl.derived_files_state(repo) is DerivedFilesState.STALE


def test_a_replaced_table_file_makes_the_stored_lengths_stale(
    tmp_path: pathlib.Path,
) -> None:
    """The key holds the manifest md5 of every table file; new bytes,
    new md5."""
    a_repaired_labelled_score(tmp_path)
    index = tmp_path / "score" / "data.txt.gz.tbi"
    index.write_bytes(index.read_bytes() + b"\0")

    impl, repo = resynced(tmp_path)

    assert impl.derived_files_state(repo) is DerivedFilesState.STALE


def test_a_current_file_stays_current_when_the_payload_is_a_pointer(
    tmp_path: pathlib.Path,
) -> None:
    """The sidecar supplies the manifest md5 the key compares against."""
    a_repaired_labelled_score(tmp_path)
    leave_as_a_pointer(tmp_path / "score" / "data.txt.gz")

    impl, repo = resynced(tmp_path)

    assert impl.derived_files_state(repo) is DerivedFilesState.CURRENT


def test_a_stale_file_whose_payload_is_a_pointer_cannot_be_rebuilt_here(
    tmp_path: pathlib.Path,
) -> None:
    """The bytes are somewhere -- the sidecar says so -- just not here."""
    a_repaired_labelled_score(tmp_path)
    (tmp_path / "score" / "statistics" / "chrom_lengths.json").unlink()
    leave_as_a_pointer(tmp_path / "score" / "data.txt.gz")

    impl, repo = resynced(tmp_path)

    assert impl.derived_files_state(repo) is \
        DerivedFilesState.PAYLOAD_ABSENT


def test_a_genome_gone_since_the_repair_leaves_the_record_standing(
    tmp_path: pathlib.Path,
) -> None:
    """The label still names the genome the lengths came from; that the
    repository no longer has it changes nothing about the record."""
    a_repaired_labelled_score(tmp_path)
    shutil.rmtree(tmp_path / "genome")

    impl, repo = resynced(tmp_path)

    assert impl.derived_files_state(repo) is DerivedFilesState.CURRENT


def test_a_genome_that_turns_up_later_makes_the_stored_lengths_stale(
    tmp_path: pathlib.Path,
) -> None:
    """Derived with no genome -- the label named one the repository
    lacked -- the record stands only while there is still none."""
    a_repaired_labelled_score(tmp_path, genome_id="late_genome")
    (a_reference_genome()
     .with_chromosome("chr1", "A" * CHR1_GENOME_LENGTH)
     .build_resource(tmp_path / "late_genome"))

    impl, repo = resynced(tmp_path)

    assert impl.derived_files_state(repo) is DerivedFilesState.STALE


def test_a_current_file_whose_table_file_is_gone_is_stale_not_an_error(
    tmp_path: pathlib.Path,
) -> None:
    """The manifest no longer lists the file, so the key cannot even be
    computed; the missing input is classified before the key is read."""
    a_repaired_labelled_score(tmp_path)
    (tmp_path / "score" / "data.txt.gz").unlink()

    impl, repo = resynced(tmp_path)

    assert impl.derived_files_state(repo) is DerivedFilesState.STALE


def test_one_unpulled_and_one_plainly_missing_input_is_a_broken_resource(
    tmp_path: pathlib.Path,
) -> None:
    """A sidecar vouches for the index; nothing vouches for the data
    file.  (The other way round the index drops out of the manifest and
    so out of the file set, and there is only one missing file.)"""
    a_repaired_labelled_score(tmp_path)
    (tmp_path / "score" / "statistics" / "chrom_lengths.json").unlink()
    leave_as_a_pointer(tmp_path / "score" / "data.txt.gz.tbi")
    (tmp_path / "score" / "data.txt.gz").unlink()

    impl, repo = resynced(tmp_path)

    assert impl.derived_files_state(repo) is DerivedFilesState.STALE


def test_a_table_file_missing_without_a_sidecar_is_not_an_unpulled_payload(
    tmp_path: pathlib.Path,
) -> None:
    """Nothing says the bytes exist anywhere: the resource is broken,
    and the rebuild is what fails it, as any read of it would."""
    a_repaired_labelled_score(tmp_path)
    (tmp_path / "score" / "statistics" / "chrom_lengths.json").unlink()
    (tmp_path / "score" / "data.txt.gz").unlink()

    impl, repo = resynced(tmp_path)

    assert impl.derived_files_state(repo) is DerivedFilesState.STALE


def test_a_kind_that_derives_nothing_is_always_current(
    tmp_path: pathlib.Path,
) -> None:
    """The base answer: nothing to compare, nothing to rebuild."""
    impl = build_resource_implementation(
        a_basic_resource().build_resource(tmp_path))

    assert impl.derived_files_state(None) is DerivedFilesState.CURRENT
