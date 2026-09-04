
from typing import Any

from gain.annotation.annotatable import Annotatable
from gain.annotation.annotation_config import AnnotatorInfo
from gain.annotation.annotation_pipeline import (
    AnnotationPipeline,
    Annotator,
    AttributeSpec,
)
from gain.annotation.annotator_base import AggregatedValues, AnnotatorBase


class HelloWorldAnnotator(AnnotatorBase):
    """Defines example annotator."""

    def get_attribute_specs(self) -> dict[str, AttributeSpec]:
        return {
            "hi": AttributeSpec(
                source="hi",
                value_type="str",
                description="Test attribute",
                internal_default=False,
            ),
        }

    def _do_annotate(
        self, annotatable: Annotatable,  # ruff: ignore[unused-method-argument]
        context: dict[str, Any],  # ruff: ignore[unused-method-argument]
    ) -> AggregatedValues:
        return AggregatedValues(
            (attr.name, "hello world") for attr in self._attributes)


def build_annotator(pipeline: AnnotationPipeline,
                    info: AnnotatorInfo) -> Annotator:
    """Create an example hello world annotator."""
    return HelloWorldAnnotator(pipeline, info)
