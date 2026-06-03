# pylint: disable=W0621,C0114,C0116,W0212,W0613
import argparse
import os
import pathlib
from unittest.mock import MagicMock

import pytest
import pytest_mock
from gain.annotation.annotate_utils import (
    add_common_annotation_arguments,
    cache_pipeline_resources,
    check_resource_locality,
    find_nonlocal_resources,
    handle_default_args,
)
from gain.genomic_resources.cached_repository import CachingProtocol


def _resource(
    resource_id: str, scheme: str | None = None, *, caching: bool = False,
) -> MagicMock:
    res = MagicMock()
    res.resource_id = resource_id
    if caching:
        res.proto = MagicMock(spec=CachingProtocol)
    else:
        proto = MagicMock()
        proto.scheme = scheme
        res.proto = proto
    return res


def _pipeline(*resources: MagicMock) -> MagicMock:
    annotator = MagicMock()
    annotator.resources = list(resources)
    pipeline = MagicMock()
    pipeline.annotators = [annotator]
    return pipeline


def test_find_nonlocal_resources_all_local() -> None:
    pipeline = _pipeline(
        _resource("res_file", "file"),
        _resource("res_mem", "memory"),
        _resource("res_cached", caching=True),
    )
    assert find_nonlocal_resources(pipeline) == []


def test_find_nonlocal_resources_reports_remote_schemes() -> None:
    pipeline = _pipeline(
        _resource("res_file", "file"),
        _resource("res_http", "http"),
        _resource("res_s3", "s3"),
        _resource("res_https", "https"),
    )
    assert find_nonlocal_resources(pipeline) == [
        ("res_http", "http"),
        ("res_s3", "s3"),
        ("res_https", "https"),
    ]


def test_find_nonlocal_resources_dedups_shared_resource() -> None:
    shared = _resource("res_http", "http")
    annotator_a = MagicMock()
    annotator_a.resources = [shared]
    annotator_b = MagicMock()
    annotator_b.resources = [shared]
    pipeline = MagicMock()
    pipeline.annotators = [annotator_a, annotator_b]

    assert find_nonlocal_resources(pipeline) == [("res_http", "http")]


def test_check_resource_locality_allow_remote_skips_everything() -> None:
    pipeline = _pipeline(_resource("res_http", "http"))
    count_rows = MagicMock()

    check_resource_locality(pipeline, count_rows, allow_remote=True)

    count_rows.assert_not_called()


def test_check_resource_locality_all_local_skips_count() -> None:
    pipeline = _pipeline(_resource("res_file", "file"))
    count_rows = MagicMock()

    check_resource_locality(pipeline, count_rows)

    count_rows.assert_not_called()


def test_check_resource_locality_below_warning_is_silent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pipeline = _pipeline(_resource("res_http", "http"))

    with caplog.at_level("WARNING"):
        check_resource_locality(pipeline, lambda _limit: 1000)

    assert caplog.records == []


def test_check_resource_locality_warning_band_warns_and_proceeds(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pipeline = _pipeline(
        _resource("res_http", "http"),
        _resource("res_s3", "s3"),
    )

    with caplog.at_level("WARNING"):
        check_resource_locality(pipeline, lambda _limit: 1001)

    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert "res_http (http)" in message
    assert "res_s3 (s3)" in message


def test_check_resource_locality_error_band_raises_and_lists_resources(
) -> None:
    pipeline = _pipeline(
        _resource("res_http", "http"),
        _resource("res_s3", "s3"),
    )

    with pytest.raises(ValueError, match="non-local genomic resources") as exc:
        check_resource_locality(pipeline, lambda limit: limit)

    assert "res_http (http)" in str(exc.value)
    assert "res_s3 (s3)" in str(exc.value)
    assert "--allow-remote-resources" in str(exc.value)


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["input.txt"], False),
        (["input.txt", "--allow-remote-resources"], True),
    ],
)
def test_allow_remote_resources_flag(
    argv: list[str], *, expected: bool,
) -> None:
    parser = argparse.ArgumentParser()
    add_common_annotation_arguments(parser)
    args = vars(parser.parse_args(argv))
    assert args["allow_remote_resources"] is expected


@pytest.mark.parametrize(
    "input_path,output_path,expected_output,expected_work_dir",
    [
        ("input.vcf", None, "input.annotated.vcf", "input.annotated_work"),
        # no input extension: the appended '.annotated' is the only suffix, so
        # work-dir derivation (with_suffix("")) strips it back off -> input_work
        ("input", None, "input.annotated", "input_work"),
        ("/mnt/data/Tools/data/QUAD/UR1.annot.filtered.txt.gz",
         None,
         "UR1.annot.filtered.annotated.txt.gz",
         "UR1.annot.filtered.annotated_work"),
        ("/mnt/data/Tools/data/QUAD/UR1.annot.filtered.txt.bgz",
         None,
         "UR1.annot.filtered.annotated.txt.bgz",
         "UR1.annot.filtered.annotated_work"),
        ("input.vcf", "output.vcf", "output.vcf", "output_work"),
        ("input_data/input.vcf", None,
         "input.annotated.vcf", "input.annotated_work"),
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


def test_handle_default_args_explicit_work_dir_is_honored(
    mocker: pytest_mock.MockerFixture,
) -> None:
    # an explicit work_dir is used verbatim; it is not derived from the
    # (now '.annotated') output name
    mocker.patch("os.path.exists", return_value=True)
    args = {
        "input": "input.vcf",
        "output": None,
        "work_dir": "my_custom_work",
    }
    result = handle_default_args(args)
    assert result["output"] == os.path.abspath("input.annotated.vcf")
    assert result["work_dir"] == os.path.abspath("my_custom_work")


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

    mocked_cache.assert_called_once_with(
        grr, set(), workers=7, progress=True)
