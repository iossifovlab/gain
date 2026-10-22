"""Pin the offline claims of the "Gene set collections" page.

The tests run the code that the page describes and assert the results
that the page states. They read no ``.rst`` file. The page is
``docs/source/development/resources/gene_sets.rst``. The examples of the
page use ``gene_properties/gene_sets/GO_2025-07-22_release`` and show its
counts; the tests use the mini-GRR collections ``mini_geneset_map`` and
``mini_geneset_gmt`` instead, and a ``directory`` collection built with
``a_gene_set_collection``. The tests pin these claims:

- Both builders give a ``GeneSetCollection``, and repeated builds of a
  resource return the same object.
- ``load()`` returns the collection, a second ``load()`` has no effect,
  and ``is_loaded()`` reports the state. A collection has no ``close()``.
- ``get_all_gene_sets`` returns a list, and ``get_gene_set`` returns one
  set or ``None``.
- A ``GeneSet`` has ``name``, ``desc``, ``syms`` and ``count``.
- ``collection_id`` comes from the ``id:`` of the resource, and the
  collection has ``web_label`` and ``web_format_str``.
- The ``directory``, ``gmt`` and ``map`` formats load into the same
  classes.
- The two statistics histograms are read from the resource, and are
  ``None`` when the resource does not store them.
"""
import logging
import pathlib

import pytest
from gain import grr
from gain.gene_sets.gene_set import GeneSet, GeneSetCollection
from gain.genomic_resources.histogram import CategoricalHistogram
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing.gene_set_collection_builder import (
    a_gene_set_collection,
)

MAP_COLLECTION = "mini_geneset_map"
GMT_COLLECTION = "mini_geneset_gmt"


def _directory_collection(root: pathlib.Path) -> GeneSetCollection:
    resource = a_gene_set_collection().with_id("toy") \
        .with_web_label("Toy") \
        .with_web_format_str("key| (|count|)") \
        .with_gene_set("SetA", "First set", genes=["G1", "G2"]) \
        .with_gene_set("SetB", "Second set", genes=["G2"]) \
        .build_resource(root)
    return grr.build_gene_set_collection_from_resource(resource)


def test_repeated_builds_return_the_same_object(
    repo: GenomicResourceRepo,
) -> None:
    """Both builders return the one collection of the resource."""
    from_id = grr.build_gene_set_collection_from_resource_id(
        MAP_COLLECTION, repo)
    from_resource = grr.build_gene_set_collection_from_resource(
        repo.get_resource(MAP_COLLECTION))

    assert isinstance(from_id, GeneSetCollection)
    assert from_resource is from_id


def test_load_returns_the_collection_and_is_idempotent(
    tmp_path: pathlib.Path,
) -> None:
    """A second ``load()`` keeps the loaded gene sets."""
    collection = _directory_collection(tmp_path)
    assert not collection.is_loaded()

    assert collection.load() is collection
    first = collection.get_all_gene_sets()
    assert collection.load() is collection

    assert collection.is_loaded()
    assert collection.get_all_gene_sets() == first


def test_collection_has_no_close(tmp_path: pathlib.Path) -> None:
    """A collection holds no open file."""
    assert not hasattr(_directory_collection(tmp_path), "close")


def test_get_all_gene_sets_returns_a_list(
    repo: GenomicResourceRepo,
) -> None:
    """Each item is a ``GeneSet``."""
    collection = grr.build_gene_set_collection_from_resource_id(
        MAP_COLLECTION, repo).load()

    gene_sets = collection.get_all_gene_sets()

    assert isinstance(gene_sets, list)
    assert len(gene_sets) == 2
    assert all(isinstance(g, GeneSet) for g in gene_sets)


def test_get_gene_set_by_name(repo: GenomicResourceRepo) -> None:
    """The page's example prints the name, the count and the symbols."""
    collection = grr.build_gene_set_collection_from_resource_id(
        MAP_COLLECTION, repo).load()

    selected = collection.get_gene_set("1")

    assert selected is not None
    assert (selected.name, selected.desc) == ("1", "Function1")
    assert (selected.count, selected.syms) == (1, ["geneA"])


def test_unknown_gene_set_gives_none(repo: GenomicResourceRepo) -> None:
    """``get_gene_set`` returns ``None`` for a name it does not hold."""
    collection = grr.build_gene_set_collection_from_resource_id(
        MAP_COLLECTION, repo).load()

    assert collection.get_gene_set("NO_SUCH_SET") is None


def test_collection_id_and_web_attributes(tmp_path: pathlib.Path) -> None:
    """``collection_id`` is the ``id:`` setting of the resource."""
    collection = _directory_collection(tmp_path)

    assert collection.collection_id == "toy"
    assert collection.web_label == "Toy"
    assert collection.web_format_str == "key| (|count|)"


@pytest.mark.parametrize(("resource_id", "collection_id"), [
    (MAP_COLLECTION, "geneset1"),
    (GMT_COLLECTION, "gmt_geneset"),
])
def test_map_and_gmt_formats_load_into_the_same_classes(
    repo: GenomicResourceRepo, resource_id: str, collection_id: str,
) -> None:
    """The ``map`` and ``gmt`` formats give the same Python objects."""
    collection = grr.build_gene_set_collection_from_resource_id(
        resource_id, repo).load()

    assert collection.collection_id == collection_id
    assert all(
        isinstance(g, GeneSet) for g in collection.get_all_gene_sets())
    assert {"geneA"} <= set(collection.get_all_gene_sets()[0].syms)


def test_directory_format_loads_into_the_same_classes(
    tmp_path: pathlib.Path,
) -> None:
    """Each gene set of a ``directory`` collection is a ``GeneSet``."""
    collection = _directory_collection(tmp_path).load()

    selected = collection.get_gene_set("SetA")

    assert isinstance(selected, GeneSet)
    assert (selected.desc, selected.syms, selected.count) == (
        "First set", ["G1", "G2"], 2)


def test_statistics_histograms_are_read_from_the_resource(
    repo: GenomicResourceRepo,
) -> None:
    """mini-GRR stores both histograms of its collections."""
    collection = grr.build_gene_set_collection_from_resource_id(
        MAP_COLLECTION, repo)

    assert isinstance(
        collection.get_genes_per_gene_set_hist(), CategoricalHistogram)
    assert isinstance(
        collection.get_gene_sets_per_gene_hist(), CategoricalHistogram)


def test_missing_statistics_histograms_give_none(
    tmp_path: pathlib.Path,
) -> None:
    """A collection whose statistics were never built has no histograms."""
    collection = _directory_collection(tmp_path)

    assert collection.get_genes_per_gene_set_hist() is None
    assert collection.get_gene_sets_per_gene_hist() is None


def test_missing_statistics_histograms_log_no_warning(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A missing histogram is an expected state, not a load failure."""
    collection = _directory_collection(tmp_path)

    with caplog.at_level(logging.WARNING):
        collection.get_genes_per_gene_set_hist()
        collection.get_gene_sets_per_gene_hist()

    assert "unable to load histogram file" not in caplog.text
