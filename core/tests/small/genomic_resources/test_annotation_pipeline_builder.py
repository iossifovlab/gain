# pylint: disable=W0621,C0114,C0116
import pathlib
from typing import Any

import pytest
from gain.annotation.annotatable import Position
from gain.annotation.annotation_config import AnnotationConfigurationError
from gain.annotation.annotation_factory import (
    load_pipeline_from_file_or_resource,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing.annotation_pipeline_builder import (
    ResourceValidationError,
    an_annotation_pipeline,
)
from gain.genomic_resources.testing.builders import (
    PositionScoreBuilder,
    a_grr,
    a_position_score,
)


def a_phastcons_score() -> PositionScoreBuilder:
    return (
        a_position_score()
        .with_score("phastCons", "float")
        .with_score_line(chrom="chr1", pos_begin="10", phastCons="0.25")
        .with_score_line(chrom="chr1", pos_begin="11", phastCons="0.75")
    )


def annotate_position(
    grr: GenomicResourceRepo, pipeline_id: str, tmp_path: pathlib.Path,
    chrom: str, pos: int,
) -> dict[str, Any]:
    pipeline = load_pipeline_from_file_or_resource(
        pipeline_id, grr, work_dir=tmp_path / "work")
    work_pipeline = pipeline.open()
    try:
        return work_pipeline.annotate(Position(chrom, pos))
    finally:
        work_pipeline.close()


def test_a_pipeline_annotates_with_the_score_it_names_by_id(
    tmp_path: pathlib.Path,
) -> None:
    grr = (
        a_grr()
        .with_resource("scores/s1", a_phastcons_score())
        .with_resource(
            "pipe",
            an_annotation_pipeline().with_annotator(
                "position_score", "scores/s1"))
        .build_repo(tmp_path / "grr")
    )

    result = annotate_position(grr, "pipe", tmp_path, "chr1", 11)

    assert result == {"phastCons": 0.75}


def test_a_bare_builder_builds_an_empty_pipeline_resource(
    tmp_path: pathlib.Path,
) -> None:
    resource = an_annotation_pipeline().build_resource(tmp_path / "res")
    grr = a_grr().with_resource("pipe", an_annotation_pipeline()) \
        .build_repo(tmp_path / "grr")

    result = annotate_position(grr, "pipe", tmp_path, "chr1", 11)

    assert resource.get_type() == "annotation_pipeline"
    assert result == {}


def test_annotator_params_reach_the_step_alongside_its_resource_id(
    tmp_path: pathlib.Path,
) -> None:
    grr = (
        a_grr()
        .with_resource("scores/s1", a_phastcons_score())
        .with_resource(
            "pipe",
            an_annotation_pipeline().with_annotator(
                "position_score", "scores/s1",
                attributes=[{"source": "phastCons", "name": "conservation"}]))
        .build_repo(tmp_path / "grr")
    )

    result = annotate_position(grr, "pipe", tmp_path, "chr1", 10)

    assert result == {"conservation": 0.25}


def test_an_annotator_without_a_resource_id_is_authorable(
    tmp_path: pathlib.Path,
) -> None:
    grr = (
        a_grr()
        .with_resource(
            "pipe", an_annotation_pipeline().with_annotator("debug_annotator"))
        .build_repo(tmp_path / "grr")
    )

    result = annotate_position(grr, "pipe", tmp_path, "chr1", 4)

    assert result == {"hi": "hello world"}


def test_steps_annotate_in_call_order_and_accumulate(
    tmp_path: pathlib.Path,
) -> None:
    grr = (
        a_grr()
        .with_resource("scores/s1", a_phastcons_score())
        .with_resource(
            "pipe",
            an_annotation_pipeline()
            .with_annotator("debug_annotator")
            .with_annotator("position_score", "scores/s1"))
        .build_repo(tmp_path / "grr")
    )

    result = annotate_position(grr, "pipe", tmp_path, "chr1", 10)

    assert result == {"hi": "hello world", "phastCons": 0.25}


def test_with_annotator_leaves_the_receiver_untouched(
    tmp_path: pathlib.Path,
) -> None:
    base = an_annotation_pipeline().with_annotator("debug_annotator")
    attributes = [{"source": "hi", "name": "greeting"}]
    extended = base.with_annotator("debug_annotator", attributes=attributes)
    attributes[0]["name"] = "mutated_after_the_call"
    grr = (
        a_grr()
        .with_resource("base", base)
        .with_resource("extended", extended)
        .build_repo(tmp_path / "grr")
    )

    base_result = annotate_position(grr, "base", tmp_path, "chr1", 4)
    extended_result = annotate_position(grr, "extended", tmp_path, "chr1", 4)

    assert base_result == {"hi": "hello world"}
    assert extended_result == {"hi": "hello world", "greeting": "hello world"}


def test_a_dangling_resource_id_builds_and_fails_only_on_load(
    tmp_path: pathlib.Path,
) -> None:
    grr = (
        a_grr()
        .with_resource(
            "pipe",
            an_annotation_pipeline().with_annotator(
                "position_score", "scores/missing"))
        .build_repo(tmp_path / "grr")
    )

    with pytest.raises(AnnotationConfigurationError, match="scores/missing"):
        load_pipeline_from_file_or_resource(
            "pipe", grr, work_dir=tmp_path / "work")


#: A preamble-form pipeline, which ``with_annotator`` cannot express.
PREAMBLE_PIPELINE = """\
preamble:
  summary: raw pipeline
annotators:
- position_score: scores/s1
"""


def test_a_raw_pipeline_is_written_verbatim(
    tmp_path: pathlib.Path,
) -> None:
    grr = (
        a_grr()
        .with_resource("scores/s1", a_phastcons_score())
        .with_resource(
            "pipe", an_annotation_pipeline().with_pipeline(PREAMBLE_PIPELINE))
        .build_repo(tmp_path / "grr")
    )

    result = annotate_position(grr, "pipe", tmp_path, "chr1", 11)
    pipeline = load_pipeline_from_file_or_resource(
        "pipe", grr, work_dir=tmp_path / "work2")

    assert result == {"phastCons": 0.75}
    assert pipeline.preamble is not None
    assert pipeline.preamble.summary == "raw pipeline"


def test_a_raw_pipeline_refuses_to_replace_authored_steps() -> None:
    with_steps = an_annotation_pipeline().with_annotator("debug_annotator")

    with pytest.raises(ResourceValidationError, match="with_pipeline"):
        with_steps.with_pipeline(PREAMBLE_PIPELINE)


def test_steps_refuse_to_join_a_raw_pipeline() -> None:
    raw = an_annotation_pipeline().with_pipeline(PREAMBLE_PIPELINE)

    with pytest.raises(ResourceValidationError, match="with_pipeline"):
        raw.with_annotator("debug_annotator")


def test_meta_reaches_the_pipeline_resource(tmp_path: pathlib.Path) -> None:
    resource = an_annotation_pipeline() \
        .with_meta(summary="annotates conservation") \
        .with_labels(domain="conservation") \
        .build_resource(tmp_path)

    assert resource.get_summary() == "annotates conservation"
    assert resource.get_labels() == {"domain": "conservation"}
