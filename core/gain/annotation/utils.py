from gain import logging
from gain.annotation.annotation_config import (
    AnnotatorInfo,
)
from gain.annotation.annotation_pipeline import (
    AnnotationPipeline,
)
from gain.genomic_resources.gene_models import (
    GeneModels,
)
from gain.genomic_resources.gene_models.gene_models_factory import (
    build_gene_models_from_resource_id,
)
from gain.genomic_resources.genomic_context import get_genomic_context
from gain.genomic_resources.reference_genome import (
    ReferenceGenome,
    build_reference_genome_from_resource_id,
)
from gain.genomic_resources.repository import (
    GenomicResourceRepo,
)

logger = logging.getLogger(__name__)


def find_annotator_gene_models(
    info: AnnotatorInfo,
    grr: GenomicResourceRepo,
) -> GeneModels:
    """Get gene models from the annotator info or genomic context."""
    gene_models_resource_id = info.parameters.get("gene_models")
    if gene_models_resource_id is not None:
        logger.debug(
            "Gene models for %s taken from %s",
            info.type, gene_models_resource_id)
        return build_gene_models_from_resource_id(
            gene_models_resource_id, grr,
        )

    gene_models = get_genomic_context().get_gene_models()
    if gene_models is None:
        raise ValueError(
            f"Can't create {info.type}: "
            f"gene models resource is missing in config "
            f"and context")
    return gene_models


def preamble_reference_genome_id(
    pipeline: AnnotationPipeline,
) -> str | None:
    """Get the reference genome id declared by the pipeline's preamble."""
    if pipeline.preamble is None:
        return None
    return pipeline.preamble.input_reference_genome


def resolve_reference_genome(
    info: AnnotatorInfo,
    genome_resource_id: str | None,
    grr: GenomicResourceRepo,
    *,
    searched: str,
) -> ReferenceGenome:
    """Build the genome `genome_resource_id` names, else use the context.

    The caller resolves its own precedence chain -- which operands it has
    differs per annotator -- and passes the winning id here.  Everything
    downstream of that chain is the same for every annotator and lives
    only in this function, so a fix to it cannot miss a call site the way
    gain#1055 had to be fixed at three of them.

    `searched` names the sources the caller consulted, for the error
    raised when nothing resolves; it is the one part of that error that
    cannot be stated here, since the chain is the caller's.
    """
    # `input_reference_genome` is optional and parses to "" when absent,
    # so an empty id means "not configured" -- not "the resource named
    # the empty string" (gain#1055).
    if genome_resource_id:
        logger.debug(
            "Reference genome for %s taken from %s",
            info.type, genome_resource_id)
        return build_reference_genome_from_resource_id(
            genome_resource_id, grr)

    genome = get_genomic_context().get_reference_genome()
    if genome is None:
        raise ValueError(
            f"The {info} has no reference genome"
            f" specified and no genome was found in {searched}.")
    return genome


def find_annotator_reference_genome(
    info: AnnotatorInfo,
    gene_models: GeneModels,
    pipeline: AnnotationPipeline,
    grr: GenomicResourceRepo,
) -> ReferenceGenome:
    """Get reference genome from the annotator info or genomic context."""
    genome_resource_id = info.parameters.get("genome") or \
        gene_models.reference_genome_id or \
        preamble_reference_genome_id(pipeline)

    return resolve_reference_genome(
        info, genome_resource_id, grr,
        searched="the gene models' configuration, the context"
                 " or the annotation config's preamble")
