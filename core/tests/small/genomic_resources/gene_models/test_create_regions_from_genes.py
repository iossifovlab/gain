# pylint: disable=C0116
"""Pin the behavior of ``create_regions_from_genes``, which gpf calls.

The gene models have three genes. ``G1`` has two overlapping transcripts
on ``chr1``, ``G2`` has one transcript on ``chr2``, and ``G3`` has one
transcript near the start of ``chr1``.
"""
import pathlib

import pytest
from gain.genomic_resources.gene_models.gene_models import (
    GeneModels,
    create_regions_from_genes,
)
from gain.genomic_resources.gene_models.gene_models_factory import (
    build_gene_models_from_resource,
)
from gain.genomic_resources.testing.gene_models_builder import a_gene_models
from gain.utils.regions import Region

pytestmark = pytest.mark.usefixtures("clean_gene_models_cache")


@pytest.fixture(name="gene_models")
def fixture_gene_models(tmp_path: pathlib.Path) -> GeneModels:
    resource = (
        a_gene_models()
        .with_transcript("tx1a", gene="G1", exons=[(101, 200)])
        .with_transcript("tx1b", gene="G1", exons=[(151, 300)])
        .with_transcript(
            "tx2", gene="G2", chrom="chr2", exons=[(1001, 1100)])
        .with_transcript("tx3", gene="G3", exons=[(11, 20)])
        .build_resource(tmp_path)
    )
    return build_gene_models_from_resource(resource).load()


def test_merges_the_transcripts_of_one_gene_into_one_region(
    gene_models: GeneModels,
) -> None:
    result = create_regions_from_genes(
        gene_models, ["G1"], None, gene_regions_heuristic_extend=0)

    assert result == [Region("chr1", 100, 301)]


def test_extends_by_20000_and_clamps_the_start_at_one_by_default(
    gene_models: GeneModels,
) -> None:
    result = create_regions_from_genes(gene_models, ["G1"], None)

    assert result == [Region("chr1", 1, 20301)]


def test_returns_the_given_regions_for_an_empty_gene_list(
    gene_models: GeneModels,
) -> None:
    regions = [Region("chr1", 1, 10)]

    result = create_regions_from_genes(gene_models, [], regions)

    assert result is regions


def test_returns_the_given_regions_for_more_genes_than_the_cutoff(
    gene_models: GeneModels,
) -> None:
    regions = [Region("chr1", 1, 10)]

    result = create_regions_from_genes(
        gene_models, ["G1", "G2"], regions,
        gene_regions_heuristic_cutoff=1)

    assert result is regions


def test_narrows_to_the_gene_regions_for_as_many_genes_as_the_cutoff(
    gene_models: GeneModels,
) -> None:
    regions = [Region("chr1", 1, 1000), Region("chr2", 1, 2000)]

    result = create_regions_from_genes(
        gene_models, ["G1", "G2"], regions,
        gene_regions_heuristic_cutoff=2, gene_regions_heuristic_extend=0)

    assert result == [Region("chr1", 100, 301), Region("chr2", 1000, 1101)]


def test_returns_none_when_no_gene_is_found_and_no_regions_are_given(
    gene_models: GeneModels,
) -> None:
    result = create_regions_from_genes(gene_models, ["NOPE"], None)

    assert result is None


def test_skips_a_gene_that_is_not_found(
    gene_models: GeneModels,
) -> None:
    result = create_regions_from_genes(
        gene_models, ["NOPE", "G2"], None, gene_regions_heuristic_extend=0)

    assert result == [Region("chr2", 1000, 1101)]


def test_returns_one_region_for_each_gene_that_does_not_overlap(
    gene_models: GeneModels,
) -> None:
    result = create_regions_from_genes(
        gene_models, ["G2", "G3", "G1"], None,
        gene_regions_heuristic_extend=0)

    assert result == [
        Region("chr1", 10, 21),
        Region("chr1", 100, 301),
        Region("chr2", 1000, 1101),
    ]


def test_treats_an_empty_region_list_as_no_regions(
    gene_models: GeneModels,
) -> None:
    result = create_regions_from_genes(
        gene_models, ["G2"], [], gene_regions_heuristic_extend=0)

    assert result == [Region("chr2", 1000, 1101)]


def test_intersects_the_gene_regions_with_the_given_regions(
    gene_models: GeneModels,
) -> None:
    regions = [Region("chr1", 250, 500), Region("chr2", 1, 1050)]

    result = create_regions_from_genes(
        gene_models, ["G1", "G2"], regions, gene_regions_heuristic_extend=0)

    assert result == [Region("chr1", 250, 301), Region("chr2", 1000, 1050)]


def test_merges_the_overlapping_intersections_into_one_region(
    gene_models: GeneModels,
) -> None:
    regions = [Region("chr1", 250, 500), Region("chr1", 120, 260)]

    result = create_regions_from_genes(
        gene_models, ["G1"], regions, gene_regions_heuristic_extend=0)

    assert result == [Region("chr1", 120, 301)]


def test_returns_an_empty_list_when_no_given_region_overlaps_a_gene(
    gene_models: GeneModels,
) -> None:
    regions = [Region("chr3", 1, 1000)]

    result = create_regions_from_genes(
        gene_models, ["G1"], regions, gene_regions_heuristic_extend=0)

    assert result == []


def test_returns_an_empty_list_when_no_gene_is_found_and_regions_are_given(
    gene_models: GeneModels,
) -> None:
    result = create_regions_from_genes(
        gene_models, ["NOPE"], [Region("chr1", 1, 1000)])

    assert result == []
