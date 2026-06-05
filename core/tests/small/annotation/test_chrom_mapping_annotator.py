# pylint: disable=W0621,C0114,C0116,W0212,W0613

import textwrap
from pathlib import Path

import pytest
from gain.annotation.annotation_config import AnnotationConfigParser
from gain.genomic_resources.repository import GR_CONF_FILE_NAME
from gain.genomic_resources.testing import (
    build_filesystem_test_repository,
    convert_to_tab_separated,
    setup_directories,
)
from gain.annotation.annotatable import (
    Annotatable,
    CNVAllele,
    Position,
    Region,
    VCFAllele,
)
from gain.annotation.annotation_config import AnnotatorInfo
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.annotation.chrom_mapping_annotator import ChromMappingAnnotator


def test_chromosome_annotator_creation(tmp_path: Path) -> None:
    annotator = ChromMappingAnnotator(
        None,  # type: ignore
        AnnotatorInfo(
            "test",
            [],
            {
                "add_prefix": "chr",
                "work_dir": tmp_path,
            },
        ),
    )
    assert annotator is not None

    annotator = ChromMappingAnnotator(
        None,  # type: ignore
        AnnotatorInfo(
            "test",
            [],
            {
                "del_prefix": "chr",
                "work_dir": tmp_path,
            },
        ),
    )
    assert annotator is not None


@pytest.mark.parametrize("annotatable_type,annotatable", [
    (Position, Position("1", 1)),
    (Region, Region("1", 1, 2)),
    (CNVAllele, CNVAllele("1", 1, 2, Annotatable.Type.LARGE_DELETION)),
    (VCFAllele, VCFAllele("1", 1, "A", "C")),
])
def test_chromosome_annotator_annotation_add_prefix(
    annotatable_type: type, annotatable: Annotatable,
    tmp_path: Path,
) -> None:
    annotator = ChromMappingAnnotator(
        None,  # type: ignore
        AnnotatorInfo(
            "test",
            [],
            {
                "add_prefix": "chr",
                "work_dir": tmp_path,
            },
        ),
    )
    output = annotator.annotate(annotatable, {})

    assert output is not None
    renamed = output["renamed_chromosome"]
    assert renamed is not None
    assert isinstance(renamed, annotatable_type)
    assert isinstance(renamed, Annotatable)
    assert renamed.chrom == "chr1"
    assert renamed.chrom != annotatable.chrom
    assert renamed.pos == annotatable.pos


@pytest.mark.parametrize("annotatable_type,annotatable", [
    (Position, Position("chr1", 1)),
    (Region, Region("chr1", 1, 2)),
    (CNVAllele, CNVAllele("chr1", 1, 2, Annotatable.Type.LARGE_DELETION)),
    (VCFAllele, VCFAllele("chr1", 1, "A", "C")),
])
def test_chromosome_annotator_annotation_del_prefix(
    annotatable_type: type, annotatable: Annotatable,
    tmp_path: Path,
) -> None:
    annotator = ChromMappingAnnotator(
        None,  # type: ignore
        AnnotatorInfo(
            "test",
            [],
            {
                "del_prefix": "chr",
                "work_dir": tmp_path,
            },
        ),
    )
    output = annotator.annotate(annotatable, {})

    assert output is not None
    renamed = output["renamed_chromosome"]
    assert renamed is not None
    assert isinstance(renamed, annotatable_type)
    assert isinstance(renamed, Annotatable)
    assert renamed.chrom == "1"
    assert renamed.chrom != annotatable.chrom
    assert renamed.pos == annotatable.pos


def test_pipeline_initialization(tmp_path: Path) -> None:
    pipeline_config = """
        - chrom_mapping:
            add_prefix: chr
    """

    pipeline = load_pipeline_from_yaml(
        pipeline_config, None, work_dir=tmp_path,  # type: ignore
    )
    assert len(pipeline.annotators) == 1
    annotator = pipeline.annotators[0]
    assert annotator is not None
    assert isinstance(annotator, ChromMappingAnnotator)
    assert annotator.chrom_mapping is not None


def test_chrom_mapping_to_dict_internal_false() -> None:
    _, configs = AnnotationConfigParser.parse_raw([{
        "chrom_mapping": {
            "add_prefix": "chr",
            "attributes": [
                {
                    "source": "renamed_chromosome",
                    "name": "renamed_chromosome",
                    "internal": False,
                },
            ],
        },
    }])
    config = configs[0]
    result = config.to_dict()
    attributes = result["chrom_mapping"]["attributes"]
    assert len(attributes) == 1
    assert attributes[0]["internal"] is False


def test_chrom_mapping_internal_false_annotates(tmp_path: Path) -> None:
    pipeline = load_pipeline_from_yaml(
        textwrap.dedent("""
            - chrom_mapping:
                add_prefix: chr
                attributes:
                - source: renamed_chromosome
                  name: renamed_chromosome
                  internal: false
        """),
        None,  # type: ignore
        work_dir=tmp_path,
    )
    result = pipeline.annotate(VCFAllele("1", 100, "A", "T"), {})
    assert "renamed_chromosome" in result


def test_chrom_mapping_internal_false_with_downstream_annotator(
    tmp_path: Path,
) -> None:
    setup_directories(tmp_path / "grr", {
        "scores/pos1": {
            GR_CONF_FILE_NAME: """
                type: position_score
                table:
                    filename: data.txt
                scores:
                - id: pos1
                  type: float
                  desc: ""
                  name: pos1
            """,
            "data.txt": convert_to_tab_separated("""
                chrom  pos_begin  pos_end  pos1
                chr3   28500584   28500584 0.5
            """),
        },
    })
    repo = build_filesystem_test_repository(tmp_path / "grr")
    pipeline = load_pipeline_from_yaml(
        textwrap.dedent("""
            - chrom_mapping:
                add_prefix: chr
                attributes:
                - source: renamed_chromosome
                  internal: false
            - position_score:
                resource_id: scores/pos1
                input_annotatable: renamed_chromosome
        """),
        repo,
        work_dir=tmp_path,
    )
    result = pipeline.annotate(Region("3", 28500584, 28500584), {})
    assert result["pos1"] == pytest.approx(0.5)
