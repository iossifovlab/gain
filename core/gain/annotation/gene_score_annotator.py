"""Module containing the gene score annotator."""

from typing import Any

from gain import logging
from gain.annotation.annotatable import Annotatable
from gain.annotation.annotation_config import (
    AnnotatorInfo,
    Attribute,
)
from gain.annotation.annotation_pipeline import (
    AnnotationPipeline,
    Annotator,
    AttributeSpec,
)
from gain.annotation.annotator_base import AnnotatedValues
from gain.annotation.score_annotator_base import ScoreAnnotatorBase
from gain.gene_scores.gene_scores import (
    GeneScore,
    build_gene_score_from_resource,
)
from gain.genomic_resources import GenomicResource
from gain.genomic_resources.resource_types import GENE_SCORE_TYPE

logger = logging.getLogger(__name__)


def build_gene_score_annotator(pipeline: AnnotationPipeline,
                               info: AnnotatorInfo) -> Annotator:
    """Create a gene score annotator."""
    # Before the input_gene_list check: a holder whose resource is the
    # wrong kind is told THAT first, rather than about a pipeline
    # attribute they would go on to wire up correctly for a resource this
    # annotator was never going to accept.
    gene_score_resource = GeneScoreAnnotator.resolve_resource(pipeline, info)
    input_gene_list = GeneScoreAnnotator.resolve_input_gene_list(
        pipeline, info)
    return GeneScoreAnnotator(pipeline, info,
                              gene_score_resource, input_gene_list)


class GeneScoreAnnotator(ScoreAnnotatorBase[GeneScore]):
    """Gene score annotator class."""

    ACCEPTED_RESOURCE_TYPES = (GENE_SCORE_TYPE,)

    def __init__(self, pipeline: AnnotationPipeline | None,
                 info: AnnotatorInfo,
                 gene_score_resource: GenomicResource,
                 input_gene_list: str):

        self.gene_score_resource = gene_score_resource
        info.resources += [gene_score_resource]
        self.input_gene_list = input_gene_list
        super().__init__(
            pipeline, info,
            build_gene_score_from_resource(self.gene_score_resource))

    def _score_attribute_spec(self, score_id: str) -> AttributeSpec:
        score_def = self.score.score_definitions[score_id]
        return AttributeSpec(
            source=score_id,
            value_type="object",
            description=score_def.desc,
            supports_aggregation=True,
            folded_value_type=score_def.value_type,
        )

    def _aggregator_value_type(self, attr: Attribute) -> str | None:  # ruff: ignore[unused-method-argument]
        return None

    def _apply_gene_aggregator(
        self, attr: Attribute, value: Any,
    ) -> Any:
        """Reduce one attribute's per-gene values with its aggregator.

        Its own rather than :func:`fold_own_values` because the values
        are a MAPPING -- one score per gene symbol -- and folding them
        means folding the mapping's values.  How the fold itself happens
        is :meth:`Attribute.fold`'s statement, shared with every other
        annotator that reduces (gain#1133).
        """
        if attr.aggregator is None or not isinstance(value, dict):
            return value
        return attr.fold(list(value.values()))

    @property
    def used_context_attributes(self) -> tuple[str, ...]:
        return (self.input_gene_list,)

    def _do_annotate(
        self,
        annotatable: Annotatable,  # ruff: ignore[unused-method-argument]
        context: dict[str, Any],
    ) -> AnnotatedValues:
        """Answer the input gene list's scores, already reduced.

        This annotator reduces for ITSELF, and its values are per-GENE
        rather than per-record: the input gene list names the genes, the
        score answers one value for each, and the attribute's aggregator
        folds those into one.  Nothing the base could do for it -- which
        is why it kept its own reduction while the base still had one,
        and why it now answers an :class:`AnnotatedValues` (gain#1133).

        The names are read here, at answer time, for the reason
        :class:`AnnotatedValues` states.
        """
        genes = context.get(self.input_gene_list)
        if genes is None:
            return self._empty_result()
        return AnnotatedValues(
            (attr.name, self._apply_gene_aggregator(attr, {
                sym: score
                for sym in genes
                if (score := self.score.get_gene_value(attr.source, sym))
                is not None
            }))
            for attr in self.attributes
        )
