"""The public import path for scripts and notebooks that use a GRR.

``gain.grr`` re-exports, under one short path, the constructors of a
Genomic Resource Repository, the functions that build a resource object
from a resource or a resource id, and the types those functions accept and
return::

    from gain.grr import (
        build_genomic_resource_repository,
        build_reference_genome_from_resource_id,
    )

    grr = build_genomic_resource_repository()
    genome = build_reference_genome_from_resource_id(
        "hg38/genomes/GRCh38-hg38", grr)

Every name here is the same object as in the module that defines it, and
the names in ``__all__`` are the supported surface: removing one takes a
release with a deprecation warning first.  The defining modules stay
importable and supported.  Code inside ``gain`` imports from the defining
modules, never from here.

Annotation pipelines are not part of this surface; they are built from
``gain.annotation.annotation_factory``.  See ADR 0033 for what is in scope
and why.
"""
from gain.gene_scores.gene_scores import (
    GeneScore,
    build_gene_score_from_resource,
    build_gene_score_from_resource_id,
)
from gain.gene_sets.gene_set import (
    GeneSetCollection,
    build_gene_set_collection_from_resource,
    build_gene_set_collection_from_resource_id,
)
from gain.genomic_resources.ann_data_resource import (
    load_ann_data_from_resource,
    load_ann_data_from_resource_id,
)
from gain.genomic_resources.data_frame_resource import (
    load_data_frame_from_resource,
    load_data_frame_from_resource_id,
)
from gain.genomic_resources.gene_models import (
    GeneModels,
    build_gene_models_from_resource,
    build_gene_models_from_resource_id,
)
from gain.genomic_resources.genomic_context import (
    get_genomic_context,
    get_grr_from_context,
)
from gain.genomic_resources.genomic_scores import (
    AlleleScore,
    FragmentScore,
    GenomicScore,
    PositionScore,
    build_allele_score_from_resource,
    build_allele_score_from_resource_id,
    build_fragment_score_from_resource,
    build_fragment_score_from_resource_id,
    build_position_score_from_resource,
    build_position_score_from_resource_id,
    build_score_from_resource,
    build_score_from_resource_id,
)
from gain.genomic_resources.liftover_chain import (
    LiftoverChain,
    build_liftover_chain_from_resource,
    build_liftover_chain_from_resource_id,
)
from gain.genomic_resources.reference_genome import (
    ReferenceGenome,
    build_reference_genome_from_resource,
    build_reference_genome_from_resource_id,
)
from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceRepo,
)
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_group_repository,
    build_genomic_resource_repository,
    get_default_grr_definition,
    get_default_grr_definition_path,
)

__all__ = [
    "AlleleScore",
    "FragmentScore",
    "GeneModels",
    "GeneScore",
    "GeneSetCollection",
    "GenomicResource",
    "GenomicResourceRepo",
    "GenomicScore",
    "LiftoverChain",
    "PositionScore",
    "ReferenceGenome",
    "build_allele_score_from_resource",
    "build_allele_score_from_resource_id",
    "build_fragment_score_from_resource",
    "build_fragment_score_from_resource_id",
    "build_gene_models_from_resource",
    "build_gene_models_from_resource_id",
    "build_gene_score_from_resource",
    "build_gene_score_from_resource_id",
    "build_gene_set_collection_from_resource",
    "build_gene_set_collection_from_resource_id",
    "build_genomic_resource_group_repository",
    "build_genomic_resource_repository",
    "build_liftover_chain_from_resource",
    "build_liftover_chain_from_resource_id",
    "build_position_score_from_resource",
    "build_position_score_from_resource_id",
    "build_reference_genome_from_resource",
    "build_reference_genome_from_resource_id",
    "build_score_from_resource",
    "build_score_from_resource_id",
    "get_default_grr_definition",
    "get_default_grr_definition_path",
    "get_genomic_context",
    "get_grr_from_context",
    "load_ann_data_from_resource",
    "load_ann_data_from_resource_id",
    "load_data_frame_from_resource",
    "load_data_frame_from_resource_id",
]
