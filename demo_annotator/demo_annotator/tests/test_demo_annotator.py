# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pathlib
import textwrap
from typing import Any

import pytest
import pytest_mock
from gain.annotation.annotatable import VCFAllele
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)

from demo_annotator.adapter import DemoAnnotatorAdapter


@pytest.fixture
def config_1() -> str:
    return textwrap.dedent("""
        - external_demo_annotator
    """)


@pytest.fixture
def config_2() -> str:
    return textwrap.dedent("""
        - external_demo_annotator: {}
    """)


@pytest.fixture
def config_3() -> str:
    return textwrap.dedent("""
        - external_demo_annotator:
            attributes:
            - name: annotatable_length
    """)


@pytest.fixture
def config_4() -> str:
    return textwrap.dedent("""
        - external_demo_annotator:
            attributes:
            - name: pesho
              source: annotatable_length
    """)


@pytest.fixture
def config_5() -> str:
    return textwrap.dedent("""
        - external_demo_annotator:
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


def _make_pipeline(config: str, tmp_path: pathlib.Path) -> DemoAnnotatorAdapter:
    grr = build_genomic_resource_repository()
    pipeline = load_pipeline_from_yaml(config, grr, work_dir=tmp_path)
    annotator = pipeline.annotators[0]
    assert isinstance(annotator, DemoAnnotatorAdapter)
    return annotator


def _mock_read_output(
    mocker: pytest_mock.MockerFixture,
    annotator: DemoAnnotatorAdapter,
    value: int,
) -> None:
    def _fill(
            _file: Any, contexts: list[dict[str, Any]]) -> None:
        for ctx in contexts:
            ctx["annotatable_length"] = value
    mocker.patch.object(annotator, "read_output", side_effect=_fill)


def test_demo_annotator_batch_annotate_default(
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockerFixture,
) -> None:
    annotator = _make_pipeline(
        "- external_demo_annotator", tmp_path)
    mocker.patch.object(annotator, "run", return_value=None)
    mocker.patch("demo_annotator.adapter.subprocess.run", return_value=None)
    _mock_read_output(mocker, annotator, 42)
    annotator.work_dir.mkdir(parents=True, exist_ok=True)
    (annotator.work_dir / "output.tsv").write_text("")

    results = annotator.batch_annotate([VCFAllele("1", 10, "A", "C")], [{}])
    assert results[0]["annotatable_length"] == 42


def test_demo_annotator_batch_annotate_renamed_attribute(
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockerFixture,
) -> None:
    annotator = _make_pipeline(textwrap.dedent("""
        - external_demo_annotator:
            attributes:
            - name: my_length
              source: annotatable_length
    """), tmp_path)
    mocker.patch.object(annotator, "run", return_value=None)
    mocker.patch("demo_annotator.adapter.subprocess.run", return_value=None)
    _mock_read_output(mocker, annotator, 42)
    annotator.work_dir.mkdir(parents=True, exist_ok=True)
    (annotator.work_dir / "output.tsv").write_text("")

    results = annotator.batch_annotate([VCFAllele("1", 10, "A", "C")], [{}])
    assert "my_length" in results[0]
    assert "annotatable_length" not in results[0]
    assert results[0]["my_length"] == 42
