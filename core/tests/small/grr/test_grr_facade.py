"""``gain.grr`` is the supported public import path for external users.

The names below are the whole of that surface.  They are spelled out, each
with the module that defines it, rather than derived from ``__all__`` or
``dir()``: a derived expectation would agree with the facade by
construction, and a name dropped from the facade would drop out of the test
with it.  A diff to ``EXPECTED`` is a public-API change and is reviewed as
one (ADR 0033).
"""
import importlib

import gain.grr
import pytest

REPOSITORY_FACTORY = "gain.genomic_resources.repository_factory"
GENOMIC_CONTEXT = "gain.genomic_resources.genomic_context"
REPOSITORY = "gain.genomic_resources.repository"
REFERENCE_GENOME = "gain.genomic_resources.reference_genome"
GENE_MODELS = "gain.genomic_resources.gene_models"
LIFTOVER_CHAIN = "gain.genomic_resources.liftover_chain"
GENOMIC_SCORES = "gain.genomic_resources.genomic_scores"
GENE_SCORES = "gain.gene_scores.gene_scores"
GENE_SETS = "gain.gene_sets.gene_set"
DATA_FRAME = "gain.genomic_resources.data_frame_resource"
ANN_DATA = "gain.genomic_resources.ann_data_resource"

EXPECTED: dict[str, str] = {
    # GRR constructors
    "build_genomic_resource_repository": REPOSITORY_FACTORY,
    "build_genomic_resource_group_repository": REPOSITORY_FACTORY,
    "get_default_grr_definition": REPOSITORY_FACTORY,
    "get_default_grr_definition_path": REPOSITORY_FACTORY,
    "get_genomic_context": GENOMIC_CONTEXT,
    "get_grr_from_context": GENOMIC_CONTEXT,
    # resource builders
    "build_reference_genome_from_resource": REFERENCE_GENOME,
    "build_reference_genome_from_resource_id": REFERENCE_GENOME,
    "build_gene_models_from_resource": GENE_MODELS,
    "build_gene_models_from_resource_id": GENE_MODELS,
    "build_liftover_chain_from_resource": LIFTOVER_CHAIN,
    "build_liftover_chain_from_resource_id": LIFTOVER_CHAIN,
    "build_position_score_from_resource": GENOMIC_SCORES,
    "build_position_score_from_resource_id": GENOMIC_SCORES,
    "build_allele_score_from_resource": GENOMIC_SCORES,
    "build_allele_score_from_resource_id": GENOMIC_SCORES,
    "build_fragment_score_from_resource": GENOMIC_SCORES,
    "build_fragment_score_from_resource_id": GENOMIC_SCORES,
    "build_score_from_resource": GENOMIC_SCORES,
    "build_score_from_resource_id": GENOMIC_SCORES,
    "build_gene_score_from_resource": GENE_SCORES,
    "build_gene_score_from_resource_id": GENE_SCORES,
    "build_gene_set_collection_from_resource": GENE_SETS,
    "build_gene_set_collection_from_resource_id": GENE_SETS,
    "load_data_frame_from_resource": DATA_FRAME,
    "load_data_frame_from_resource_id": DATA_FRAME,
    "load_ann_data_from_resource": ANN_DATA,
    "load_ann_data_from_resource_id": ANN_DATA,
    # signature types
    "GenomicResourceRepo": REPOSITORY,
    "GenomicResource": REPOSITORY,
    "ReferenceGenome": REFERENCE_GENOME,
    "GeneModels": GENE_MODELS,
    "LiftoverChain": LIFTOVER_CHAIN,
    "GenomicScore": GENOMIC_SCORES,
    "PositionScore": GENOMIC_SCORES,
    "AlleleScore": GENOMIC_SCORES,
    "FragmentScore": GENOMIC_SCORES,
    "GeneScore": GENE_SCORES,
    "GeneSetCollection": GENE_SETS,
}


def test_all_is_exactly_the_public_surface() -> None:
    """A name added to or dropped from ``__all__`` fails here."""
    assert sorted(gain.grr.__all__) == sorted(EXPECTED)


def test_all_has_no_duplicates() -> None:
    assert len(gain.grr.__all__) == len(set(gain.grr.__all__))


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_each_name_is_the_object_its_defining_module_holds(name: str) -> None:
    """A pure re-export: no alias, no wrapper, no rebinding."""
    defining = importlib.import_module(EXPECTED[name])

    assert getattr(gain.grr, name) is getattr(defining, name)
