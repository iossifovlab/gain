# pylint: disable=W0621,C0114,C0116,W0212,W0613


from unittest.mock import MagicMock

from gain.annotation.annotation_config import Attribute
from gain.annotation.annotation_pipeline import (
    FULL_REANNOTATION_REASON,
    AnnotationPipeline,
    Annotator,
    ReannotationPipeline,
    _build_dependency_graph,
    _get_deleted_attributes,
    _get_rerun_annotators,
    format_annotation_plan,
)

from tests.small.annotation.conftest import DummyAnnotator


def _make_pipeline_with(
    annotators: list[Annotator],
) -> AnnotationPipeline:
    pipeline = AnnotationPipeline(MagicMock())
    for annotator in annotators:
        pipeline.add_annotator(annotator)
    return pipeline


def test_dependency_graph_empty() -> None:
    # empty graph
    pipeline = _make_pipeline_with([])
    graph = _build_dependency_graph(pipeline)
    assert not graph


def test_dependency_graph_no_dependencies() -> None:
    # annotator can depend on nothing
    dummy_annotator_1 = DummyAnnotator(
        [Attribute("attr_1", "attr_1", internal=False, parameters={})],
    )
    dummy_annotator_2 = DummyAnnotator(
        [Attribute("attr_2", "attr_2", internal=False, parameters={})],
    )
    pipeline = _make_pipeline_with([dummy_annotator_1, dummy_annotator_2])
    graph = _build_dependency_graph(pipeline)
    assert graph == {
        dummy_annotator_1.get_info(): [],
        dummy_annotator_2.get_info(): [],
    }


def test_dependency_graph_one_dependency() -> None:
    # annotator can depend on one annotator
    attribute_1 = \
        Attribute("attr_1", "attr_1", internal=False, parameters={})
    dummy_annotator_1 = DummyAnnotator([attribute_1])

    attribute_2 = \
        Attribute("attr_2", "attr_2", internal=False, parameters={})
    dummy_annotator_2 = DummyAnnotator([attribute_2], dependencies=("attr_1",))

    pipeline = _make_pipeline_with([dummy_annotator_1, dummy_annotator_2])
    graph = _build_dependency_graph(pipeline)
    assert graph == {
        dummy_annotator_1.get_info(): [],
        dummy_annotator_2.get_info(): [
            (dummy_annotator_1.get_info(), attribute_1),
        ],
    }


def test_dependency_graph_multiple_dependencies() -> None:
    # annotator can depend on multiple annotators
    attribute_1 = \
        Attribute("attr_1", "attr_1", internal=False, parameters={})
    dummy_annotator_1 = DummyAnnotator([attribute_1])

    attribute_2 = \
        Attribute("attr_2", "attr_2", internal=False, parameters={})
    dummy_annotator_2 = DummyAnnotator([attribute_2])

    attribute_3 = \
        Attribute("attr_3", "attr_3", internal=False, parameters={})
    dummy_annotator_3 = DummyAnnotator([attribute_3],
                                       dependencies=("attr_1", "attr_2"))

    pipeline = _make_pipeline_with([dummy_annotator_1,
                                    dummy_annotator_2,
                                    dummy_annotator_3])
    graph = _build_dependency_graph(pipeline)
    assert graph == {
        dummy_annotator_1.get_info(): [],
        dummy_annotator_2.get_info(): [],
        dummy_annotator_3.get_info(): [
            (dummy_annotator_1.get_info(), attribute_1),
            (dummy_annotator_2.get_info(), attribute_2),
        ],
    }


def test_dependency_graph_dependency_for_many() -> None:
    # annotator can be a dependency for multiple annotators
    attribute_1 = \
        Attribute("attr_1", "attr_1", internal=False, parameters={})
    dummy_annotator_1 = DummyAnnotator([attribute_1])

    attribute_2 = \
        Attribute("attr_2", "attr_2", internal=False, parameters={})
    dummy_annotator_2 = DummyAnnotator([attribute_2], dependencies=("attr_1",))

    attribute_3 = \
        Attribute("attr_3", "attr_3", internal=False, parameters={})
    dummy_annotator_3 = DummyAnnotator([attribute_3], dependencies=("attr_1",))

    pipeline = _make_pipeline_with([dummy_annotator_1,
                                    dummy_annotator_2,
                                    dummy_annotator_3])
    graph = _build_dependency_graph(pipeline)
    assert graph == {
        dummy_annotator_1.get_info(): [],
        dummy_annotator_2.get_info(): [
            (dummy_annotator_1.get_info(), attribute_1),
        ],
        dummy_annotator_3.get_info(): [
            (dummy_annotator_1.get_info(), attribute_1),
        ],
    }


