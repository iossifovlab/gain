# pylint: disable=W0621,C0114,C0116,W0212,W0613
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
    assert result["output"] == expected_output
    assert result["work_dir"] == expected_work_dir


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
