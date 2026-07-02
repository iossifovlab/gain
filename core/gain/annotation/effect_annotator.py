import textwrap
from typing import Any

import gain.logging as logging
from gain.annotation.annotatable import Annotatable, CNVAllele, VCFAllele
from gain.annotation.annotation_config import (
    AnnotatorInfo,
)
from gain.annotation.annotation_pipeline import (
    AnnotationPipeline,
    Annotator,
    AttributeSpec,
)
from gain.annotation.annotator_base import AnnotatorBase
from gain.annotation.utils import (
    find_annotator_gene_models,
    find_annotator_reference_genome,
)
from gain.effect_annotation.annotator import EffectAnnotator
from gain.effect_annotation.effect import (
    AlleleEffects,
    AnnotationEffect,
    EffectTypesMixin,
)

logger = logging.getLogger(__name__)


def build_effect_annotator(pipeline: AnnotationPipeline,
                           info: AnnotatorInfo) -> Annotator:
    return EffectAnnotatorAdapter(pipeline, info)


class EffectAnnotatorAdapter(AnnotatorBase):
    """Adapts effect annotator to be used in annotation infrastructure."""

    @staticmethod
    def _build_source_effect_types() -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for group in [
                *EffectTypesMixin.EFFECT_GROUPS,
                *EffectTypesMixin.EFFECT_TYPES]:
            result[f"{group}_gene_list"] = {"effect_type": group}
            result[f"{group}_genes"] = {"effect_type": group}
        result["LGD_gene_list"] = {"effect_type": "LGDs"}
        return result

    def __init__(self, pipeline: AnnotationPipeline, info: AnnotatorInfo):
        self._source_effect_types = self._build_source_effect_types()
        gene_models = find_annotator_gene_models(
            info, pipeline.repository)
        genome = find_annotator_reference_genome(
            info, gene_models, pipeline, pipeline.repository)

        info.documentation += textwrap.dedent(f"""

Annotator to identify the effect of the variant on protein coding.

<a href="{self.BASE_DOC_URL}#effect-annotator" target="_blank">More info</a>

""")
        info.resources += [genome.resource, gene_models.resource]
        super().__init__(pipeline, info)

        self.used_attributes = [
            attr.source for attr in self._attributes
        ]
        self._attr_effect_types: dict[str, str | None] = {
            attr.source: attr.parameters.get("effect_type")
            for attr in self._attributes
        }
        self.genome = genome
        self.gene_models = gene_models
        self._promoter_len = info.parameters.get("promoter_len", 0)
        self._region_length_cutoff = info.parameters.get(
            "region_length_cutoff", 15_000_000)

        self.effect_annotator = EffectAnnotator(
            self.genome,
            self.gene_models,
            promoter_len=self._promoter_len,
        )

    def get_attribute_specs(self) -> dict[str, AttributeSpec]:
        effect_gene_lists: dict[str, AttributeSpec] = {}
        effect_genes: dict[str, AttributeSpec] = {}
        for group in [
                *EffectTypesMixin.EFFECT_GROUPS,
                *EffectTypesMixin.EFFECT_TYPES]:
            source_gl = f"{group}_gene_list"
            source_ge = f"{group}_genes"
            effect_gene_lists[source_gl] = AttributeSpec(
                source=source_gl,
                value_type="object",
                description=f"List of all {group} genes",
                internal_default=True,
                is_default=False,
                attribute_type="gene_list",
            )
            effect_genes[source_ge] = AttributeSpec(
                source=source_ge,
                value_type="str",
                description=f"Comma separated list of {group} genes",
                internal_default=False,
                is_default=False,
                supports_aggregation=False,
            )
        effect_gene_lists["LGD_gene_list"] = AttributeSpec(
            source="LGD_gene_list",
            value_type="object",
            description=(
                "List of all LGD genes (deprecated, use LGDs_gene_list)"
            ),
            internal_default=True,
            is_default=False,
            attribute_type="gene_list",
        )
        return {
            "worst_effect": AttributeSpec(
                source="worst_effect",
                value_type="str",
                description="Worst effect across all transcripts.",
                is_default=True,
                internal_default=False,
                supports_aggregation=False),
            "worst_effect_genes": AttributeSpec(
                source="worst_effect_genes",
                value_type="str",
                description="comma separated list of genes with worst effect.",
                internal_default=False,
                is_default=True,
                supports_aggregation=False),
            "worst_effect_gene_list": AttributeSpec(
                source="worst_effect_gene_list",
                value_type="object",
                description="list of genes with worst effect.",
                internal_default=True,
                is_default=False,
                attribute_type="gene_list"),
            "gene_effects": AttributeSpec(
                source="gene_effects",
                value_type="str",
                description=(
                    "`<gene_1>:<effect_1>|...` A gene can be repeated."
                ),
                internal_default=False,
                is_default=True,
                supports_aggregation=False),
            "effect_details": AttributeSpec(
                source="effect_details",
                value_type="str",
                description=(
                    "Effect details for each affected "
                    "transcript. Format: `< transcript 1 >:"
                    "<gene 1>:<effect 1>:<details 1>|...`"
                ),
                internal_default=False,
                is_default=True,
                supports_aggregation=False),
            "allele_effects": AttributeSpec(
                source="allele_effects",
                value_type="object",
                description=("The a list of a python objects with "
                "details of the effects for each "
                "affected transcript."),
                internal_default=True,
                is_default=False,
                supports_aggregation=False),
            "gene_list": AttributeSpec(
                source="gene_list",
                value_type="object",
                description="List of all genes",
                internal_default=True,
                is_default=True,
                attribute_type="gene_list"),
            "genes": AttributeSpec(
                source="genes",
                value_type="str",
                description="Comma separated list of all affected genes.",
                internal_default=False,
                is_default=False,
                supports_aggregation=False),
            **effect_gene_lists,
            **effect_genes,
        }

    def get_attribute_defaults(
        self, spec: AttributeSpec,
    ) -> dict[str, Any]:
        return dict(self._source_effect_types.get(spec.source, {}))

    def close(self) -> None:
        self.genome.close()
        self.gene_models.close()
        assert self.effect_annotator is not None
        self.effect_annotator.close()
        self.effect_annotator = None  # type: ignore
        super().close()

    def open(self) -> Annotator:
        self.genome.open()
        self.gene_models.load()
        return super().open()

    def _not_found(self, attributes: dict[str, Any]) -> dict[str, Any]:
        effect_type = "unknown"
        effect = AnnotationEffect(effect_type)
        full_desc = AnnotationEffect.effects_description([effect])
        attributes.update({
            "worst_effect": full_desc[0],
            "gene_effects": full_desc[1],
            "effect_details": full_desc[2],
            "allele_effects": AlleleEffects.from_effects([effect]),
            "gene_list": [],
            "genes": "",
            "worst_effect_gene_list": [],
            "worst_effect_genes": "",
        })
        return attributes

    def _region_length_cutoff_effect(
        self, attributes: dict[str, Any], annotatable: Annotatable,
    ) -> dict[str, Any]:
        if annotatable.type == Annotatable.Type.LARGE_DELETION:
            effect_type = "CNV-"
        elif annotatable.type == Annotatable.Type.LARGE_DUPLICATION:
            effect_type = "CNV+"
        else:
            effect_type = "unknown"
        effect = AnnotationEffect(effect_type)
        effect.length = len(annotatable)
        full_desc = AnnotationEffect.effects_description([effect])
        attributes.update({
            "worst_effect": full_desc[0],
            "gene_effects": full_desc[1],
            "effect_details": full_desc[2],
            "allele_effects": AlleleEffects.from_effects([effect]),
            "gene_list": [],
            "genes": "",
            "worst_effect_gene_list": [],
            "worst_effect_genes": "",
        })
        return attributes

    def _do_annotate(
        self, annotatable: Annotatable,
        context: dict[str, Any],  # noqa: ARG002
    ) -> dict[str, Any]:
        result: dict = {}

        length = len(annotatable)
        if isinstance(annotatable, VCFAllele):
            try:
                assert self.effect_annotator is not None

                effects = self.effect_annotator.annotate_allele(
                    chrom=annotatable.chromosome,
                    pos=annotatable.position,
                    ref=annotatable.reference,
                    alt=annotatable.alternative,
                    variant_type=annotatable.type,
                    length=length,
                )
            except Exception:  # pylint: disable=broad-except
                logger.exception(
                    "unable to create effect annotation for allele %s",
                    annotatable)
                return self._not_found(result)

        elif length > self._region_length_cutoff:
            logger.warning(
                "region length %s is longer than cutoff %s; %s",
                length, self._region_length_cutoff, annotatable)
            return self._region_length_cutoff_effect(result, annotatable)
        elif isinstance(annotatable, CNVAllele):
            assert self.effect_annotator is not None
            effects = self.effect_annotator.annotate_cnv(
                annotatable.chrom,
                annotatable.pos, annotatable.pos_end, annotatable.type)
        elif isinstance(annotatable, Annotatable):
            assert self.effect_annotator is not None

            effects = self.effect_annotator.annotate_region(
                annotatable.chrom,
                annotatable.pos, annotatable.pos_end)
        else:
            raise ValueError(f"unexpected annotatable: {type(annotatable)}")

        gene_list = AnnotationEffect.genes(effects)

        full_desc = AnnotationEffect.effects_description(effects)
        worst_effect = full_desc[0]
        worst_effect_genes = AnnotationEffect.filter_genes(
            effects, worst_effect)
        result = {
            "worst_effect": full_desc[0],
            "gene_effects": full_desc[1],
            "effect_details": full_desc[2],
            "allele_effects": AlleleEffects.from_effects(effects),
            "gene_list": gene_list,
            "genes": ",".join(gene_list),
            "worst_effect_gene_list": worst_effect_genes,
            "worst_effect_genes": ",".join(worst_effect_genes),
        }
        for attr in self.attributes:
            effect_type = self._attr_effect_types.get(attr.source)
            if effect_type is not None:
                genes = sorted(
                    AnnotationEffect.filter_genes(effects, effect_type))
                assert attr.spec is not None
                if attr.spec.attribute_type == "gene_list":
                    result[attr.source] = genes
                else:
                    result[attr.source] = ",".join(genes)

        return result