def test_dependency_graph_grandparent_dependency() -> None:
    # annotator can be a dependency for a child as well as grandchildren
    attribute_1 = \
        Attribute("attr_1", "attr_1", internal=False, parameters={})
    dummy_annotator_1 = DummyAnnotator([attribute_1])

    attribute_2 = \
        Attribute("attr_2", "attr_2", internal=False, parameters={})
    dummy_annotator_2 = DummyAnnotator([attribute_2], dependencies=("attr_1",))

    attribute_3 = \
        Attribute("attr_3", "attr_3", internal=False, parameters={})
    dummy_annotator_3 = DummyAnnotator([attribute_3], dependencies=("attr_2",))

    pipeline = _make_pipeline_with([dummy_annotator_1,
                                    dummy_annotator_2,
                                    dummy_annotator_3])
    graph = _build_dependency_graph(pipeline)
    assert graph == {
        dummy_annotator_1.get_info(): [],
        dummy_annotator_2.get_info(): [
            (dummy_annotator_1.get_info(), attribute_1),
        ],
        dummy_annotator_3.get_info(): [
            (dummy_annotator_2.get_info(), attribute_2),
            (dummy_annotator_1.get_info(), attribute_1),
        ],
    }


def test_get_rerun_annotators_dependency_changed() -> None:
    attribute_1 = \
        Attribute("attr_1", "attr_1", internal=False, parameters={})
    dummy_annotator_1 = DummyAnnotator([attribute_1])

    attribute_2 = \
        Attribute("attr_2", "attr_2", internal=False, parameters={})
    dummy_annotator_2 = DummyAnnotator([attribute_2], dependencies=("attr_1",))

    pipeline = _make_pipeline_with([dummy_annotator_1, dummy_annotator_2])

    # should not rerun if upstream annotator hasn't changed
    rerun = _get_rerun_annotators(pipeline, [])
    assert rerun == set()

    # should rerun if upstream annotator HAS changed
    rerun = _get_rerun_annotators(pipeline, [dummy_annotator_1.get_info()])
    assert rerun == {
        dummy_annotator_2.get_info(),
    }


def test_get_rerun_annotators_internal_new_dependent() -> None:
    attribute_1 = \
        Attribute("attr_1", "attr_1", internal=True, parameters={})
    dummy_annotator_1 = DummyAnnotator([attribute_1])

    attribute_2 = \
        Attribute("attr_2", "attr_2", internal=False, parameters={})
    dummy_annotator_2 = DummyAnnotator([attribute_2], dependencies=("attr_1",))

    pipeline = _make_pipeline_with([dummy_annotator_1, dummy_annotator_2])

    # should rerun if internal and a new dependent has been added downstream
    rerun = _get_rerun_annotators(pipeline, [dummy_annotator_2.get_info()])
    assert rerun == {
        dummy_annotator_1.get_info(),
    }


def test_get_rerun_annotators_internal_dependent_rerun() -> None:
    attribute_1 = \
        Attribute("attr_1", "attr_1", internal=True, parameters={})
    dummy_annotator_1 = DummyAnnotator([attribute_1])

    attribute_2 = \
        Attribute("attr_2", "attr_2", internal=True, parameters={})
    dummy_annotator_2 = DummyAnnotator([attribute_2], dependencies=("attr_1",))

    attribute_3 = \
        Attribute("attr_3", "attr_3", internal=False, parameters={})
    dummy_annotator_3 = DummyAnnotator([attribute_3], dependencies=("attr_2",))

    pipeline = _make_pipeline_with([dummy_annotator_1,
                                    dummy_annotator_2,
                                    dummy_annotator_3])

    # should rerun if internal (annotator 1) and a downstream
    # annotator is rerun (annotator 2)
    rerun = _get_rerun_annotators(pipeline, [dummy_annotator_3.get_info()])
    assert rerun == {
        dummy_annotator_2.get_info(),
        dummy_annotator_1.get_info(),
    }


