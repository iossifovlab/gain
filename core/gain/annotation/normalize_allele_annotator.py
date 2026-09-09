"""Provides normalize allele annotator and helpers."""
from typing import Any

from gain import logging
from gain.annotation.annotatable import Annotatable, VCFAllele
from gain.annotation.annotation_config import AnnotatorInfo
from gain.annotation.annotation_pipeline import (
    AnnotationPipeline,
    Annotator,
    AttributeSpec,
)
from gain.annotation.annotator_base import AnnotatedValues, AnnotatorBase
from gain.annotation.utils import (
    preamble_reference_genome_id,
    resolve_reference_genome,
)
from gain.genomic_resources.reference_genome import ReferenceGenome
from gain.genomic_resources.variant_utils import normalize_variant

logger = logging.getLogger(__name__)


def build_normalize_allele_annotator(pipeline: AnnotationPipeline,
                                     info: AnnotatorInfo) -> Annotator:
    return NormalizeAlleleAnnotator(pipeline, info)


class NormalizeAlleleAnnotator(AnnotatorBase):
    """Annotator to normalize VCF alleles."""

    def __init__(self, pipeline: AnnotationPipeline, info: AnnotatorInfo):

        # No gene models operand here, so the chain is shorter than the
        # one in `find_annotator_reference_genome` -- but everything
        # after it is shared (gain#1102).
        genome = resolve_reference_genome(
            info,
            info.parameters.get("genome")
            or preamble_reference_genome_id(pipeline),
            pipeline.repository,
            searched="the annotation config's preamble or the context")

        info.resources += [genome.resource]
        super().__init__(pipeline, info)

        self.genome = genome

    def get_attribute_specs(self) -> dict[str, AttributeSpec]:
        return {
            "normalized_allele": AttributeSpec(
                source="normalized_allele",
                value_type="annotatable",
                description="Normalized allele.",
                internal_default=True,
                is_default=True,
                attribute_type="annotatable",
            ),
        }

    def close(self) -> None:
        self.genome.close()
        super().close()

    def open(self) -> Annotator:
        self.genome.open()
        return super().open()

    def _do_annotate(
        self, annotatable: Annotatable,
        context: dict[str, Any],  # ruff: ignore[unused-method-argument]
    ) -> AnnotatedValues:
        if isinstance(annotatable, VCFAllele):
            annotatable = normalize_allele(annotatable, self.genome)
        return self._every(annotatable)


def normalize_allele(allele: VCFAllele, genome: ReferenceGenome) -> VCFAllele:
    """Normalize an allele.

    Using algorithm defined in
    following https://genome.sph.umich.edu/wiki/Variant_Normalization
    """
    chrom, pos, ref, alts = normalize_variant(
        allele.chrom, allele.pos, allele.ref, [allele.alt], genome)
    return VCFAllele(chrom, pos, ref, alts[0])
