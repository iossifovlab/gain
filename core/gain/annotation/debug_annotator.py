
from typing import Any

from gain.annotation.annotatable import Annotatable
from gain.annotation.annotation_config import AnnotatorInfo
from gain.annotation.annotation_pipeline import (
    AnnotationPipeline,
    Annotator,
    AttributeDesc,
)
from gain.annotation.annotator_base import AnnotatorBase


class HelloWorldAnnotator(AnnotatorBase):
    """Defines example annotator."""

    def get_all_attribute_descriptions(self) -> dict[str, AttributeDesc]:
        return {
            "hi": AttributeDesc(
                source="hi",
                type="str",
                description="Test attribute",
                internal=False,
            ),
        }

    def _do_annotate(
        self, annotatable: Annotatable,  # noqa: ARG002
        context: dict[str, Any],  # noqa: ARG002
    ) -> dict[str, Any]:
        return {attr.name: "hello world" for attr in self._info.attributes}


def build_annotator(pipeline: AnnotationPipeline,
                    info: AnnotatorInfo) -> Annotator:
    """Create an example hello world annotator."""
    return HelloWorldAnnotator(pipeline, info)
