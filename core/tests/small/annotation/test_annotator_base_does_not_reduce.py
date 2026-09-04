# pylint: disable=W0621,C0114,C0116,W0212
"""``AnnotatorBase`` hands through what ``_do_annotate`` answers.

The base used to do two things to a ``_do_annotate`` result: fold each
attribute's raw values with the aggregator the attribute named, and turn
the result's SOURCE keys into attribute NAMES.  gain#1133 retired the
fold -- every annotator that reduces does so itself -- and gain#1134
retired the rename: every annotator answers an :class:`AggregatedValues`
keyed by attribute name, and the base has nothing left to do to it.

What is pinned here is therefore the absence of both: an answer comes
back exactly as handed, through ``annotate`` and ``batch_annotate``
alike, and the one result the base builds for itself -- the answer for
a ``None`` annotatable -- is keyed the same way as everything else.
"""

import pathlib
from typing import Any

import pytest
from gain.annotation.annotatable import Annotatable, Position
from gain.annotation.annotation_config import AnnotatorInfo, AttributeConfig
from gain.annotation.annotation_pipeline import AttributeSpec
from gain.annotation.annotator_base import AggregatedValues, AnnotatorBase


class _StubAnnotator(AnnotatorBase):
    """Answers a canned :class:`AggregatedValues`, AS HANDED.

    One attribute whose name differs from its source, so a result keyed
    the wrong way is visible in the assertion rather than coinciding
    with the right one.
    """

    def __init__(
        self, work_dir: pathlib.Path, result: AggregatedValues,
        *, aggregator: str | None = "list",
    ) -> None:
        self._result = result
        super().__init__(None, AnnotatorInfo(
            "stub",
            attributes=[AttributeConfig(
                name="renamed", source="score_id", aggregator=aggregator,
            )],
            parameters={"work_dir": str(work_dir)},
        ))

    def get_attribute_specs(self) -> dict[str, AttributeSpec]:
        return {"score_id": AttributeSpec(
            source="score_id", value_type="float", description="",
        )}

    def _do_annotate(
        self,
        annotatable: Annotatable | None,
        context: dict[str, Any],
    ) -> AggregatedValues:
        return self._result


@pytest.fixture
def annotatable() -> Annotatable:
    return Position("chr1", 10)


def test_what_do_annotate_answers_is_what_annotate_answers(
    tmp_path: pathlib.Path, annotatable: Annotatable,
) -> None:
    """Already keyed by name and already folded: nothing left to do.

    A ``mode`` aggregator would have collapsed the list to ``1.0`` had
    the base still been folding, so the list coming back whole is the
    proof that it does not.
    """
    annotator = _StubAnnotator(
        tmp_path, AggregatedValues([("renamed", [1.0, 1.0, 2.0])]),
        aggregator="mode")

    assert annotator.annotate(annotatable, {}) == {"renamed": [1.0, 1.0, 2.0]}


def test_batch_annotate_hands_each_answer_through_too(
    tmp_path: pathlib.Path, annotatable: Annotatable,
) -> None:
    """``annotate`` and ``batch_annotate`` answer through the same seam."""
    annotator = _StubAnnotator(
        tmp_path, AggregatedValues([("renamed", [1.0, 1.0, 2.0])]),
        aggregator="mode")

    results = annotator.batch_annotate([annotatable, annotatable], [{}, {}])

    assert results == [
        {"renamed": [1.0, 1.0, 2.0]},
        {"renamed": [1.0, 1.0, 2.0]},
    ]


def test_a_none_annotatable_answers_none_under_the_attribute_name(
    tmp_path: pathlib.Path,
) -> None:
    annotator = _StubAnnotator(
        tmp_path, AggregatedValues([("renamed", [1.0])]))

    assert annotator.annotate(None, {}) == {"renamed": None}


def test_a_none_in_a_batch_answers_none_under_the_attribute_name(
    tmp_path: pathlib.Path, annotatable: Annotatable,
) -> None:
    annotator = _StubAnnotator(
        tmp_path, AggregatedValues([("renamed", [1.0])]))

    results = annotator.batch_annotate([annotatable, None], [{}, {}])

    assert results == [{"renamed": [1.0]}, {"renamed": None}]


def test_the_empty_result_is_an_aggregated_values_keyed_by_name(
    tmp_path: pathlib.Path,
) -> None:
    """The one result the base builds itself has the seam's one shape.

    Keyed by attribute NAME like every answer an annotator hands back,
    and typed as one, so an annotator returning it from ``_do_annotate``
    -- the chromosome-not-covered guard, the region-too-long guard --
    is answering the same contract as its real results.
    """
    annotator = _StubAnnotator(
        tmp_path, AggregatedValues([("renamed", [1.0])]))

    empty = annotator._empty_result()

    assert isinstance(empty, AggregatedValues)
    assert empty == {"renamed": None}