def test_get_rerun_annotators_non_internal_new_dependent() -> None:
    attribute_1 = \
        Attribute("attr_1", "attr_1", internal=False, parameters={})
    dummy_annotator_1 = DummyAnnotator([attribute_1])

    attribute_2 = \
        Attribute("attr_2", "attr_2", internal=False, parameters={})
    dummy_annotator_2 = DummyAnnotator([attribute_2], dependencies=("attr_1",))

    pipeline = _make_pipeline_with([dummy_annotator_1, dummy_annotator_2])

    # should not rerun if not internal and dependent is new
    rerun = _get_rerun_annotators(pipeline, [dummy_annotator_2.get_info()])
    assert rerun == set()


def test_get_rerun_annotators_non_internal_rerun_dependent() -> None:
    attribute_1 = \
        Attribute("attr_1", "attr_1", internal=False, parameters={})
    dummy_annotator_1 = DummyAnnotator([attribute_1])

    attribute_2 = \
        Attribute("attr_2", "attr_2", internal=True, parameters={})
    dummy_annotator_2 = DummyAnnotator([attribute_2], dependencies=("attr_1",))

    attribute_3 = \
        Attribute("attr_3", "attr_3", internal=False, parameters={})
    dummy_annotator_3 = DummyAnnotator([attribute_3], dependencies=("attr_2",))

    pipeline = _make_pipeline_with([dummy_annotator_1,
                                    dummy_annotator_2,
                                    dummy_annotator_3])

    # shouldn't rerun if not internal (annotator 1) and a downstream
    # annotator is rerun (annotator 2)
    rerun = _get_rerun_annotators(pipeline, [dummy_annotator_3.get_info()])
    assert rerun == {
        dummy_annotator_2.get_info(),
    }


def test_get_deleted_attributes() -> None:
    # attribute deleted in new pipeline
    attribute_1 = \
        Attribute("attr_1", "attr_1", internal=False, parameters={})
    dummy_annotator_1 = DummyAnnotator([attribute_1])

    attribute_2 = \
        Attribute("attr_2", "attr_2", internal=False, parameters={})
    dummy_annotator_2 = DummyAnnotator([attribute_2])

    old_pipeline = _make_pipeline_with([dummy_annotator_1])
    new_pipeline = _make_pipeline_with([dummy_annotator_2])

    assert _get_deleted_attributes(new_pipeline, old_pipeline) == ["attr_1"]


def test_get_deleted_attributes_shared_name() -> None:
    # new attribute in new pipeline shares name with old - must still delete
    attribute_1 = \
        Attribute("attr_1", "attr_1", internal=False, parameters={})
    dummy_annotator_1 = DummyAnnotator([attribute_1])

    attribute_2 = \
        Attribute("attr_1", "attr_2", internal=False, parameters={})
    dummy_annotator_2 = DummyAnnotator([attribute_2])

    old_pipeline = _make_pipeline_with([dummy_annotator_1])
    new_pipeline = _make_pipeline_with([dummy_annotator_2])

    assert _get_deleted_attributes(new_pipeline, old_pipeline) == ["attr_1"]


def test_get_deleted_attributes_ignore_internal() -> None:
    # don't try to delete internal attributes
    attribute_1 = \
        Attribute("attr_1", "attr_1", internal=True, parameters={})
    dummy_annotator_1 = DummyAnnotator([attribute_1])

    attribute_2 = \
        Attribute("attr_2", "attr_2", internal=False, parameters={})
    dummy_annotator_2 = DummyAnnotator([attribute_2])

    old_pipeline = _make_pipeline_with([dummy_annotator_1])
    new_pipeline = _make_pipeline_with([dummy_annotator_2])

    assert not _get_deleted_attributes(new_pipeline, old_pipeline)


def test_get_deleted_attributes_full_reannotation() -> None:
    # full reannotation - delete all
    attribute_1 = \
        Attribute("attr_1", "attr_1", internal=True, parameters={})
    dummy_annotator_1 = DummyAnnotator([attribute_1])

    attribute_2 = \
        Attribute("attr_2", "attr_2", internal=False, parameters={})
    dummy_annotator_2 = DummyAnnotator([attribute_2])

    old_pipeline = _make_pipeline_with([dummy_annotator_1, dummy_annotator_2])
    new_pipeline = _make_pipeline_with([dummy_annotator_1, dummy_annotator_2])

    assert _get_deleted_attributes(
        new_pipeline, old_pipeline, full_reannotation=True,
    ) == ["attr_1", "attr_2"]


