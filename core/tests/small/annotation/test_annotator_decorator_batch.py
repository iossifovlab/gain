# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""The annotator decorators forward the batch path to the child (gain#1984).

The stub child below works only in batches: its ``annotate`` refuses,
and its ``batch_annotate`` records each call and answers lazily.  A
decorator that fell back to the per-row default of ``Annotator`` would
reach the refusing ``annotate``; one that forwarded the batch would
see the child called once per batch.
"""

import pathlib
from collections.abc import Iterable, Iterator, Sequence
from typing import Any

import pytest
from gain.annotation import annotation_factory
from gain.annotation.annotatable import Annotatable, Position
from gain.annotation.annotation_config import AnnotatorInfo, AttributeConfig
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.annotation.annotation_pipeline import (
    AnnotationPipeline,
    AttributeSpec,
    InputAnnotableAnnotatorDecorator,
    ValueTransformAnnotatorDecorator,
)
from gain.annotation.annotator_base import AnnotatedValues, AnnotatorBase
from gain.genomic_resources.testing import build_inmemory_test_repository

from tests.small.annotation.conftest import DummyAnnotator, typed_attribute


class BatchOnlyAnnotator(AnnotatorBase):
    """An annotator with a batch path and no single-row path."""

    def __init__(
        self, info: AnnotatorInfo, *, pipeline: Any = None,
        dependencies: tuple[str, ...] = (),
    ) -> None:
        self.batch_calls: list[list[Annotatable | None]] = []
        self.dependencies = dependencies
        super().__init__(pipeline, info)

    def get_attribute_specs(self) -> dict[str, AttributeSpec]:
        return {
            "value": AttributeSpec(
                source="value", value_type="int", description="A value"),
        }

    @property
    def used_context_attributes(self) -> tuple[str, ...]:
        return self.dependencies

    def annotate(
        self, annotatable: Annotatable | None, context: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError("batch only")

    def _do_annotate(
        self, annotatable: Annotatable, context: dict[str, Any],
    ) -> AnnotatedValues:
        raise NotImplementedError("batch only")

    def batch_annotate(  # type: ignore[override]
        self,
        annotatables: Sequence[Annotatable | None],
        contexts: list[dict[str, Any]],
        batch_work_dir: str | None = None,
    ) -> Iterable[dict[str, Any]]:
        self.batch_calls.append(list(annotatables))
        return self._answers(annotatables)

    @staticmethod
    def _answers(
        annotatables: Sequence[Annotatable | None],
    ) -> Iterator[dict[str, Any]]:
        for annotatable in annotatables:
            yield {"value": None if annotatable is None
                   else annotatable.position}


def batch_only_info(
    work_dir: pathlib.Path, *,
    annotator_parameters: dict[str, Any] | None = None,
    **attribute_parameters: Any,
) -> AnnotatorInfo:
    return AnnotatorInfo(
        "batch_only",
        attributes=[AttributeConfig(
            name="value", source="value",
            parameters=attribute_parameters or {})],
        parameters={"work_dir": str(work_dir), **(annotator_parameters or {})},
    )


class ShiftedPositionProvider(AnnotatorBase):
    """Provides ``shifted``, the input position moved by 100."""

    def get_attribute_specs(self) -> dict[str, AttributeSpec]:
        return {
            "shifted": AttributeSpec(
                source="shifted", value_type="object",
                description="The shifted position",
                attribute_type="annotatable"),
        }

    def _do_annotate(
        self, annotatable: Annotatable, context: dict[str, Any],
    ) -> AnnotatedValues:
        return AnnotatedValues({"shifted": Position(
            annotatable.chrom, annotatable.position + 100)})


@pytest.fixture
def pipeline() -> AnnotationPipeline:
    """A pipeline whose one annotator provides the annotatable ``lifted``."""
    pipeline = AnnotationPipeline(build_inmemory_test_repository({}))
    provider = DummyAnnotator(
        attributes=[typed_attribute(
            "lifted", "object", attribute_type="annotatable")])
    provider.pipeline = pipeline
    pipeline.add_annotator(provider)
    return pipeline


def test_value_transform_decorator_forwards_one_batch_to_the_child(
    tmp_path: pathlib.Path,
) -> None:
    child = BatchOnlyAnnotator(
        batch_only_info(tmp_path, value_transform="value * 10"))
    decorator = ValueTransformAnnotatorDecorator.decorate(child)
    annotatables = [Position("chr1", 1), Position("chr1", 2)]

    results = list(decorator.batch_annotate(annotatables, [{}, {}]))

    assert child.batch_calls == [annotatables]
    assert results == [{"value": 10}, {"value": 20}]


def test_input_annotatable_decorator_forwards_the_substituted_batch(
    tmp_path: pathlib.Path, pipeline: AnnotationPipeline,
) -> None:
    child = BatchOnlyAnnotator(
        batch_only_info(
            tmp_path,
            annotator_parameters={"input_annotatable": "lifted"}),
        pipeline=pipeline)
    decorator = InputAnnotableAnnotatorDecorator.decorate(child)
    lifted = [Position("chr1", 7), None, Position("chr1", 9)]
    contexts = [{"lifted": annotatable} for annotatable in lifted]

    results = list(decorator.batch_annotate(
        [Position("chr1", 1), Position("chr1", 2), Position("chr1", 3)],
        contexts))

    assert child.batch_calls == [lifted]
    assert results == [{"value": 7}, {"value": None}, {"value": 9}]


def test_input_annotatable_decorator_refuses_a_batch_value_that_is_not_one(
    tmp_path: pathlib.Path, pipeline: AnnotationPipeline,
) -> None:
    child = BatchOnlyAnnotator(
        batch_only_info(
            tmp_path,
            annotator_parameters={"input_annotatable": "lifted"}),
        pipeline=pipeline)
    decorator = InputAnnotableAnnotatorDecorator.decorate(child)
    contexts = [{"lifted": Position("chr1", 7)}, {"lifted": "chr1:9"}]

    with pytest.raises(ValueError, match="is not an Annotatable"):
        list(decorator.batch_annotate(
            [Position("chr1", 1), Position("chr1", 2)], contexts))


def test_value_transform_decorator_reports_the_child_context_attributes(
    tmp_path: pathlib.Path,
) -> None:
    child = BatchOnlyAnnotator(
        batch_only_info(tmp_path, value_transform="value * 10"),
        dependencies=("gene_list",))

    decorator = ValueTransformAnnotatorDecorator.decorate(child)

    assert decorator.used_context_attributes == ("gene_list",)


def test_both_decorators_report_the_child_and_input_attributes(
    tmp_path: pathlib.Path, pipeline: AnnotationPipeline,
) -> None:
    child = BatchOnlyAnnotator(
        batch_only_info(
            tmp_path,
            annotator_parameters={"input_annotatable": "lifted"},
            value_transform="value * 10"),
        pipeline=pipeline, dependencies=("gene_list",))

    decorator = ValueTransformAnnotatorDecorator.decorate(
        InputAnnotableAnnotatorDecorator.decorate(child))

    assert decorator.used_context_attributes == ("gene_list", "lifted")


def test_pipeline_forwards_each_batch_through_both_decorators(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    children: list[BatchOnlyAnnotator] = []

    def build_batch_only(
        pipeline: AnnotationPipeline, info: AnnotatorInfo,
    ) -> BatchOnlyAnnotator:
        children.append(BatchOnlyAnnotator(info, pipeline=pipeline))
        return children[-1]

    annotation_factory.get_available_annotator_types()
    monkeypatch.setitem(
        annotation_factory._ANNOTATOR_FACTORY_REGISTRY,
        "shifted_position", ShiftedPositionProvider)
    monkeypatch.setitem(
        annotation_factory._ANNOTATOR_FACTORY_REGISTRY,
        "batch_only", build_batch_only)
    pipeline = load_pipeline_from_yaml("""
        - shifted_position:
            attributes:
            - shifted
        - batch_only:
            input_annotatable: shifted
            attributes:
            - source: value
              value_transform: value * 10
    """, build_inmemory_test_repository({}), work_dir=tmp_path)

    first = pipeline.batch_annotate(
        [Position("chr1", 1), Position("chr1", 2)])
    second = pipeline.batch_annotate([Position("chr1", 3)])

    assert [len(batch) for batch in children[0].batch_calls] == [2, 1]
    assert [context["value"] for context in first + second] == [
        1010, 1020, 1030]
