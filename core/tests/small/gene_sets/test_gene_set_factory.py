# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Tests for the gene set collection factory functions."""
from __future__ import annotations

import pathlib
import threading
from collections.abc import Iterator
from typing import Any

import pytest
from gain.gene_sets.gene_set import (
    _FILE_CACHE,
    _RESOURCE_CACHE,
    GeneSetCollection,
    build_gene_set_collection_from_file,
    build_gene_set_collection_from_resource,
)
from gain.genomic_resources.repository import GenomicResourceRepo

GMT_ALPHA = "ALPHA_SET\talpha description\tPOGZ\tCHD8\n"
GMT_BETA = "BETA_SET\tbeta description\tANK2\n"


@pytest.fixture(autouse=True)
def clear_memo_caches() -> Iterator[None]:
    """Clear the process wide memo caches before and after each test."""
    _FILE_CACHE.clear()
    _RESOURCE_CACHE.clear()
    yield
    _FILE_CACHE.clear()
    _RESOURCE_CACHE.clear()


def call_with_timeout(
    func: Any, *args: Any, timeout: float = 5.0, **kwargs: Any,
) -> Any:
    """Call ``func`` on a worker thread, failing if it does not return.

    ``build_gene_set_collection_from_file`` used to deadlock on itself.
    ``faulthandler_timeout`` in ``pytest.ini`` would eventually abort the
    run with a thread dump, but only after ten minutes and taking the whole
    session with it; this reports the culprit in seconds instead. The
    worker is a daemon so that a hung call cannot keep the interpreter
    alive at exit.

    The timeout is short because a self-deadlock happens immediately or not
    at all, and a hung call strands the module lock, so every later test
    here would burn the same timeout over again.
    """
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["value"] = func(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        pytest.fail(
            f"{func.__name__} did not return within {timeout}s -- deadlocked",
        )
    if "error" in box:
        raise box["error"]
    return box["value"]


@pytest.fixture
def alpha_gmt(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "alpha.gmt"
    path.write_text(GMT_ALPHA)
    return path


def test_build_from_file_returns_a_loadable_collection(
    alpha_gmt: pathlib.Path,
) -> None:
    collection = call_with_timeout(
        build_gene_set_collection_from_file, str(alpha_gmt))

    assert isinstance(collection, GeneSetCollection)
    assert sorted(collection.load().gene_sets) == ["ALPHA_SET"]


def test_two_files_in_one_directory_keep_their_own_gene_sets(
    tmp_path: pathlib.Path,
) -> None:
    """Distinct paths must not share a cache entry.

    Every synthetic resource is built with the resource id ``"."``, so
    keying on the resource identity alone makes all the files of a
    directory collide.  The assertion is on the loaded gene sets rather
    than on object identity, because the damage that matters is a
    collection serving another file's contents.
    """
    alpha = tmp_path / "alpha.gmt"
    alpha.write_text(GMT_ALPHA)
    beta = tmp_path / "beta.gmt"
    beta.write_text(GMT_BETA)

    alpha_collection = call_with_timeout(
        build_gene_set_collection_from_file, str(alpha))
    beta_collection = call_with_timeout(
        build_gene_set_collection_from_file, str(beta))

    assert sorted(alpha_collection.load().gene_sets) == ["ALPHA_SET"]
    assert sorted(beta_collection.load().gene_sets) == ["BETA_SET"]


def test_same_relative_name_in_two_directories_stays_distinct(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A relative path is only meaningful together with the directory.

    The synthetic resource is rooted at the path's dirname, which for a
    bare relative name is the current working directory -- so the working
    directory has to reach the cache key, or the same relative name in two
    directories collides exactly as two files of one directory used to.
    """
    alpha_dir = tmp_path / "alpha"
    alpha_dir.mkdir()
    (alpha_dir / "sets.gmt").write_text(GMT_ALPHA)
    beta_dir = tmp_path / "beta"
    beta_dir.mkdir()
    (beta_dir / "sets.gmt").write_text(GMT_BETA)

    monkeypatch.chdir(alpha_dir)
    alpha_collection = call_with_timeout(
        build_gene_set_collection_from_file, "sets.gmt")
    monkeypatch.chdir(beta_dir)
    beta_collection = call_with_timeout(
        build_gene_set_collection_from_file, "sets.gmt")

    assert sorted(alpha_collection.load().gene_sets) == ["ALPHA_SET"]
    assert sorted(beta_collection.load().gene_sets) == ["BETA_SET"]


def test_config_arguments_apply_to_a_path_built_before(
    alpha_gmt: pathlib.Path,
) -> None:
    """Every config shaping argument must reach the cache key."""
    call_with_timeout(build_gene_set_collection_from_file, str(alpha_gmt))

    collection = call_with_timeout(
        build_gene_set_collection_from_file,
        str(alpha_gmt),
        collection_id="explicit_id",
        collection_format="map",
        web_label="Explicit label",
        web_format_str="key| (|count|)",
    )

    assert collection.collection_id == "explicit_id"
    assert collection.config.resource_format == "map"
    assert collection.web_label == "Explicit label"
    assert collection.web_format_str == "key| (|count|)"


def test_identical_arguments_reuse_the_cached_collection(
    alpha_gmt: pathlib.Path,
) -> None:
    """Memoisation survives the fix -- it is narrowed, not removed."""
    first = call_with_timeout(
        build_gene_set_collection_from_file, str(alpha_gmt))
    second = call_with_timeout(
        build_gene_set_collection_from_file, str(alpha_gmt))

    assert first is second


def test_explicit_format_overrides_the_extension(
    tmp_path: pathlib.Path,
) -> None:
    """An explicit format wins over what the extension would detect."""
    path = tmp_path / "sets.txt"
    path.write_text(GMT_ALPHA)

    collection = call_with_timeout(
        build_gene_set_collection_from_file, str(path),
        collection_format="gmt")

    assert collection.config.resource_format == "gmt"
    assert sorted(collection.load().gene_sets) == ["ALPHA_SET"]


def test_unknown_extension_raises_then_succeeds_with_explicit_format(
    tmp_path: pathlib.Path,
) -> None:
    """An unknown extension is refused, and refusal is not the last word.

    The raise happens before anything is cached, so the retry cannot be
    served a stale entry -- this pins that ordering, which is the reason
    the auto-detect lives outside the cache lookup.
    """
    path = tmp_path / "sets.unknown"
    path.write_text(GMT_ALPHA)

    with pytest.raises(ValueError, match="Cannot find collection format"):
        call_with_timeout(build_gene_set_collection_from_file, str(path))

    collection = call_with_timeout(
        build_gene_set_collection_from_file, str(path),
        collection_format="gmt")

    assert sorted(collection.load().gene_sets) == ["ALPHA_SET"]


def test_build_from_file_supports_the_map_format(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "test-map.txt").write_text(
        "#geneNS\tsym\n"
        "POGZ\ttest:01 test:02\n"
        "CHD8\ttest:02 test:03\n",
    )
    (tmp_path / "test-mapnames.txt").write_text(
        "test:01\ttest_first\n"
        "test:02\ttest_second\n"
        "test:03\ttest_third\n",
    )

    collection = call_with_timeout(
        build_gene_set_collection_from_file, str(tmp_path / "test-map.txt"))

    assert sorted(collection.load().gene_sets) == [
        "test:01", "test:02", "test:03"]


def test_build_from_file_supports_the_directory_format(
    tmp_path: pathlib.Path,
) -> None:
    gene_sets_dir = tmp_path / "GeneSets"
    gene_sets_dir.mkdir()
    (gene_sets_dir / "main_candidates.txt").write_text(
        "main_candidates\nMain Candidates\nPOGZ\nCHD8\n")
    (gene_sets_dir / "alt_candidates.txt").write_text(
        "alt_candidates\nAlt Candidates\nDIABLO\n")

    collection = call_with_timeout(
        build_gene_set_collection_from_file, str(gene_sets_dir))

    assert sorted(collection.load().gene_sets) == [
        "alt_candidates", "main_candidates"]


def test_resource_built_collections_are_still_memoised(
    gene_sets_repo_in_memory: GenomicResourceRepo,
) -> None:
    """The resource keyed cache keeps working across the cache split."""
    resource = gene_sets_repo_in_memory.get_resource("test_gmt")

    first = build_gene_set_collection_from_resource(resource)
    second = build_gene_set_collection_from_resource(resource)

    assert first is second
    assert sorted(first.load().gene_sets) == [
        "TEST_GENE_SET1", "TEST_GENE_SET2", "TEST_GENE_SET3"]
