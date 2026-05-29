# pylint: disable=W0621,C0114,C0116,W0212,W0613

import pathlib
import textwrap

import pytest
from gain.annotation.annotatable import Region
from gain.annotation.annotation_config import AnnotatorInfo, AttributeConfig
from gain.annotation.gene_score_annotator import GeneScoreAnnotator
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GenomicResourceRepo,
)
from gain.genomic_resources.testing import build_inmemory_test_repository

_DUMMY_ANNOTATABLE = Region("chr1", 1, 1)


@pytest.fixture
def scores_repo() -> GenomicResourceRepo:
    return build_inmemory_test_repository({
        "LGD_rank": {
            GR_CONF_FILE_NAME: """
                type: gene_score
                filename: LGD.csv
                scores:
                  - id: LGD_rank
                    desc: LGD rank
                    histogram:
                      type: number
                      number_of_bins: 150
                      x_log_scale: false
                      y_log_scale: false
                """,
            "LGD.csv": textwrap.dedent("""
                "gene","LGD_score","LGD_rank"
                "LRP1",0.000014,1
                "TRRAP",0.00016,3
                "ANKRD11",0.0004,5
                "ZFHX3",0.000925,8
                "HERC2",0.003682,25
                "TRIO",0.001563,11
                "MACF1",0.000442,6
                "PLEC",0.004842,40
                "SRRM2",0.004471,35
                "SPTBN1",0.002715,19.5
                "UBR4",0.007496,59
            """),
        },
        "int_score": {
            GR_CONF_FILE_NAME: """
                type: gene_score
                filename: int.csv
                scores:
                  - id: int_score
                    desc: test integer score
                    type: int
                    histogram:
                      type: number
                      number_of_bins: 6
                      x_log_scale: false
                      y_log_scale: false
                """,
            "int.csv": textwrap.dedent("""
                gene,int_score
                G1,1
                G2,2
                G3,3
                G4,4
                G5,5
                G6,6
            """),
        },
    })


def test_gene_score_annotator(
    scores_repo: GenomicResourceRepo,
    tmp_path: pathlib.Path,
) -> None:
    resource = scores_repo.get_resource("LGD_rank")
    annotator = GeneScoreAnnotator(
        None,
        AnnotatorInfo(
            "gosho",
            [AttributeConfig(
                "LGD_rank",
                "LGD_rank",
                internal=False,
                parameters={})],
            {"work_dir": str(tmp_path)},
        ),
        resource,
        "gene_list",
    )

    result = annotator.annotate(
        _DUMMY_ANNOTATABLE, {"gene_list": ["LRP1", "TRRAP"]})

    assert result == {"LGD_rank": {"LRP1": 1, "TRRAP": 3}}


def test_gene_score_annotator_int_attributes(
    scores_repo: GenomicResourceRepo,
    tmp_path: pathlib.Path,
) -> None:
    resource = scores_repo.get_resource("int_score")
    annotator = GeneScoreAnnotator(
        None,
        AnnotatorInfo(
            "gosho",
            [AttributeConfig(
                "int_score",
                "int_score",
                internal=False,
                parameters={})],
            {"work_dir": str(tmp_path)},
        ),
        resource,
        "gene_list",
    )

    attribute_specs = annotator.get_attribute_specs()

    assert attribute_specs["int_score"].value_type == "object"

    result = annotator.annotate(_DUMMY_ANNOTATABLE, {"gene_list": ["G2"]})

    assert result == {"int_score": {"G2": 2}}


def test_gene_score_annotator_default_aggregator(
    scores_repo: GenomicResourceRepo,
    tmp_path: pathlib.Path,
) -> None:
    resource = scores_repo.get_resource("LGD_rank")
    annotator = GeneScoreAnnotator(
        None, AnnotatorInfo("gosho", [], {"work_dir": str(tmp_path)}),
        resource, "gene_list")

    result = annotator.annotate(
        _DUMMY_ANNOTATABLE, {"gene_list": ["LRP1", "TRRAP"]})

    assert result == {"LGD_rank": {"LRP1": 1, "TRRAP": 3}}


def test_gene_score_annotator_resources(
    scores_repo: GenomicResourceRepo,
    tmp_path: pathlib.Path,
) -> None:
    resource = scores_repo.get_resource("LGD_rank")
    annotator = GeneScoreAnnotator(
        None, AnnotatorInfo("gosho", [], {"work_dir": str(tmp_path)}),
        resource, "gene_list")

    assert annotator.resource_ids == {"LGD_rank"}


def test_gene_score_annotator_used_context_attributes(
    scores_repo: GenomicResourceRepo,
    tmp_path: pathlib.Path,
) -> None:
    resource = scores_repo.get_resource("LGD_rank")
    annotator = GeneScoreAnnotator(
        None,
        AnnotatorInfo(
            "gosho",
            [AttributeConfig(
                "LGD_rank",
                "LGD_rank",
                internal=False,
                parameters={})],
            {"work_dir": str(tmp_path)},
        ),
        resource,
        "gene_list",
    )
    assert annotator.used_context_attributes == ("gene_list",)


