# pylint: disable=W0621,C0114,C0116
"""``AnnotatorBase`` hands through what ``_do_annotate`` answers.

The base used to do two things to a ``_do_annotate`` result: fold each
attribute's raw values with the aggregator the attribute named, and turn
the result's SOURCE keys into attribute NAMES.  gain#1133 retired the
fold -- every annotator that reduces does so itself -- and gain#1134
retired the rename: every annotator answers an :class:`AnnotatedValues`
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
from gain.annotation.annotator_base import AnnotatedValues, AnnotatorBase


class _StubAnnotator(AnnotatorBase):
    """Answers a canned :class:`AnnotatedValues`, AS HANDED.

    One attribute whose name differs from its source, so a result keyed
    the wrong way is visible in the assertion rather than coinciding
    with the right one; and naming ``mode``, an aggregator that would
    collapse a list to one value, so a list coming back whole is the
    proof that nothing folded it.
    """

    def __init__(
        self, work_dir: pathlib.Path, result: AnnotatedValues,
    ) -> None:
        self._result = result
        super().__init__(None, AnnotatorInfo(
            "stub",
            attributes=[AttributeConfig(
                name="renamed", source="score_id", aggregator="mode",
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
    ) -> AnnotatedValues:
        return self._result


@pytest.fixture
def annotatable() -> Annotatable:
    return Position("chr1", 10)


def test_what_do_annotate_answers_is_what_annotate_answers(
    tmp_path: pathlib.Path, annotatable: Annotatable,
) -> None:
    annotator = _StubAnnotator(
        tmp_path, AnnotatedValues([("renamed", [1.0, 1.0, 2.0])]))

    assert annotator.annotate(annotatable, {}) == {"renamed": [1.0, 1.0, 2.0]}


def test_batch_annotate_hands_each_answer_through_too(
    tmp_path: pathlib.Path, annotatable: Annotatable,
) -> None:
    """The same seam for a batch, the ``None`` short-circuit included."""
    annotator = _StubAnnotator(
        tmp_path, AnnotatedValues([("renamed", [1.0, 1.0, 2.0])]))

    results = annotator.batch_annotate([annotatable, None], [{}, {}])

    assert list(results) == [
        {"renamed": [1.0, 1.0, 2.0]},
        {"renamed": None},
    ]


def test_an_attribute_the_annotator_did_not_answer_stays_absent(
    tmp_path: pathlib.Path, annotatable: Annotatable,
) -> None:
    """The base fills nothing in: answering every attribute is the
    annotator's job, with ``_empty_result`` for the no-value case."""
    annotator = _StubAnnotator(tmp_path, AnnotatedValues())

    assert annotator.annotate(annotatable, {}) == {}


def test_a_none_annotatable_answers_none_under_the_attribute_name(
    tmp_path: pathlib.Path,
) -> None:
    annotator = _StubAnnotator(
        tmp_path, AnnotatedValues([("renamed", [1.0])]))

    assert annotator.annotate(None, {}) == {"renamed": None}
