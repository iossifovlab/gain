# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pathlib

import pytest
from gain.annotation.annotation_factory import (
    load_pipeline_from_file,
    load_pipeline_from_file_or_resource,
)
from gain.genomic_resources.repository import GenomicResourceRepo


@pytest.mark.parametrize(
        "pipeline_ext", [".yaml", ".yml"],
)
def test_load_pipeline_from_file(
    annotate_directory_fixture: pathlib.Path,
    annotation_grr: GenomicResourceRepo,
    tmp_path: pathlib.Path,
    pipeline_ext: str,
) -> None:
    pipeline_filename = tmp_path / f"pipeline{pipeline_ext}"
    pipeline_filename.write_text(
        """
        - position_score:
            resource_id: one
        """)
    pipeline = load_pipeline_from_file(
        str(pipeline_filename), annotation_grr)
    assert len(pipeline.annotators) == 1


def test_load_pipeline_from_file_or_resource_file_branch(
    annotation_grr: GenomicResourceRepo,
    tmp_path: pathlib.Path,
) -> None:
    pipeline_filename = tmp_path / "pipeline.yaml"
    pipeline_filename.write_text(
        """
        - position_score:
            resource_id: one
        """)
    pipeline = load_pipeline_from_file_or_resource(
        str(pipeline_filename), annotation_grr)
    assert len(pipeline.annotators) == 1


def test_load_pipeline_from_file_or_resource_grr_id_branch(
    annotation_grr: GenomicResourceRepo,
) -> None:
    pipeline = load_pipeline_from_file_or_resource(
        "res_pipeline", annotation_grr)
    assert len(pipeline.annotators) == 1


def test_load_pipeline_from_file_or_resource_missing(
    annotation_grr: GenomicResourceRepo,
) -> None:
    with pytest.raises(ValueError, match="neither a valid file path"):
        load_pipeline_from_file_or_resource(
            "definitely_not_a_path_or_resource", annotation_grr)


def test_load_pipeline_from_file_or_resource_wrong_type(
    annotation_grr: GenomicResourceRepo,
) -> None:
    with pytest.raises(TypeError, match="annotation_pipeline"):
        load_pipeline_from_file_or_resource("one", annotation_grr)