def test_full_reannotation_includes_all_new_annotators() -> None:
    # full reannotation must recompute every annotator of the new pipeline,
    # even ones unchanged between old and new (the regression in #108)
    attribute_1 = \
        Attribute("attr_1", "attr_1", internal=False, parameters={})
    dummy_annotator_1 = DummyAnnotator([attribute_1])

    attribute_2 = \
        Attribute("attr_2", "attr_2", internal=False, parameters={})
    dummy_annotator_2 = DummyAnnotator([attribute_2])

    old_pipeline = _make_pipeline_with(
        [dummy_annotator_1, dummy_annotator_2])
    new_pipeline = _make_pipeline_with(
        [dummy_annotator_1, dummy_annotator_2])

    pipeline = ReannotationPipeline(
        new_pipeline, old_pipeline, full_reannotation=True)

    # every annotator of the new pipeline must be present, even though
    # the old and new pipelines are identical (worst case)
    assert pipeline.annotators == new_pipeline.annotators


def test_adjust_for_reannotation() -> None:
    attribute_1 = \
        Attribute("attr_1", "attr_1", internal=False, parameters={})
    dummy_annotator_1 = DummyAnnotator([attribute_1])

    attribute_2 = \
        Attribute("attr_2", "attr_2", internal=True, parameters={})
    dummy_annotator_2 = DummyAnnotator([attribute_2], dependencies=("attr_1",))

    attribute_3 = \
        Attribute("attr_3", "attr_3", internal=False, parameters={})
    dummy_annotator_3 = DummyAnnotator([attribute_3], dependencies=("attr_2",))

    old_pipeline = _make_pipeline_with(
        [dummy_annotator_1, dummy_annotator_2])

    new_pipeline = _make_pipeline_with(
        [dummy_annotator_1, dummy_annotator_2, dummy_annotator_3])

    pipeline = ReannotationPipeline(new_pipeline, old_pipeline)

    assert pipeline.annotators == [
        # annotator subset to run should have all rerun annotators
        dummy_annotator_2,
        # annotator subset to run should have all new annotators
        dummy_annotator_3,
    ]


# ---------------------------------------------------------------------------
# ReannotationPlan computation
# ---------------------------------------------------------------------------


def _plan_names(entries: list) -> list[str]:
    return [entry.name for entry in entries]


def test_reannotation_plan_buckets() -> None:
    # attr_1: copied (unchanged annotator, not rerun)
    # attr_2 (new annotator): added
    # attr_3 (unchanged but rerun because it depends on new attr_2): computed
    # attr_old: deleted (present only in the old pipeline)
    attribute_1 = \
        Attribute("attr_1", "attr_1", internal=False, parameters={})
    dummy_annotator_1 = DummyAnnotator([attribute_1])

    attribute_old = \
        Attribute("attr_old", "attr_old", internal=False, parameters={})
    dummy_annotator_old = DummyAnnotator([attribute_old])

    attribute_2 = \
        Attribute("attr_2", "attr_2", internal=False, parameters={})
    dummy_annotator_2 = DummyAnnotator([attribute_2])

    attribute_3 = \
        Attribute("attr_3", "attr_3", internal=False, parameters={})
    dummy_annotator_3 = DummyAnnotator([attribute_3], dependencies=("attr_2",))

    old_pipeline = _make_pipeline_with(
        [dummy_annotator_1, dummy_annotator_old, dummy_annotator_3])
    new_pipeline = _make_pipeline_with(
        [dummy_annotator_1, dummy_annotator_2, dummy_annotator_3])

    pipeline = ReannotationPipeline(new_pipeline, old_pipeline)
    plan = pipeline.plan

    assert _plan_names(plan.copied) == ["attr_1"]
    assert _plan_names(plan.added) == ["attr_2"]
    assert _plan_names(plan.computed) == ["attr_3"]
    assert _plan_names(plan.deleted) == ["attr_old"]

    # reasons
    assert plan.copied[0].reason is None
    assert plan.added[0].reason is None
    assert plan.computed[0].reason is not None
    # the trigger of the rerun is the new attr_2
    assert plan.computed[0].reason == "depends on new attr_2"