@pytest.fixture
def default_annotation_repo() -> GenomicResourceRepo:
    return build_inmemory_test_repository({
        "MultiScore": {
            GR_CONF_FILE_NAME: """
                type: gene_score
                filename: scores.csv
                default_annotation:
                  - source: score1
                  - source: score2
                scores:
                  - id: score1
                    desc: first score
                    histogram:
                      type: number
                      number_of_bins: 3
                      x_log_scale: false
                      y_log_scale: false
                  - id: score2
                    desc: second score
                    histogram:
                      type: number
                      number_of_bins: 3
                      x_log_scale: false
                      y_log_scale: false
                  - id: score3
                    desc: third score (not in default_annotation)
                    histogram:
                      type: number
                      number_of_bins: 3
                      x_log_scale: false
                      y_log_scale: false
                """,
            "scores.csv": textwrap.dedent("""
                gene,score1,score2,score3
                G1,1,10,100
                G2,2,20,200
                G3,3,30,300
            """),
        },
    })


def test_default_annotation_limits_scores(
    default_annotation_repo: GenomicResourceRepo,
    tmp_path: pathlib.Path,
) -> None:
    resource = default_annotation_repo.get_resource("MultiScore")
    annotator = GeneScoreAnnotator(
        None, AnnotatorInfo("gosho", [], {"work_dir": str(tmp_path)}),
        resource, "gene_list",
    )
    assert [a.name for a in annotator.attributes] == ["score1", "score2"]
    assert "score3" not in [a.name for a in annotator.attributes]


def test_default_annotation_custom_aggregator(
    default_annotation_repo: GenomicResourceRepo,
    tmp_path: pathlib.Path,
) -> None:
    resource = default_annotation_repo.get_resource("MultiScore")
    annotator = GeneScoreAnnotator(
        None, AnnotatorInfo("gosho", [], {"work_dir": str(tmp_path)}),
        resource, "gene_list",
    )
    result = annotator.annotate(_DUMMY_ANNOTATABLE, {"gene_list": ["G1", "G2"]})
    assert result["score1"] == {"G1": 1, "G2": 2}
    assert result["score2"] == {"G1": 10, "G2": 20}


def test_default_annotation_non_default_accessible_explicitly(
    default_annotation_repo: GenomicResourceRepo,
    tmp_path: pathlib.Path,
) -> None:
    resource = default_annotation_repo.get_resource("MultiScore")
    annotator = GeneScoreAnnotator(
        None,
        AnnotatorInfo(
            "gosho",
            [AttributeConfig("score3", "score3", internal=False,
                       parameters={})],
            {"work_dir": str(tmp_path)},
        ),
        resource,
        "gene_list",
    )
    result = annotator.annotate(_DUMMY_ANNOTATABLE, {"gene_list": ["G1", "G2"]})
    assert result == {"score3": {"G1": 100, "G2": 200}}


def test_default_annotation_invalid_score_raises(
    default_annotation_repo: GenomicResourceRepo,
) -> None:
    bad_repo = build_inmemory_test_repository({
        "BadScore": {
            GR_CONF_FILE_NAME: """
                type: gene_score
                filename: scores.csv
                default_annotation:
                  - source: nonexistent
                scores:
                  - id: score1
                    desc: only score
                    histogram:
                      type: number
                      number_of_bins: 3
                      x_log_scale: false
                      y_log_scale: false
                """,
            "scores.csv": textwrap.dedent("""
                gene,score1
                G1,1
            """),
        },
    })
    resource = bad_repo.get_resource("BadScore")
    with pytest.raises(ValueError, match="nonexistent"):
        GeneScoreAnnotator(
            None, AnnotatorInfo("gosho", [], {}), resource, "gene_list",
        )


def test_default_annotation_attribute_descriptions(
    default_annotation_repo: GenomicResourceRepo,
    tmp_path: pathlib.Path,
) -> None:
    resource = default_annotation_repo.get_resource("MultiScore")
    annotator = GeneScoreAnnotator(
        None, AnnotatorInfo("gosho", [], {"work_dir": str(tmp_path)}),
        resource, "gene_list",
    )
    specs = annotator.get_attribute_specs()
    assert specs["score1"].is_default is True
    assert specs["score2"].is_default is True
    assert specs["score3"].is_default is False


def test_gene_score_annotator_aggregation_raises(
    scores_repo: GenomicResourceRepo,
    tmp_path: pathlib.Path,
) -> None:
    resource = scores_repo.get_resource("LGD_rank")
    with pytest.raises(ValueError, match="does not support aggregation"):
        GeneScoreAnnotator(
            None,
            AnnotatorInfo(
                "gosho",
                [AttributeConfig(
                    "LGD_rank",
                    "LGD_rank",
                    internal=False,
                    aggregator="max",
                    parameters={})],
                {"work_dir": str(tmp_path)},
            ),
            resource,
            "gene_list",
        )
