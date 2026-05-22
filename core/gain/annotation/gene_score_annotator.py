"""Module containing the gene score annotator."""

import logging
from typing import Any

from gain.annotation.annotatable import Annotatable
from gain.annotation.annotation_config import (
    AnnotationConfigParser,
    AnnotatorInfo,
)
from gain.annotation.annotation_pipeline import (
    AnnotationPipeline,
    Annotator,
    AttributeSpec,
)
from gain.annotation.annotator_base import AnnotatorBase
from gain.gene_scores.gene_scores import build_gene_score_from_resource
from gain.genomic_resources import GenomicResource
from gain.genomic_resources.aggregators import (
    build_aggregator,
    validate_aggregator,
)

logger = logging.getLogger(__name__)


def build_gene_score_annotator(pipeline: AnnotationPipeline,
                               info: AnnotatorInfo) -> Annotator:
    """Create a gene score annotator."""
    gene_score_resource_id = info.parameters["resource_id"]
    if not gene_score_resource_id:
        raise ValueError(f"The {info} needs a 'resource_id' parameter.")
    gene_score_resource = pipeline.repository.get_resource(
        gene_score_resource_id)
    if gene_score_resource is None:
        raise ValueError(f"The {gene_score_resource_id} is not available.")

    input_gene_list = info.parameters.get("input_gene_list")
    if input_gene_list is None:
        raise ValueError(f"The {input} must have an 'input_gene_list' "
                         "parameter")
    input_gene_list_info = pipeline.get_attribute_info(input_gene_list)
    if input_gene_list_info is None:
        raise ValueError(f"The {input_gene_list} is not provided by the "
                         "pipeline.")
    if input_gene_list_info.spec is None \
            or input_gene_list_info.spec.value_type != "object":
        raise ValueError(f"The {input_gene_list} provided by the pipeline "
                         "is not of type object.")
    return GeneScoreAnnotator(pipeline, info,
                              gene_score_resource, input_gene_list)


class GeneScoreAnnotator(AnnotatorBase):
    """Gene score annotator class."""

    DEFAULT_AGGREGATOR_TYPE = "dict"

    def __init__(self, pipeline: AnnotationPipeline | None,
                 info: AnnotatorInfo,
                 gene_score_resource: GenomicResource,
                 input_gene_list: str):

        self.gene_score_resource = gene_score_resource
        self.score = build_gene_score_from_resource(self.gene_score_resource)
        info.resources += [gene_score_resource]
        self.input_gene_list = input_gene_list
        super().__init__(pipeline, info)

        self.aggregators: list[str] = []
        for attr in self._attributes:
            aggregator_type = attr.parameters.get("gene_aggregator")
            assert aggregator_type is not None
            validate_aggregator(aggregator_type)

            self.aggregators.append(aggregator_type)

            aggregator_doc = f"**gene_aggregator**: {aggregator_type}"
            if aggregator_type == "dict":
                aggregator_doc = f"{aggregator_doc} [default]"
                assert attr.spec is not None
                attr.spec = AttributeSpec(
                    source=attr.spec.source,
                    value_type="object",
                    description=attr.spec.description,
                    is_default=attr.spec.is_default,
                    internal_default=attr.spec.internal_default,
                    supports_aggregation=attr.spec.supports_aggregation,
                    attribute_type=attr.spec.attribute_type,
                )

            attr._documentation = (  # noqa: SLF001
                f"{attr.documentation}\n\n"
                f"{aggregator_doc}"
            )

    def get_attribute_specs(self) -> dict[str, AttributeSpec]:
        specs: dict[str, AttributeSpec] = {}
        for score_id, score_def in self.score.score_definitions.items():
            specs[score_id] = AttributeSpec(
                source=score_id,
                value_type=score_def.value_type,
                description=score_def.description,
            )

        default_annotation = self.score.config.get("default_annotation")
        if default_annotation is not None:
            for source in list(specs):
                specs[source] = AttributeSpec(
                    source=specs[source].source,
                    value_type=specs[source].value_type,
                    description=specs[source].description,
                    is_default=False,
                    internal_default=specs[source].internal_default,
                    supports_aggregation=specs[source].supports_aggregation,
                    attribute_type=specs[source].attribute_type,
                )
            for attr in default_annotation:
                default_attr = \
                    AnnotationConfigParser.parse_raw_attribute_config(attr)
                if default_attr.source not in specs:
                    raise ValueError(
                        f"Default annotation attribute "
                        f"'{default_attr.source}' is not defined in the "
                        f"{self.gene_score_resource.get_id()} gene score "
                        "resource!")
                desc_override = default_attr.parameters.get("description")
                if desc_override:
                    specs[default_attr.source].description = desc_override
                specs[default_attr.source].is_default = True
                if default_attr.internal is not None:
                    specs[default_attr.source].internal_default = \
                        default_attr.internal
                gene_agg = default_attr.parameters.get("gene_aggregator")
                if gene_agg is not None:
                    validate_aggregator(gene_agg)
                    self._resource_gene_aggregators[default_attr.source] = \
                        gene_agg

        return specs

    def get_attribute_defaults(
        self, spec: AttributeSpec,
    ) -> dict[str, Any]:
        agg = self._resource_gene_aggregators.get(
            spec.source, self.DEFAULT_AGGREGATOR_TYPE)
        return {"gene_aggregator": agg}

    @property
    def _resource_gene_aggregators(self) -> dict[str, str]:
        if not hasattr(self, "_rga"):
            self._rga: dict[str, str] = {}
        return self._rga

    @property
    def used_context_attributes(self) -> tuple[str, ...]:
        return (self.input_gene_list,)

    def _do_annotate(
        self,
        annotatable: Annotatable,  # noqa: ARG002
        context: dict[str, Any],
    ) -> dict[str, Any]:
        genes = context.get(self.input_gene_list)
        if genes is None:
            return self._empty_result()
        return {
            attr.name: self.aggregate_gene_values(
                attr.source, genes, aggregator_type,
            )
            for attr, aggregator_type in zip(
                self.attributes, self.aggregators, strict=True,
            )
        }

    def aggregate_gene_values(
            self, score_id: str,
            gene_symbols: list[str],
            aggregator_type: str) -> Any:
        """Aggregate gene score values."""
        aggregator = build_aggregator(aggregator_type)

        for symbol in gene_symbols:
            aggregator.add(
                self.score.get_gene_value(score_id, symbol),
                key=symbol,
            )

        return aggregator.get_final()
