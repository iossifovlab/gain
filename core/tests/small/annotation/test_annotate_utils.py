# pylint: disable=W0621,C0114,C0116,W0212,W0613
import os
import pathlib
from unittest.mock import MagicMock

import pytest
import pytest_mock
from gain.annotation.annotate_utils import (
    cache_pipeline_resources,
    handle_default_args,
)


@pytest.mark.parametrize(
    "input_path,output_path,expected_output,expected_work_dir",
    [
        ("input.vcf", None, "input_annotated.vcf", "input_annotated_work"),
        ("/mnt/data/Tools/data/QUAD/UR1.annot.filtered.txt.gz",
         None,
         "UR1.annot.filtered_annotated.txt",
         "UR1.annot.filtered_annotated_work"),
        ("input.vcf", "output.vcf", "output.vcf", "output_work"),
        ("input_data/input.vcf", None,
         "input_annotated.vcf", "input_annotated_work"),
        ("input_data/input.vcf", "output.vcf",
         "output.vcf", "output_work"),
    ],
)
def test_handle_default_args_work_dir(
    mocker: pytest_mock.MockerFixture,
    input_path: str,
    output_path: str | None,
    expected_output: str | None, expected_work_dir: str | None,
) -> None:
    mocker.patch("os.path.exists", return_value=True)
    args = {
        "input": input_path,
        "output": output_path,
    }
    result = handle_default_args(args)
    assert result["output"] == os.path.abspath(expected_output)
    assert result["work_dir"] == os.path.abspath(expected_work_dir)


def test_handle_default_args_absolutizes_extended_paths(
    mocker: pytest_mock.MockerFixture,
) -> None:
    mocker.patch("os.path.exists", return_value=True)
    relative_paths = {
        "grr_filename": "grr.yaml",
        "grr_directory": "my_grr",
        "reannotate": "old_pipeline.yaml",
        "dask_cluster_config_file": "cluster.yaml",
    }
    expected = {
        key: os.path.abspath(value)
        for key, value in relative_paths.items()
    }
    args = {"input": "in.txt", "output": "out.txt", **relative_paths}
    result = handle_default_args(args)
    for key, expected_path in expected.items():
        assert result[key] == expected_path, key
        assert os.path.isabs(result[key]), key


def test_handle_default_args_leaves_absent_extended_paths(
    mocker: pytest_mock.MockerFixture,
) -> None:
    mocker.patch("os.path.exists", return_value=True)
    args = {
        "input": "in.txt",
        "output": "out.txt",
        "grr_filename": None,
        "reannotate": None,
    }
    result = handle_default_args(args)
    assert result["grr_filename"] is None
    assert result["reannotate"] is None


@pytest.mark.parametrize("sentinel", ["context", "gpf_instance"])
def test_handle_default_args_leaves_pipeline_sentinel(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    sentinel: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    in_file = tmp_path / "in.txt"
    in_file.write_text("x")
    args = {
        "input": str(in_file),
        "output": str(tmp_path / "out.txt"),
        "pipeline": sentinel,
    }
    result = handle_default_args(args)
    assert result["pipeline"] == sentinel


def test_handle_default_args_absolutizes_pipeline_file(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    in_file = tmp_path / "in.txt"
    in_file.write_text("x")
    pipeline_file = tmp_path / "pipeline.yaml"
    pipeline_file.write_text("- debug")
    args = {
        "input": str(in_file),
        "output": str(tmp_path / "out.txt"),
        "pipeline": "pipeline.yaml",  # relative on purpose
    }
    result = handle_default_args(args)
    assert result["pipeline"] == str(pipeline_file)
    assert os.path.isabs(result["pipeline"])


def test_cache_pipeline_resources_forwards_workers(
    mocker: pytest_mock.MockerFixture,
) -> None:
    mocked_cache = mocker.patch(
        "gain.annotation.annotate_utils.cache_resources")
    pipeline = MagicMock()
    pipeline.annotators = []
    grr = MagicMock()

    cache_pipeline_resources(grr, pipeline, workers=7)

    mocked_cache.assert_called_once_with(grr, set(), workers=7)
