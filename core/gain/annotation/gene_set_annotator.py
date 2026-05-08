import logging
from typing import Any

from gain.annotation.annotatable import Annotatable
from gain.annotation.annotation_config import (
    AnnotatorInfo,
)
from gain.annotation.annotation_pipeline import (
    AnnotationPipeline,
    Annotator,
)
from gain.annotation.annotator_base import (
    AnnotatorBase,
    AttributeDesc,
)
from gain.gene_sets.gene_set import (
    GeneSet,
    build_gene_set_collection_from_resource,
)
from gain.genomic_resources import GenomicResource
from gain.genomic_resources.aggregators import (
    build_aggregator,
    validate_aggregator,
)

logger = logging.getLogger(__name__)


def build_gene_set_annotator(
    pipeline: AnnotationPipeline,
    info: AnnotatorInfo,
) -> Annotator:
    """Create a gene set annotator."""
    gene_set_resource_id = info.parameters["resource_id"]
    if not gene_set_resource_id:
        raise ValueError(f"The {info} needs a 'resource_id' parameter.")
    gene_set_resource = pipeline.repository.get_resource(
        gene_set_resource_id)
    if gene_set_resource is None:
        raise ValueError(f"The {gene_set_resource_id} is not available.")

    input_gene_list = info.parameters.get("input_gene_list")
    if input_gene_list is None:
        raise ValueError(f"The {input} must have an 'input_gene_list' "
                         "parameter")
    input_gene_list_info = pipeline.get_attribute_info(input_gene_list)
    if input_gene_list_info is None:
        raise ValueError(f"The {input_gene_list} is not privided by the "
                         "pipeline.")
    if input_gene_list_info.value_type != "object":
        raise ValueError(f"The {input_gene_list} privided by the pipeline "
                         "is not of type object.")
    return GeneSetAnnotator(
        pipeline,
        info,
        gene_set_resource,
        input_gene_list,
    )


class GeneSetAnnotator(AnnotatorBase):
    """Gene set annotator class."""

    DEFAULT_AGGREGATOR_TYPE = "list"

    def __init__(
        self,
        pipeline: AnnotationPipeline | None,
        info: AnnotatorInfo,
        gene_set_resource: GenomicResource,
        input_gene_list: str,
    ):
        self.gene_set_resource = gene_set_resource
        self.gene_set_collection = build_gene_set_collection_from_resource(
            self.gene_set_resource)
        self.gene_sets: list[GeneSet] | None = None
        self.input_gene_list = input_gene_list

        info.resources += [gene_set_resource]

        info.documentation = (
            "This gene set collection annotator uses the "
            f"**{self.gene_set_collection.collection_id}** "
            f"gene set collection."
        )
        self._info = info
        super().__init__(pipeline, info)

        self.aggregators: dict[str, str] = {}
        for attribute_config in self._info.attributes:
            if attribute_config.source == "in_sets":
                continue
            aggregator_type = attribute_config.parameters.get("aggregator")
            if aggregator_type is None:
                aggregator_type = self.attribute_descriptions[
                    attribute_config.source
                ].params.get("aggregator", self.DEFAULT_AGGREGATOR_TYPE)
            else:
                validate_aggregator(aggregator_type)
            self.aggregators[attribute_config.source] = aggregator_type

    def get_all_attribute_descriptions(self) -> dict[str, AttributeDesc]:
        gene_sets_list = self.gene_set_collection \
            .get_gene_sets_list_statistics()
        if gene_sets_list is None:
            logger.info(
                "The gene set collection statistics for %s is empty.",
                self.gene_set_collection.collection_id,
            )
            self.gene_set_collection.load()
            gene_sets_list = [
                {"name": gs.name, "count": gs.count,
                 "desc": gs.desc or gs.name}
                for gs in sorted(
                    self.gene_set_collection.get_all_gene_sets(),
                    key=lambda gs: (-gs.count, gs.name),
                )
            ]
        in_sets_desc = AttributeDesc(
            source="in_sets", type="object", description=(
                "List of the gene sets of the collection, "
                "which have at least one gene from the input gene "
                "list"
            ))
        source_type_desc = {
            "in_sets": in_sets_desc,
        }
        source_type_desc["in_sets"] = in_sets_desc
        source_type_desc.update({
            gs["name"]: AttributeDesc(
                source=gs["name"],
                type="object",
                description=f"({gs['count']}) {gs['desc']}",
                default=False,
                params={"aggregator": self.DEFAULT_AGGREGATOR_TYPE},
            )
            for gs in gene_sets_list
        })
        return source_type_desc

    @property
    def used_context_attributes(self) -> tuple[str, ...]:
        return (self.input_gene_list,)

    def open(self) -> Annotator:
        self.gene_set_collection.load()
        self.gene_sets = self.gene_set_collection.get_all_gene_sets()
        super().open()
        return self

    def _do_annotate(
        self,
        annotatable: Annotatable | None,  # noqa: ARG002
        context: dict[str, Any],
    ) -> dict[str, Any]:
        genes = context.get(self.input_gene_list)
        if genes is None:
            return self._empty_result()
        genes_set = set(genes)

        in_sets: list[str] = []
        output: dict[str, Any] = {"in_sets": in_sets}
        if self.gene_sets is None:
            raise ValueError(
                f"The GeneSetAnnotator {self.gene_set_resource} "
                f"is not open.")
        for gs in self.gene_sets:
            intersecting = list(genes_set.intersection(set(gs.syms)))
            aggregator_type = self.aggregators.get(
                gs.name, self.DEFAULT_AGGREGATOR_TYPE)
            agg = build_aggregator(aggregator_type)
            for gene in intersecting:
                agg.add(gene)
            output[gs.name] = agg.get_final()
            if intersecting:
                in_sets.append(gs.name)

        return output
