"""The configuration refusals of the ``AnnotatorBase`` constructor.

The constructor refuses a spec that is not an ``AttributeSpec``, an
attribute whose source the annotator does not support, and an
annotator configuration without a ``work_dir`` parameter.  Each refusal
is pinned at the public seam, the constructor of a minimal subclass.
"""

from typing import Any

import pytest
from gain.annotation.annotatable import Annotatable
from gain.annotation.annotation_config import AnnotatorInfo, AttributeConfig
from gain.annotation.annotation_pipeline import AttributeSpec
from gain.annotation.annotator_base import AnnotatedValues, AnnotatorBase


class _SpecsAnnotator(AnnotatorBase):
    """Declares the specs it is handed."""

    def __init__(
        self,
        specs: dict[str, Any],
        parameters: dict[str, Any],
        attributes: list[AttributeConfig] | None = None,
    ) -> None:
        self._specs = specs
        super().__init__(None, AnnotatorInfo(
            "stub", attributes=attributes or [], parameters=parameters))

    def get_attribute_specs(self) -> dict[str, AttributeSpec]:
        return self._specs

    def _do_annotate(
        self,
        annotatable: Annotatable | None,
        context: dict[str, Any],
    ) -> AnnotatedValues:
        return AnnotatedValues()


def _spec() -> AttributeSpec:
    return AttributeSpec(
        source="score", value_type="float", description="",
        is_default=True)


def test_a_spec_that_is_not_an_attribute_spec_is_refused() -> None:
    with pytest.raises(
        TypeError,
        match="Invalid attribute spec for source 'score' in annotator stub",
    ):
        _SpecsAnnotator({"score": "not a spec"}, {"work_dir": "w"})


def test_a_configuration_without_work_dir_is_refused() -> None:
    with pytest.raises(ValueError, match="Missing a 'work_dir' parameter"):
        _SpecsAnnotator({"score": _spec()}, {})


def test_an_attribute_with_an_unsupported_source_is_refused() -> None:
    with pytest.raises(
        ValueError,
        match="The attribute source 'missing' is not supported for the"
              " annotator stub",
    ):
        _SpecsAnnotator(
            {"score": _spec()}, {"work_dir": "w"},
            attributes=[AttributeConfig(name="x", source="missing")])
