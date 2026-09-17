# pylint: disable=W0621,C0116
"""The per-attribute help a score annotator renders for its attributes.

The help embeds the score's histogram image by its public-mirror address,
and an annulled histogram has none -- see
``ScoreResource.get_histogram_config`` for what decides that -- so the
help carries no image at all rather than a dangling one (gain#1025).
"""
import pathlib

import pytest
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.annotation.genomic_score_annotator_base import (
    GenomicScoreAnnotatorBase,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing.builders import a_grr, a_position_score

PUBLIC_URL = "http://grr.example.org"


@pytest.fixture
def repo(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    """One position score: ``score`` has a histogram, ``nullified`` none."""
    return (
        a_grr()
        .with_resource(
            "scores/pos1",
            a_position_score()
            .with_score("score", "float")
            .with_score("nullified", "float")
            .with_histogram({"type": "null", "reason": "annulled"})
            .with_score_line(chrom="1", pos_begin=10, score=0.1,
                             nullified=0.2))
        .with_public_url(PUBLIC_URL)
        .build_repo(tmp_path / "grr")
    )


def genomic_score_help(
    repo: GenomicResourceRepo, tmp_path: pathlib.Path, source: str,
) -> str:
    pipeline = load_pipeline_from_yaml(
        f"- position_score:\n    resource_id: scores/pos1\n"
        f"    attributes:\n    - {source}\n",
        repo, work_dir=tmp_path / "work")
    annotator = pipeline.annotators[0]
    assert isinstance(annotator, GenomicScoreAnnotatorBase)
    return annotator.build_attribute_help(annotator.attributes[0])


def test_the_help_embeds_the_histogram_by_its_public_address(
    repo: GenomicResourceRepo, tmp_path: pathlib.Path,
) -> None:
    help_text = genomic_score_help(repo, tmp_path, "score")

    assert (
        f"![HISTOGRAM]({PUBLIC_URL}/scores/pos1/statistics/"
        f"histogram_score.png)"
    ) in help_text


def test_an_annulled_histogram_puts_no_image_in_the_help(
    repo: GenomicResourceRepo, tmp_path: pathlib.Path,
) -> None:
    help_text = genomic_score_help(repo, tmp_path, "nullified")

    assert "![HISTOGRAM]" not in help_text
    assert "None" not in help_text
    # The rest of the help is still there.
    assert "scores/pos1" in help_text
