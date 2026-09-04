from __future__ import annotations

from typing import Any

from gain.annotation.annotatable import Annotatable
from gain.annotation.annotation_pipeline import (
    AnnotationPipeline,
    Annotator,
    AnnotatorInfo,
    AttributeSpec,
)
from gain.annotation.annotator_base import AggregatedValues, AnnotatorBase

from experimental_followup_annotator.annotator import (
    annotate_experimental_followup,
)


class ExperimentalFollowupAnnotator(AnnotatorBase):
    """Annotator that flags variants for experimental follow-up.

    This annotator is intended to run at the end of a GAIn annotation
    pipeline. It reads annotation values already produced by earlier
    annotators and combines them into a simple yes/no follow-up decision.
    """

    def get_attribute_specs(self) -> dict[str, AttributeSpec]:
        return {
            "experimental_followup": AttributeSpec(
                source="experimental_followup",
                value_type="str",
                description=(
                    "Whether this variant is selected for experimental "
                    "follow-up based on gnomAD allele frequency, "
                    "phyloP7way conservation, and ClinVar clinical "
                    "significance"
                ),
                internal_default=False,
                is_default=True,
            ),
        }

    def _do_annotate(
        self,
        annotatable: Annotatable | None,
        context: dict[str, Any],
    ) -> AggregatedValues:
        followup = annotate_experimental_followup(
            annotatable,
            context,
        )
        # The value under every configured attribute's name; see
        # ``AggregatedValues`` for the contract.
        return self._every(followup)


def build_experimental_followup_annotator(
    pipeline: AnnotationPipeline,
    info: AnnotatorInfo,
) -> Annotator:
    return ExperimentalFollowupAnnotator(pipeline, info)