def test_reannotation_plan_computed_reason_names_consumed_attribute() -> None:
    # The COMPUTED reason must name the attribute actually consumed by the
    # dependent, not merely the trigger annotator's first attribute.
    attribute_1 = \
        Attribute("attr_1", "attr_1", internal=False, parameters={})
    dummy_annotator_1 = DummyAnnotator([attribute_1])

    # New annotator producing two attributes; the dependent consumes the
    # second one (attr_new_b), not the first (attr_new_a).
    attribute_new_a = \
        Attribute("attr_new_a", "attr_new_a", internal=False, parameters={})
    attribute_new_b = \
        Attribute("attr_new_b", "attr_new_b", internal=False, parameters={})
    dummy_annotator_new = DummyAnnotator(
        [attribute_new_a, attribute_new_b])

    attribute_3 = \
        Attribute("attr_3", "attr_3", internal=False, parameters={})
    dummy_annotator_3 = DummyAnnotator(
        [attribute_3], dependencies=("attr_new_b",))

    old_pipeline = _make_pipeline_with(
        [dummy_annotator_1, dummy_annotator_3])
    new_pipeline = _make_pipeline_with(
        [dummy_annotator_1, dummy_annotator_new, dummy_annotator_3])

    pipeline = ReannotationPipeline(new_pipeline, old_pipeline)
    plan = pipeline.plan

    assert _plan_names(plan.computed) == ["attr_3"]
    # names the consumed attr_new_b, not the annotator's first attr_new_a
    assert plan.computed[0].reason == "depends on new attr_new_b"


def test_reannotation_plan_internal_tag() -> None:
    # an added internal attribute must be flagged internal in the plan
    attribute_1 = \
        Attribute("attr_1", "attr_1", internal=False, parameters={})
    dummy_annotator_1 = DummyAnnotator([attribute_1])

    attribute_internal = \
        Attribute("attr_raw", "attr_raw", internal=True, parameters={})
    dummy_annotator_internal = DummyAnnotator([attribute_internal])

    old_pipeline = _make_pipeline_with([dummy_annotator_1])
    new_pipeline = _make_pipeline_with(
        [dummy_annotator_1, dummy_annotator_internal])

    pipeline = ReannotationPipeline(new_pipeline, old_pipeline)
    plan = pipeline.plan

    assert _plan_names(plan.copied) == ["attr_1"]
    added = {entry.name: entry for entry in plan.added}
    assert added["attr_raw"].internal is True


def test_reannotation_plan_full_reannotation() -> None:
    attribute_1 = \
        Attribute("attr_1", "attr_1", internal=False, parameters={})
    dummy_annotator_1 = DummyAnnotator([attribute_1])

    attribute_2 = \
        Attribute("attr_2", "attr_2", internal=False, parameters={})
    dummy_annotator_2 = DummyAnnotator([attribute_2])

    old_pipeline = _make_pipeline_with([dummy_annotator_1, dummy_annotator_2])
    new_pipeline = _make_pipeline_with([dummy_annotator_1, dummy_annotator_2])

    pipeline = ReannotationPipeline(
        new_pipeline, old_pipeline, full_reannotation=True)
    plan = pipeline.plan

    # nothing reused under full reannotation
    assert not plan.copied
    # everything recomputed, reason forced
    assert _plan_names(plan.computed) == ["attr_1", "attr_2"]
    assert all(
        entry.reason == FULL_REANNOTATION_REASON for entry in plan.computed)
    # everything deleted
    assert _plan_names(plan.deleted) == ["attr_1", "attr_2"]
    assert not plan.added


# ---------------------------------------------------------------------------
# Rendering of the plan as human-readable text
# ---------------------------------------------------------------------------


def test_format_plan_renders_all_buckets() -> None:
    attribute_1 = \
        Attribute("attr_1", "attr_1", internal=False, parameters={})
    dummy_annotator_1 = DummyAnnotator([attribute_1])

    attribute_old = \
        Attribute("attr_old", "attr_old", internal=False, parameters={})
    dummy_annotator_old = DummyAnnotator([attribute_old])

    attribute_2 = \
        Attribute("attr_2", "attr_2", internal=False, parameters={})
    dummy_annotator_2 = DummyAnnotator([attribute_2])

    attribute_3 = \
        Attribute("attr_3", "attr_3", internal=False, parameters={})
    dummy_annotator_3 = DummyAnnotator([attribute_3], dependencies=("attr_2",))

    old_pipeline = _make_pipeline_with(
        [dummy_annotator_1, dummy_annotator_old, dummy_annotator_3])
    new_pipeline = _make_pipeline_with(
        [dummy_annotator_1, dummy_annotator_2, dummy_annotator_3])

    pipeline = ReannotationPipeline(new_pipeline, old_pipeline)
    text = pipeline.format_plan(reference="old.yaml")

    assert text.startswith("Reannotation plan (vs old.yaml):")
    # all four buckets with counts always printed
    assert "COPIED   (1): attr_1" in text
    assert "ADDED    (1): attr_2" in text
    assert "COMPUTED (1): attr_3" in text
    assert "DELETED  (1): attr_old" in text
    # computed carries the producing annotator id and the trigger
    computed_line = next(
        line for line in text.splitlines() if "COMPUTED" in line)
    assert "dummy" in computed_line
    assert "attr_2" in computed_line


