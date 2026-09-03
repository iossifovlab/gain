"""Module containing the gene score annotator."""

from typing import Any

from gain import logging
from gain.annotation.annotatable import Annotatable
from gain.annotation.annotation_config import (
    AnnotationConfigParser,
    AnnotatorInfo,
    Attribute,
)
from gain.annotation.annotation_pipeline import (
    AnnotationPipeline,
    Annotator,
    AttributeSpec,
)
from gain.annotation.annotator_base import AggregatedValues, AnnotatorBase
from gain.gene_scores.gene_scores import build_gene_score_from_resource
from gain.genomic_resources import GenomicResource
from gain.genomic_resources.aggregators import Aggregator

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

    def __init__(self, pipeline: AnnotationPipeline | None,
                 info: AnnotatorInfo,
                 gene_score_resource: GenomicResource,
                 input_gene_list: str):

        self.gene_score_resource = gene_score_resource
        self.score = build_gene_score_from_resource(self.gene_score_resource)
        info.resources += [gene_score_resource]
        self.input_gene_list = input_gene_list
        super().__init__(pipeline, info)

    def get_attribute_specs(self) -> dict[str, AttributeSpec]:
        specs: dict[str, AttributeSpec] = {}
        for score_id, score_def in self.score.score_definitions.items():
            specs[score_id] = AttributeSpec(
                source=score_id,
                value_type="object",
                description=score_def.desc,
                supports_aggregation=True,
            )

        default_annotation = self.score.config.get("default_annotation")
        if default_annotation is not None:
            for source in list(specs):
                specs[source] = AttributeSpec(
                    source=specs[source].source,
                    value_type="object",
                    description=specs[source].description,
                    is_default=False,
                    internal_default=specs[source].internal_default,
                    supports_aggregation=True,
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

        return specs

    def _aggregator_value_type(self, attr: Attribute) -> str | None:  # ruff: ignore[unused-method-argument]
        return None

    def _apply_gene_aggregator(
        self, attr: Attribute, value: Any,
    ) -> Any:
        """Reduce one attribute's per-gene values with its aggregator.

        A fresh accumulator per attribute per call, built from the
        attribute's aggregator NAME.  The name resolution is memoised
        (:meth:`Aggregator.build`), so building anew costs about what
        clearing a held instance did, and nothing outlives the call --
        which is what the base's reuse contract was there to make safe
        (gain#1133).
        """
        if attr.aggregator is None or not isinstance(value, dict):
            return value
        return Aggregator.build(attr.aggregator).aggregate(
            list(value.values()))

    @property
    def used_context_attributes(self) -> tuple[str, ...]:
        return (self.input_gene_list,)

    def _do_annotate(
        self,
        annotatable: Annotatable,  # ruff: ignore[unused-method-argument]
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Answer the input gene list's scores, already reduced.

        This annotator reduces for ITSELF, and its values are per-GENE
        rather than per-record: the input gene list names the genes, the
        score answers one value for each, and the attribute's aggregator
        folds those into one.  Nothing the base could do for it -- which
        is why it kept its own reduction while the base still had one,
        and why it now answers an :class:`AggregatedValues` (gain#1133).

        The names are read here, at answer time, for the reason
        :class:`AggregatedValues` states.
        """
        genes = context.get(self.input_gene_list)
        if genes is None:
            return self._empty_result()
        return AggregatedValues(
            (attr.name, self._apply_gene_aggregator(attr, {
                sym: score
                for sym in genes
                if (score := self.score.get_gene_value(attr.source, sym))
                is not None
            }))
            for attr in self.attributes
        )
