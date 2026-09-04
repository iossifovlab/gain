from __future__ import annotations

from collections.abc import Sequence
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
        # Keyed by each attribute's NAME, read off ``self._attributes``
        # here rather than spelled out: a pipeline may expose this
        # attribute under another name, and the values are final --
        # GAIn neither renames nor reduces what an annotator answers.
        return AggregatedValues(
            (attr.name, followup) for attr in self._attributes
        )

    def _do_batch_annotate(
        self,
        annotatables: Sequence[Annotatable | None],
        contexts: list[dict[str, Any]],
        batch_work_dir: str | None = None,
    ) -> list[AggregatedValues]:
        return [
            self._do_annotate(annotatable, context)
            for annotatable, context in zip(annotatables, contexts)
        ]


def build_experimental_followup_annotator(
    pipeline: AnnotationPipeline,
    info: AnnotatorInfo,
) -> Annotator:
    return ExperimentalFollowupAnnotator(pipeline, info)