# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pathlib
import textwrap

import pytest
from gain.annotation.annotatable import VCFAllele
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)

from demo_annotator.adapter import DemoAnnotatorAdapter


@pytest.fixture
def config_1() -> str:
    return textwrap.dedent("""
        - external_demo_stream_annotator
    """)


@pytest.fixture
def config_2() -> str:
    return textwrap.dedent("""
        - external_demo_stream_annotator: {}
    """)


@pytest.fixture
def config_3() -> str:
    return textwrap.dedent("""
        - external_demo_stream_annotator:
            attributes:
            - name: annotatable_length
    """)


@pytest.fixture
def config_4() -> str:
    return textwrap.dedent("""
        - external_demo_stream_annotator:
            attributes:
            - name: pesho
              source: annotatable_length
    """)


@pytest.fixture
def config_5() -> str:
    return textwrap.dedent("""
        - external_demo_stream_annotator:
            attributes:
            - name: pesho
              source: annotatable_length
            - name: gosho
              source: annotatable_length
    """)


@pytest.fixture
def annotation_configs(
    config_1: str,
    config_2: str,
    config_3: str,
    config_4: str,
    config_5: str,
) -> dict[str, str]:
    return {
        "config_1": config_1,
        "config_2": config_2,
        "config_3": config_3,
        "config_4": config_4,
        "config_5": config_5,
    }


@pytest.mark.parametrize(
    "config_key",
    [
        ("config_1"),
        ("config_2"),
        ("config_3"),
        ("config_4"),
        ("config_5"),
    ],
)
def test_demo_annotator_initialization(
    annotation_configs: dict[str, str],
    config_key: str,
    tmp_path: pathlib.Path,
) -> None:
    grr = build_genomic_resource_repository()
    pipeline = load_pipeline_from_yaml(
        annotation_configs[config_key], grr,
        work_dir=tmp_path,
        allow_repeated_attributes=True)

    annotators = pipeline.annotators
    assert len(annotators) == 1
    assert isinstance(annotators[0], DemoAnnotatorAdapter)


def test_demo_stream_annotator_batch_annotate_default(
    tmp_path: pathlib.Path,
) -> None:
    grr = build_genomic_resource_repository()
    pipeline = load_pipeline_from_yaml(
        "- external_demo_stream_annotator", grr, work_dir=tmp_path)
    with pipeline.open() as p:
        results = p.batch_annotate([VCFAllele("1", 10, "A", "C")])
    assert "annotatable_length" in results[0]
    assert isinstance(results[0]["annotatable_length"], int)


def test_demo_stream_annotator_batch_annotate_renamed_attribute(
    tmp_path: pathlib.Path,
) -> None:
    grr = build_genomic_resource_repository()
    pipeline = load_pipeline_from_yaml(textwrap.dedent("""
        - external_demo_stream_annotator:
            attributes:
            - name: my_length
              source: annotatable_length
    """), grr, work_dir=tmp_path)
    with pipeline.open() as p:
        results = p.batch_annotate([VCFAllele("1", 10, "A", "C")])
    assert "my_length" in results[0]
    assert "annotatable_length" not in results[0]
    assert isinstance(results[0]["my_length"], int)
