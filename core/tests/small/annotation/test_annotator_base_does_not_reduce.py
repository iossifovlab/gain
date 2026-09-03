# pylint: disable=C0114,C0116
"""``AnnotatorBase`` does not aggregate anything (gain#1133).

The base used to reduce: a ``_do_annotate`` handing back a source-keyed
dict of raw values had each attribute's values folded by the aggregator
the attribute named.  Every annotator in gain that reduces now does so
itself and says so with an :class:`AggregatedValues`, so the base's fold
is gone and two cases are left -- an ``AggregatedValues`` passes through
untouched, and anything else is renamed from source to attribute name
and handed on **unreduced**.

The unreduced half is the behaviour change, and is pinned here rather
than left to be inferred from the absence of a branch: an annotator that
still answers the legacy shape and names an aggregator used to get a
folded value and now gets its list back whole.  No annotator in gain is
in that position -- this is the contract as seen by one out of tree.
"""

import pathlib
from typing import Any

import pytest
from gain.annotation.annotatable import Annotatable, Position
from gain.annotation.annotation_config import AnnotatorInfo, AttributeConfig
from gain.annotation.annotation_pipeline import AttributeSpec
from gain.annotation.annotator_base import AggregatedValues, AnnotatorBase


class _LegacyShapedAnnotator(AnnotatorBase):
    """Answers the legacy shape: source-keyed, values not yet reduced."""

    def __init__(
        self, work_dir: pathlib.Path, values: dict[str, Any],
        *, aggregator: str | None = "list",
    ) -> None:
        self._values = values
        super().__init__(None, AnnotatorInfo(
            "legacy_shaped",
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
    ) -> dict[str, Any]:
        return dict(self._values)


class _AlreadyReducedAnnotator(AnnotatorBase):
    """Answers an ``AggregatedValues``: keyed by NAME, already folded."""

    def __init__(self, work_dir: pathlib.Path, value: Any) -> None:
        self._value = value
        super().__init__(None, AnnotatorInfo(
            "already_reduced",
            attributes=[AttributeConfig(
                name="renamed", source="score_id", aggregator="list",
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
    ) -> dict[str, Any]:
        return AggregatedValues([("renamed", self._value)])


@pytest.fixture
def annotatable() -> Annotatable:
    return Position("chr1", 10)


def test_a_legacy_shaped_list_is_renamed_and_left_unreduced(
    tmp_path: pathlib.Path, annotatable: Annotatable,
) -> None:
    """A ``mode`` aggregator would have collapsed this to ``1.0``."""
    annotator = _LegacyShapedAnnotator(
        tmp_path, {"score_id": [1.0, 1.0, 2.0]}, aggregator="mode")

    assert annotator.annotate(annotatable, {}) == {"renamed": [1.0, 1.0, 2.0]}


def test_a_legacy_shaped_scalar_is_renamed(
    tmp_path: pathlib.Path, annotatable: Annotatable,
) -> None:
    annotator = _LegacyShapedAnnotator(
        tmp_path, {"score_id": 0.5}, aggregator=None)

    assert annotator.annotate(annotatable, {}) == {"renamed": 0.5}


def test_a_source_the_annotator_did_not_answer_comes_back_none(
    tmp_path: pathlib.Path, annotatable: Annotatable,
) -> None:
    annotator = _LegacyShapedAnnotator(tmp_path, {}, aggregator=None)

    assert annotator.annotate(annotatable, {}) == {"renamed": None}


def test_an_aggregated_values_passes_through_untouched(
    tmp_path: pathlib.Path, annotatable: Annotatable,
) -> None:
    """Already keyed by name and already folded: nothing left to do."""
    annotator = _AlreadyReducedAnnotator(tmp_path, [1.0, 2.0])

    assert annotator.annotate(annotatable, {}) == {"renamed": [1.0, 2.0]}


def test_batch_annotate_renames_without_reducing_too(
    tmp_path: pathlib.Path, annotatable: Annotatable,
) -> None:
    """``annotate`` and ``batch_annotate`` answer through the same seam."""
    annotator = _LegacyShapedAnnotator(
        tmp_path, {"score_id": [1.0, 1.0, 2.0]}, aggregator="mode")

    results = annotator.batch_annotate([annotatable, annotatable], [{}, {}])

    assert results == [
        {"renamed": [1.0, 1.0, 2.0]},
        {"renamed": [1.0, 1.0, 2.0]},
    ]


def test_a_none_annotatable_answers_none_per_attribute(
    tmp_path: pathlib.Path,
) -> None:
    annotator = _LegacyShapedAnnotator(tmp_path, {"score_id": [1.0]})

    assert annotator.annotate(None, {}) == {"renamed": None}
