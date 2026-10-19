"""The histogram accessors read the only score when no id is given (#1978).

``get_histogram_config``, ``get_histogram_image_url``,
``get_histogram_image_public_url`` and ``get_score_range`` with the score
id omitted answer for the only score of the resource, as the calls that
name that id do.  A resource with several scores has no only score, and
each call is refused with the message ``get_score_histogram()`` raises.
"""
from __future__ import annotations

import pathlib
from collections.abc import Callable

import pytest
from gain.gene_scores.gene_scores import build_gene_score_from_resource
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.genomic_scores import PositionScore
from gain.genomic_resources.score_resource import ScoreResource
from gain.genomic_resources.testing import build_filesystem_test_repository
from gain.genomic_resources.testing.builders import (
    a_gene_score,
    a_grr,
    a_position_score,
)
from gain.genomic_resources.testing.statistics import publish_statistics


@pytest.fixture
def one_score_position_score(tmp_path: pathlib.Path) -> PositionScore:
    a_grr().with_resource("pos", (
        a_position_score()
        .with_score("s", "float")
        .with_data("""
            chrom  pos_begin  pos_end  s
            1      10         10       0.5
            1      11         11       1.5
        """)
    )).build_repo(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])
    repo = build_filesystem_test_repository(tmp_path)
    return PositionScore(repo.get_resource("pos"))


def test_a_one_score_position_score_reads_its_only_histogram_config(
    one_score_position_score: PositionScore,
) -> None:
    score = one_score_position_score

    config = score.get_histogram_config()

    assert config == score.get_histogram_config("s")


def test_a_one_score_position_score_reads_its_only_score_range(
    one_score_position_score: PositionScore,
) -> None:
    score = one_score_position_score

    score_range = score.get_score_range()

    assert score_range == score.get_score_range("s")
    assert score_range is not None


def test_a_one_score_position_score_reads_its_only_histogram_image_url(
    one_score_position_score: PositionScore,
) -> None:
    score = one_score_position_score

    url = score.get_histogram_image_url()

    assert url == score.get_histogram_image_url("s")
    assert url is not None


def test_a_one_score_position_score_reads_its_only_public_image_url(
    one_score_position_score: PositionScore,
) -> None:
    score = one_score_position_score

    url = score.get_histogram_image_public_url()

    assert url == score.get_histogram_image_public_url("s")
    assert url is not None


def test_a_one_score_gene_score_reads_its_only_histogram_config_and_range(
    tmp_path: pathlib.Path,
) -> None:
    resource = (
        a_gene_score()
        .with_score("pli", "float")
        .with_data("""
            gene  pli
            G1    0.1
            G2    0.9
        """)
        .build_resource(tmp_path)
    )
    publish_statistics(resource)
    score = build_gene_score_from_resource(resource)

    config = score.get_histogram_config()
    score_range = score.get_score_range()

    assert config == score.get_histogram_config("pli")
    assert score_range == score.get_score_range("pli")
    assert score_range is not None


@pytest.mark.parametrize("accessor", [
    ScoreResource.get_histogram_config,
    ScoreResource.get_histogram_image_url,
    ScoreResource.get_histogram_image_public_url,
    ScoreResource.get_score_range,
])
def test_a_two_score_position_score_refuses_an_accessor_without_an_id(
    tmp_path: pathlib.Path,
    accessor: Callable[[ScoreResource], object],
) -> None:
    repo = a_grr().with_resource("pos", (
        a_position_score()
        .with_score("t", "float")
        .with_score("s", "float")
        .with_data("""
            chrom  pos_begin  pos_end  t    s
            1      10         10       0.5  1.5
        """)
    )).build_repo(tmp_path)
    score = PositionScore(repo.get_resource("pos"))
    with pytest.raises(ValueError) as histogram_refusal:
        score.get_score_histogram()

    with pytest.raises(ValueError) as excinfo:
        accessor(score)

    assert str(excinfo.value) == str(histogram_refusal.value)
    assert str(excinfo.value) == (
        "resource 'pos' has no only score to read without a score id; "
        "it has ['s', 't']")
