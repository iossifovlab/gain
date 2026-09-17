# pylint: disable=W0621,C0116
"""The per-score help a gene score carries into the gene-scores database.

The help embeds the score's histogram image by its public-mirror address,
and an annulled histogram has none -- see
``ScoreResource.get_histogram_config`` for what decides that -- so the
help carries no image at all rather than a dangling one (gain#1025).
"""
import pathlib

import pytest
from gain.gene_scores.gene_scores import (
    GeneScore,
    GeneScoresDb,
    build_gene_score_from_resource,
)
from gain.genomic_resources.testing.builders import a_gene_score


@pytest.fixture
def gene_score(tmp_path: pathlib.Path) -> GeneScore:
    """One gene score resource: ``pli`` has a histogram, ``nullified`` none."""
    res = (
        a_gene_score()
        .with_score("pli", "float")
        .with_score("nullified", "float")
        .with_histogram({"type": "null", "reason": "annulled"})
        .with_data("""
            gene   pli   nullified
            G1     1.0   1.0
        """)
        .build_resource(tmp_path / "gene_score")
    )
    return build_gene_score_from_resource(res)


def gene_score_help(gene_score: GeneScore, score_id: str) -> str:
    descs = {
        desc.score_id: desc
        for desc in GeneScoresDb.build_descs_from_score(gene_score)
    }
    return descs[score_id].help


def test_a_gene_score_help_embeds_the_histogram_by_its_public_address(
    gene_score: GeneScore,
) -> None:
    assert "![HISTOGRAM](" in gene_score_help(gene_score, "pli")


def test_an_annulled_gene_score_histogram_has_no_image_address(
    gene_score: GeneScore,
) -> None:
    assert gene_score.get_histogram_image_public_url("nullified") is None


def test_an_annulled_gene_score_histogram_puts_no_image_in_the_help(
    gene_score: GeneScore,
) -> None:
    assert "![HISTOGRAM]" not in gene_score_help(gene_score, "nullified")
