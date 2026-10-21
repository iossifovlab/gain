"""Pin the offline claims of the "Repositories and resources" page.

The tests run the code that the page describes and assert the results
that the page states. They read no ``.rst`` file. The page is
``docs/source/development/resources/repositories.rst``. The examples of
the page use ``hg38/scores/phastCons100way``; the tests use a mini-GRR
position score instead. The tests pin these claims:

- ``build_genomic_resource_repository`` takes a definition dictionary of
  type ``directory``, and with no argument it uses the definition that
  ``GRR_DEFINITION_FILE`` names.
- ``get_resource`` raises for an unknown id and ``find_resource`` returns
  ``None``. A ``version_constraint`` that no resource satisfies fails the
  same way.
- ``search_resources`` filters by resource type, and both
  ``search_resources`` and ``get_all_resources`` return generators.
- ``GenomicResource`` reports its id, type, labels and description, and
  ``open_raw_file`` reads a file by its path in the resource.
- A repository is ready without ``open()``. After ``close()`` its
  ``definition`` is ``None``.
- ``get_loaded_manifest`` returns ``None`` when there is no stored
  manifest, and ``get_manifest`` builds one.
- ``build_genomic_resource_group_repository`` combines repositories, and
  ``get_grr_from_context`` returns the repository of a genomic context.
"""
import pathlib
import types

import pytest
import yaml
from gain import grr
from gain.genomic_resources.genomic_context import (
    SimpleGenomicContext,
)
from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceRepo,
    Manifest,
)
from gain.genomic_resources.testing.builders import (
    a_grr,
    a_position_score,
)

POSITION_SCORE = "mini_positionscore_tabix_0"


def test_directory_definition_builds_a_repository(
    mini_grr_dir: pathlib.Path,
) -> None:
    """A ``directory`` definition gives a repository of that directory."""
    repo = grr.build_genomic_resource_repository({
        "id": "local_grr",
        "type": "directory",
        "directory": str(mini_grr_dir),
    })

    assert isinstance(repo, GenomicResourceRepo)
    assert repo.repo_id == "local_grr"
    assert repo.find_resource(POSITION_SCORE) is not None


