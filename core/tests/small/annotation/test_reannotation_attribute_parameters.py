# pylint: disable=redefined-outer-name,C0114,C0116
"""Attribute parameters count as a change for reannotation (#1155).

``ReannotationPipeline`` decides what to recompute by comparing the new
pipeline's annotator infos with the previously applied ones.  An attribute
parameter -- ``value_transform``, ``none_value_replacement`` -- changes what
the annotator emits, so a pipeline that differs only in one must rerun that
annotator.  Pinned at the seam a user drives: two pipelines loaded from
YAML, diffed by ``ReannotationPipeline``.
"""

import pathlib
import textwrap

import pytest
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.annotation.annotation_pipeline import (
    AnnotationPipeline,
    ReannotationPipeline,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing.builders import (
    a_grr,
    a_position_score,
)


@pytest.fixture
def repo(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    return (
        a_grr()
        .with_resource(
            "scores", a_position_score().with_score("score", "float"))
        .build_repo(tmp_path)
    )


def _pipeline_with_attribute(
    repo: GenomicResourceRepo, attribute_extra: str,
) -> AnnotationPipeline:
    """A one-annotator pipeline whose only attribute carries ``extra``."""
    return load_pipeline_from_yaml(
        textwrap.dedent(f"""
            - position_score:
                resource_id: scores
                attributes:
                  - name: s
                    source: score
                    {attribute_extra}
        """),
        repo,
    )


def _reannotation(
    repo: GenomicResourceRepo, previous_extra: str, new_extra: str,
) -> ReannotationPipeline:
    return ReannotationPipeline(
        _pipeline_with_attribute(repo, new_extra),
        _pipeline_with_attribute(repo, previous_extra),
    )


@pytest.mark.parametrize(("previous_extra", "new_extra"), [
    ("", 'value_transform: "value * 2"'),
    ("none_value_replacement: 0.0", "none_value_replacement: -1.0"),
])
def test_changed_attribute_parameter_reruns_the_annotator(
    repo: GenomicResourceRepo, previous_extra: str, new_extra: str,
) -> None:
    reannotation = _reannotation(repo, previous_extra, new_extra)

    assert [a.get_info().annotator_id for a in reannotation.annotators] \
        == ["A0"]
    assert [entry.name for entry in reannotation.plan.added] == ["s"]
    assert reannotation.plan.copied == []


def test_identical_attribute_parameters_rerun_nothing(
    repo: GenomicResourceRepo,
) -> None:
    reannotation = _reannotation(
        repo,
        'value_transform: "value * 2"',
        'value_transform: "value * 2"',
    )

    assert reannotation.annotators == []
    assert [entry.name for entry in reannotation.plan.copied] == ["s"]