def test_format_plan_no_reference() -> None:
    attribute_1 = \
        Attribute("attr_1", "attr_1", internal=False, parameters={})
    dummy_annotator_1 = DummyAnnotator([attribute_1])

    attribute_2 = \
        Attribute("attr_2", "attr_2", internal=False, parameters={})
    dummy_annotator_2 = DummyAnnotator([attribute_2])

    old_pipeline = _make_pipeline_with([dummy_annotator_1])
    new_pipeline = _make_pipeline_with([dummy_annotator_1, dummy_annotator_2])

    pipeline = ReannotationPipeline(new_pipeline, old_pipeline)
    text = pipeline.format_plan()
    assert text.startswith("Reannotation plan:")
    assert "vs" not in text.splitlines()[0]


def test_format_plan_internal_tagged() -> None:
    attribute_1 = \
        Attribute("attr_1", "attr_1", internal=False, parameters={})
    dummy_annotator_1 = DummyAnnotator([attribute_1])

    attribute_internal = \
        Attribute("attr_raw", "attr_raw", internal=True, parameters={})
    dummy_annotator_internal = DummyAnnotator([attribute_internal])

    old_pipeline = _make_pipeline_with([dummy_annotator_1])
    new_pipeline = _make_pipeline_with(
        [dummy_annotator_1, dummy_annotator_internal])

    pipeline = ReannotationPipeline(new_pipeline, old_pipeline)
    text = pipeline.format_plan()
    assert "attr_raw (internal)" in text


def test_format_plan_full_reannotation_header() -> None:
    attribute_1 = \
        Attribute("attr_1", "attr_1", internal=False, parameters={})
    dummy_annotator_1 = DummyAnnotator([attribute_1])

    old_pipeline = _make_pipeline_with([dummy_annotator_1])
    new_pipeline = _make_pipeline_with([dummy_annotator_1])

    pipeline = ReannotationPipeline(
        new_pipeline, old_pipeline, full_reannotation=True)
    text = pipeline.format_plan(reference="old.yaml")
    assert text.startswith(
        "Reannotation plan [full reannotation] (vs old.yaml):")
    assert FULL_REANNOTATION_REASON in text
    assert "COPIED   (0):" in text


def test_print_plan_writes_to_file(capsys) -> None:
    attribute_1 = \
        Attribute("attr_1", "attr_1", internal=False, parameters={})
    dummy_annotator_1 = DummyAnnotator([attribute_1])

    attribute_2 = \
        Attribute("attr_2", "attr_2", internal=False, parameters={})
    dummy_annotator_2 = DummyAnnotator([attribute_2])

    old_pipeline = _make_pipeline_with([dummy_annotator_1])
    new_pipeline = _make_pipeline_with([dummy_annotator_1, dummy_annotator_2])

    pipeline = ReannotationPipeline(new_pipeline, old_pipeline)
    pipeline.print_plan(reference="old.yaml")
    captured = capsys.readouterr()
    # default file is stderr
    assert "Reannotation plan (vs old.yaml):" in captured.err
    assert "ADDED    (1): attr_2" in captured.err


def test_format_annotation_plan_plain_pipeline() -> None:
    # a plain (non-reannotation) pipeline: everything is ADDED
    attribute_1 = \
        Attribute("attr_1", "attr_1", internal=False, parameters={})
    dummy_annotator_1 = DummyAnnotator([attribute_1])

    attribute_2 = \
        Attribute("attr_2", "attr_2", internal=True, parameters={})
    dummy_annotator_2 = DummyAnnotator([attribute_2])

    pipeline = _make_pipeline_with([dummy_annotator_1, dummy_annotator_2])
    text = format_annotation_plan(pipeline)

    assert text.startswith("Annotation plan:")
    assert "vs" not in text.splitlines()[0]
    assert "ADDED    (2): attr_1, attr_2 (internal)" in text
