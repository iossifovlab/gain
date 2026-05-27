import textwrap
from copy import deepcopy
from typing import Any

from gain.annotation.annotatable import Annotatable
from gain.annotation.annotation_config import AnnotatorInfo
from gain.annotation.annotation_pipeline import (
    AnnotationPipeline,
    Annotator,
    AttributeSpec,
)
from gain.annotation.annotator_base import AnnotatorBase
from gain.genomic_resources.utils import build_chrom_mapping


class ChromMappingAnnotator(AnnotatorBase):
    """Annotator for adjusting chromosome values."""

    def __init__(self, pipeline: AnnotationPipeline, info: AnnotatorInfo):

        mapping = info.parameters.get("mapping")
        add_prefix = info.parameters.get("add_prefix")
        del_prefix = info.parameters.get("del_prefix")
        filename = info.parameters.get("filename")

        assert filename is None

        mapping_config = {
            "chrom_mapping": {
                "mapping": mapping,
                "add_prefix": add_prefix,
                "del_prefix": del_prefix,
            },
        }
        self.chrom_mapping = build_chrom_mapping(None, mapping_config)
        if self.chrom_mapping is None:
            raise ValueError(
                "ChromosomeAnnotator requires a valid chrom_mapping config")

        info.documentation += textwrap.dedent(f"""

Annotator that maps chromsomes from one naming convention to another.

<a href="{self.BASE_DOC_URL}#chromosome-mapping-annotator" target="_blank">More info</a>

""")  # noqa

        super().__init__(pipeline, info)

    def get_attribute_specs(self) -> dict[str, AttributeSpec]:
        return {
            "renamed_chromosome": AttributeSpec(
                source="renamed_chromosome",
                value_type="annotatable",
                description="Allele with renamed chromosome.",
                internal_default=True,
                attribute_type="annotatable",
            ),
        }

    def _do_annotate(
        self,
        annotatable: Annotatable,
        context: dict[str, Any],  # noqa: ARG002
    ) -> dict[str, Any]:
        new_annotatable = deepcopy(annotatable)
        assert self.chrom_mapping is not None

        new_chrom = self.chrom_mapping(new_annotatable.chrom)
        if new_chrom is None:
            return {attr.name: None for attr in self._attributes}
        new_annotatable._chrom = new_chrom  # noqa: SLF001
        return {attr.name: new_annotatable for attr in self._attributes}


def build_chrom_mapping_annotator(
    pipeline: AnnotationPipeline, info: AnnotatorInfo,
) -> Annotator:
    return ChromMappingAnnotator(pipeline, info)
