# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Unit tests of the page dependencies of a resource (gain#2452).

``page_dependencies(repo)`` lists the resources that the info pages of a
resource read. ``page_fingerprint(repo)`` covers the full id and the
manifest of each of them. A genomic score depends on the reference
genome that its ``reference_genome`` label names.
"""
import pathlib
from collections.abc import Callable

import pytest
from gain.genomic_resources import reference_genome
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.implementations.genomic_scores_impl import (
    base as score_base,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.repository_factory import (
    build_resource_implementation,
)
from gain.genomic_resources.testing import build_filesystem_test_repository
from gain.genomic_resources.testing.builders import a_grr
from gain.genomic_resources.testing.data_frame_builder import a_data_frame
from gain.genomic_resources.testing.reference_genome_builder import (
    a_reference_genome,
)
from gain.genomic_resources.testing.table_score_builder import (
    _TableScoreBuilder,
    a_fragment_score,
    a_position_score,
    an_allele_score,
)


def _a_score() -> _TableScoreBuilder:
    return a_position_score() \
        .with_score("phastCons", "float") \
        .with_tabix() \
        .with_data("""
            chrom  pos_begin  pos_end  phastCons
            1      10         15       0.02
        """)


def _a_labelled_grr(
    tmp_path: pathlib.Path,
    *,
    label: str = "genome",
    score: _TableScoreBuilder | None = None,
    sequence: str = "A" * 40,
) -> GenomicResourceRepo:
    score = score if score is not None else _a_score()
    return a_grr() \
        .with_resource(
            "genome", a_reference_genome().with_chromosome("1", sequence)) \
        .with_resource("score", score.with_labels(reference_genome=label)) \
        .with_resource("df", a_data_frame()) \
        .build_repo(tmp_path)


def _dependencies(
    repo: GenomicResourceRepo, resource_id: str,
) -> list[str]:
    implementation = build_resource_implementation(
        repo.get_resource(resource_id))
    return [
        resource.resource_id
        for resource in implementation.page_dependencies(repo)
    ]


def test_a_score_depends_on_the_genome_that_its_label_names(
    tmp_path: pathlib.Path,
) -> None:
    repo = _a_labelled_grr(tmp_path)

    implementation = build_resource_implementation(repo.get_resource("score"))
    dependencies = implementation.page_dependencies(repo)

    assert dependencies == [repo.get_resource("genome")]


def test_a_score_with_no_label_has_no_dependency(
    tmp_path: pathlib.Path,
) -> None:
    repo = a_grr() \
        .with_resource(
            "genome", a_reference_genome().with_chromosome("1", "A" * 40)) \
        .with_resource("score", _a_score()) \
        .build_repo(tmp_path)

    assert _dependencies(repo, "score") == []


def test_a_label_that_names_a_missing_id_gives_no_dependency(
    tmp_path: pathlib.Path,
) -> None:
    repo = _a_labelled_grr(tmp_path, label="no_such_genome")

    assert _dependencies(repo, "score") == []


def test_a_label_that_names_another_type_gives_no_dependency(
    tmp_path: pathlib.Path,
) -> None:
    repo = _a_labelled_grr(tmp_path, label="df")

    assert _dependencies(repo, "score") == []


def test_a_data_frame_has_no_dependency(tmp_path: pathlib.Path) -> None:
    repo = _a_labelled_grr(tmp_path)

    assert _dependencies(repo, "df") == []


def test_the_dependencies_build_no_genome(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _a_labelled_grr(tmp_path)
    implementation = build_resource_implementation(repo.get_resource("score"))

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("page_dependencies built a genome")

    # Every route to a genome object constructs a ``ReferenceGenome``:
    # the builders, ``build_resource_implementation`` and a direct call.
    monkeypatch.setattr(reference_genome.ReferenceGenome, "__init__", refuse)
    monkeypatch.setattr(
        score_base, "build_reference_genome_from_resource", refuse)

    assert implementation.page_dependencies(repo) == \
        [repo.get_resource("genome")]


def _fingerprint(repo: GenomicResourceRepo, resource_id: str) -> str | None:
    implementation = build_resource_implementation(
        repo.get_resource(resource_id))
    return implementation.page_fingerprint(repo)


def _change_the_genome_manifest(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    (tmp_path / "genome" / "notes.txt").write_text("a file no page reads\n")
    cli_manage(["resource-manifest", "-R", str(tmp_path), "-r", "genome"])
    return build_filesystem_test_repository(tmp_path)


def test_a_new_genome_manifest_changes_the_score_fingerprint(
    tmp_path: pathlib.Path,
) -> None:
    before = _fingerprint(_a_labelled_grr(tmp_path), "score")

    after = _fingerprint(_change_the_genome_manifest(tmp_path), "score")

    assert before is not None
    assert after != before


def test_a_new_genome_manifest_keeps_the_data_frame_fingerprint(
    tmp_path: pathlib.Path,
) -> None:
    before = _fingerprint(_a_labelled_grr(tmp_path), "df")

    after = _fingerprint(_change_the_genome_manifest(tmp_path), "df")

    assert before is not None
    assert after == before


def test_another_genome_under_the_same_id_changes_the_score_fingerprint(
    tmp_path: pathlib.Path,
) -> None:
    before = _fingerprint(
        _a_labelled_grr(tmp_path / "first"), "score")

    after = _fingerprint(
        _a_labelled_grr(tmp_path / "second", sequence="C" * 40), "score")

    assert after != before


def _an_allele_score() -> _TableScoreBuilder:
    return an_allele_score() \
        .with_score("freq", "float") \
        .with_data("""
            chrom  pos_begin  reference  alternative  freq
            1      10         A          C            0.2
        """)


def _a_fragment_score() -> _TableScoreBuilder:
    return a_fragment_score() \
        .with_score("v", "float") \
        .with_data("""
            chrom  pos_begin  pos_end  v
            1      10         15       3.0
        """)


_SCORE_KINDS = pytest.mark.parametrize(
    "score",
    [_a_score, _an_allele_score, _a_fragment_score],
    ids=["position", "allele", "fragment"],
)


@_SCORE_KINDS
def test_every_score_kind_depends_on_its_labelled_genome(
    tmp_path: pathlib.Path,
    score: Callable[[], _TableScoreBuilder],
) -> None:
    repo = _a_labelled_grr(tmp_path, score=score())

    assert _dependencies(repo, "score") == ["genome"]


@_SCORE_KINDS
def test_every_score_kind_fingerprint_covers_its_labelled_genome(
    tmp_path: pathlib.Path,
    score: Callable[[], _TableScoreBuilder],
) -> None:
    before = _fingerprint(_a_labelled_grr(tmp_path, score=score()), "score")

    after = _fingerprint(_change_the_genome_manifest(tmp_path), "score")

    assert before is not None
    assert after != before