def test_no_argument_uses_the_default_definition(
    mini_grr_dir: pathlib.Path, tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no argument, the builder reads the default definition."""
    definition_file = tmp_path / "grr_definition.yaml"
    definition_file.write_text(yaml.safe_dump({
        "id": "default_mini",
        "type": "directory",
        "directory": str(mini_grr_dir),
    }))
    monkeypatch.setenv("GRR_DEFINITION_FILE", str(definition_file))

    repo = grr.build_genomic_resource_repository()

    assert repo.repo_id == "default_mini"
    assert repo.find_resource(POSITION_SCORE) is not None


def test_get_resource_reports_full_id_and_type(
    repo: GenomicResourceRepo,
) -> None:
    """The "Finding a resource" example prints the full id and the type."""
    resource = repo.get_resource(POSITION_SCORE)

    assert isinstance(resource, GenomicResource)
    assert resource.get_full_id() == POSITION_SCORE
    assert resource.get_type() == "position_score"


def test_get_resource_raises_for_an_unknown_id(
    repo: GenomicResourceRepo,
) -> None:
    """``get_resource`` raises when no resource matches."""
    with pytest.raises(FileNotFoundError):
        repo.get_resource("no/such/resource")


def test_find_resource_returns_none_for_an_unknown_id(
    repo: GenomicResourceRepo,
) -> None:
    """``find_resource`` returns ``None`` when no resource matches."""
    assert repo.find_resource("no/such/resource") is None


def test_satisfied_version_constraint_finds_the_resource(
    repo: GenomicResourceRepo,
) -> None:
    """The page's ``>=0`` constraint finds a resource of version 0."""
    resource = repo.get_resource(POSITION_SCORE, version_constraint=">=0")

    assert resource.get_version_str() == "0"


def test_unsatisfied_version_constraint_fails_like_an_unknown_id(
    repo: GenomicResourceRepo,
) -> None:
    """``>=1.0`` on a version 0 resource raises or returns ``None``."""
    assert repo.find_resource(
        POSITION_SCORE, version_constraint=">=1.0") is None
    with pytest.raises(FileNotFoundError):
        repo.get_resource(POSITION_SCORE, version_constraint=">=1.0")


def test_search_resources_by_type_is_a_generator(
    repo: GenomicResourceRepo,
) -> None:
    """``search_resources(resource_type=...)`` yields resources of a type."""
    result = repo.search_resources(resource_type="position_score")

    assert isinstance(result, types.GeneratorType)
    resources = list(result)
    assert POSITION_SCORE in {r.get_id() for r in resources}
    assert {r.get_type() for r in resources} == {"position_score"}


def test_get_all_resources_is_an_unfiltered_generator(
    repo: GenomicResourceRepo,
) -> None:
    """``get_all_resources`` yields every resource, of every type."""
    result = repo.get_all_resources()

    assert isinstance(result, types.GeneratorType)
    types_seen = {r.get_type() for r in result}
    assert {"genome", "gene_models", "position_score"} <= types_seen


def test_resource_describes_itself(repo: GenomicResourceRepo) -> None:
    """The metadata methods describe the resource."""
    resource = repo.get_resource(POSITION_SCORE)

    assert resource.get_config()["type"] == "position_score"
    assert resource.get_labels() == {}
    assert "position score" in resource.get_description()


def test_open_raw_file_reads_a_file_by_its_path(
    repo: GenomicResourceRepo,
) -> None:
    """``open_raw_file`` reads a file named by its path in the resource."""
    resource = repo.get_resource(POSITION_SCORE)

    with resource.open_raw_file(
            "statistics/histogram_pos_tabix_0.yaml") as infile:
        content = infile.read()

    assert "config" in yaml.safe_load(content)


def test_repository_is_ready_without_open(
    repo: GenomicResourceRepo,
) -> None:
    """A repository has no ``open()``: it answers as soon as it is built."""
    assert not hasattr(repo, "open")
    assert repo.find_resource(POSITION_SCORE) is not None


def test_close_sets_definition_to_none(repo: GenomicResourceRepo) -> None:
    """After ``close()``, the repository's ``definition`` is ``None``."""
    assert repo.definition is not None

    repo.close()

    assert repo.definition is None


def test_invalidate_keeps_the_repository_usable(
    repo: GenomicResourceRepo,
) -> None:
    """After ``invalidate()``, the next access reloads the resources."""
    repo.invalidate()

    assert repo.find_resource(POSITION_SCORE) is not None


def _repo_without_manifests(root: pathlib.Path) -> GenomicResourceRepo:
    """A directory repository of one resource that stores no manifest."""
    a_grr().with_resource(
        "score", a_position_score().with_score("s", "float")
        .with_data("chrom pos_begin s\nchr1 1 0.5\n"),
    ).build_repo(root)
    for manifest_file in root.rglob(".MANIFEST"):
        manifest_file.unlink()
    return grr.build_genomic_resource_repository({
        "id": "no_manifests", "type": "directory", "directory": str(root),
    })


def test_loaded_manifest_is_none_without_a_stored_manifest(
    tmp_path: pathlib.Path,
) -> None:
    """``get_loaded_manifest`` does not build a missing manifest."""
    resource = _repo_without_manifests(tmp_path).get_resource("score")

    assert resource.get_loaded_manifest() is None


def test_get_manifest_builds_a_missing_manifest(
    tmp_path: pathlib.Path,
) -> None:
    """``get_manifest`` builds the manifest that is missing."""
    resource = _repo_without_manifests(tmp_path).get_resource("score")

    manifest = resource.get_manifest()

    assert isinstance(manifest, Manifest)
    assert set(manifest.names()) == {"data.txt", "genomic_resource.yaml"}


def test_group_repository_combines_repositories(
    repo: GenomicResourceRepo, tmp_path: pathlib.Path,
) -> None:
    """A group repository finds the resources of each child."""
    other = a_grr().with_resource(
        "other_score", a_position_score().with_score("s", "float")
        .with_data("chrom pos_begin s\nchr1 1 0.5\n"),
    ).build_repo(tmp_path)

    group = grr.build_genomic_resource_group_repository(
        "group", [repo, other])

    assert group.find_resource(POSITION_SCORE) is not None
    assert group.find_resource("other_score") is not None


def test_get_grr_from_context_returns_the_context_repository(
    repo: GenomicResourceRepo,
) -> None:
    """``get_grr_from_context`` answers the repository of a context."""
    context = SimpleGenomicContext(
        {"genomic_resources_repository": repo}, source="test")

    assert grr.get_grr_from_context(context) is repo
