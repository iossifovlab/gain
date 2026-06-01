# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""End-to-end tests for ``grr_cache_repo`` against the realistic gpf-side
fixtures (``repo/`` + ``annotation.yaml``) copied from gpf-core. Complements
the programmatic-fixture tests in ``test_cli_cache_repo.py``; this file
exercises a multi-annotator pipeline (liftover, normalize_allele, two
position_score annotators) against an on-disk directory GRR with a
``.CONTENTS.json`` manifest, which the programmatic-fixture file does not."""
import logging
import pathlib

import pytest
from gain.genomic_resources.cli_cache_repo import cli_cache_repo


@pytest.fixture
def fixtures_dir() -> pathlib.Path:
    return pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_repo_dir(fixtures_dir: pathlib.Path) -> pathlib.Path:
    return (fixtures_dir / "repo").resolve()


@pytest.fixture
def annotation_yaml(fixtures_dir: pathlib.Path) -> pathlib.Path:
    return (fixtures_dir / "annotation.yaml").resolve()


@pytest.fixture
def grr_yaml(
    tmp_path: pathlib.Path,
    fixture_repo_dir: pathlib.Path,
) -> pathlib.Path:
    yaml_path = tmp_path / "grr.yaml"
    yaml_path.write_text(
        f"id: local\n"
        f"type: directory\n"
        f"directory: {fixture_repo_dir}\n"
        f"cache_dir: {tmp_path / 'cache'}\n",
    )
    return yaml_path


@pytest.fixture
def grr_with_pipeline_yaml(
    tmp_path: pathlib.Path,
    fixture_repo_dir: pathlib.Path,
) -> pathlib.Path:
    yaml_path = tmp_path / "grr.yaml"
    yaml_path.write_text(
        "id: group\n"
        "type: group\n"
        f"cache_dir: {tmp_path / 'cache'}\n"
        "children:\n"
        "  - id: fixture\n"
        "    type: directory\n"
        f"    directory: {fixture_repo_dir}\n"
        "  - id: pipelines\n"
        "    type: embedded\n"
        "    content:\n"
        "      pipelines/test_pipeline:\n"
        "        genomic_resource.yaml: |\n"
        "          type: annotation_pipeline\n"
        "          filename: annotation.yaml\n"
        "        annotation.yaml: |\n"
        "          - position_score: scores/mock1\n"
        "          - position_score:\n"
        "              resource_id: scores/mock2\n",
    )
    return yaml_path


def _cached_resource_path(
    tmp_path: pathlib.Path, repo_id: str, *parts: str,
) -> pathlib.Path:
    return tmp_path / "cache" / repo_id / pathlib.Path(*parts) \
        / "genomic_resource.yaml"


def test_cli_cache_pipeline_file(
    tmp_path: pathlib.Path,
    grr_yaml: pathlib.Path,
    annotation_yaml: pathlib.Path,
) -> None:
    cli_cache_repo([
        "--grr", str(grr_yaml),
        "-j", "1",
        str(annotation_yaml),
    ])

    for parts in [
        ("genomes", "mock"),
        ("genomes", "mock0"),
        ("liftover", "mock"),
        ("scores", "mock1"),
        ("scores", "mock2"),
    ]:
        full = _cached_resource_path(tmp_path, "local", *parts)
        assert full.exists(), full

    assert not _cached_resource_path(
        tmp_path, "local", "gene_models", "mock").exists()
    assert not _cached_resource_path(
        tmp_path, "local", "scores", "mock_extra").exists()


def test_cli_cache_pipeline_grr_resource(
    tmp_path: pathlib.Path,
    grr_with_pipeline_yaml: pathlib.Path,
) -> None:
    cli_cache_repo([
        "--grr", str(grr_with_pipeline_yaml),
        "-j", "1",
        "pipelines/test_pipeline",
    ])

    for parts in [
        ("scores", "mock1"),
        ("scores", "mock2"),
    ]:
        full = _cached_resource_path(tmp_path, "fixture", *parts)
        assert full.exists(), full

    assert not _cached_resource_path(
        tmp_path, "fixture", "gene_models", "mock").exists()
    assert not _cached_resource_path(
        tmp_path, "fixture", "scores", "mock_extra").exists()
    assert not _cached_resource_path(
        tmp_path, "fixture", "liftover", "mock").exists()


def test_cli_cache_no_pipeline(
    tmp_path: pathlib.Path,
    grr_yaml: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="grr_cache_repo"):
        cli_cache_repo([
            "--grr", str(grr_yaml),
            "-j", "1",
        ])

    assert any(
        "no pipeline supplied" in record.message
        for record in caplog.records
    )
    assert not (tmp_path / "cache").exists() or not any(
        (tmp_path / "cache").rglob("genomic_resource.yaml"))
