# pylint: disable=C0116
"""Whether a score's histogram image has an address at all.

A score whose histogram is annulled *by definition* -- ``histogram:
{type: null}``, or no block and a value type with no default histogram --
never gets a PNG written for it, so an address for one points at a file
nobody writes (gain#1025).  The oracle is the score DEFINITION, never the
loaded statistics: a page addressed on the GRR's public mirror is rendered
from checkouts whose statistics were never built, and reading them would
conflate "annulled" with "not built here".
"""
import pathlib

from gain.genomic_resources.genomic_scores import build_score_from_resource
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing.builders import (
    PositionScoreBuilder,
    a_grr,
    a_position_score,
)

PUBLIC_URL = "http://grr.example.org"


def a_repo_with(
    score: PositionScoreBuilder, tmp_path: pathlib.Path,
) -> GenomicResourceRepo:
    return (
        a_grr()
        .with_resource("scores/pos1", score)
        .with_public_url(PUBLIC_URL)
        .build_repo(tmp_path / "grr")
    )


def test_an_annulled_histogram_has_no_image_address(
    tmp_path: pathlib.Path,
) -> None:
    repo = a_repo_with(
        a_position_score()
        .with_score("score", "float")
        .with_histogram({"type": "null", "reason": "annulled by design"}),
        tmp_path)
    score = build_score_from_resource(repo.get_resource("scores/pos1"))

    assert score.get_histogram_image_url("score") is None
    assert score.get_histogram_image_public_url("score") is None


def test_a_type_with_no_default_histogram_is_annulled_too(
    tmp_path: pathlib.Path,
) -> None:
    # No ``histogram:`` block at all: the definition falls back to the
    # value type's default, and ``bool`` has none.
    repo = a_repo_with(
        a_position_score()
        .with_score("flag", "bool")
        .with_score_line(chrom="1", pos_begin=10, flag=True),
        tmp_path)
    score = build_score_from_resource(repo.get_resource("scores/pos1"))

    assert score.get_histogram_image_url("flag") is None
    assert score.get_histogram_image_public_url("flag") is None


def test_a_numeric_score_is_addressed_before_its_statistics_are_built(
    tmp_path: pathlib.Path,
) -> None:
    # The oracle is the definition: nothing under statistics/ exists yet,
    # and the address must still name the image the build WILL write --
    # a public-mirror page is rendered from exactly such a checkout.
    repo = a_repo_with(a_position_score(), tmp_path)
    score = build_score_from_resource(repo.get_resource("scores/pos1"))

    assert score.get_histogram_image_public_url("score") == \
        f"{PUBLIC_URL}/scores/pos1/statistics/histogram_score.png"
