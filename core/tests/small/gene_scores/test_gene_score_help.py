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
from gain.genomic_resources.histogram import NullHistogram
from gain.genomic_resources.testing.builders import a_gene_score
from gain.genomic_resources.testing.statistics import (
    a_gene_score_with_too_many_categories,
    publish_statistics,
)


@pytest.fixture
def gene_score(tmp_path: pathlib.Path) -> GeneScore:
    """One gene score resource: ``pli`` has a histogram (and, as a built
    resource would, its image), ``nullified`` none."""
    res = (
        a_gene_score()
        .with_score("pli", "float")
        .with_score("nullified", "float")
        .with_histogram({"type": "null", "reason": "annulled"})
        .with_data("""
            gene   pli   nullified
            G1     1.0   1.0
        """)
        .with_file("statistics/histogram_pli.png", "drawn")
        .build_resource(tmp_path / "gene_score")
    )
    return build_gene_score_from_resource(res)


@pytest.fixture
def built_gene_score(tmp_path: pathlib.Path) -> GeneScore:
    """A gene score after a REAL statistics build.

    ``pli`` gets its histogram drawn.  ``label`` declares no histogram, so
    it gets the default categorical one, which enforces the unique-values
    limit -- and its values exceed it, so the build nullifies it while
    accumulating and draws nothing (gain#1533).
    """
    res = a_gene_score_with_too_many_categories().build_resource(
        tmp_path / "gene_score")
    publish_statistics(res)
    gene_score = build_gene_score_from_resource(res)
    # The premise, not the subject: the build did nullify it, for the
    # unique-values reason, and drew no image for it.
    label_hist = gene_score.get_score_histogram("label")
    assert isinstance(label_hist, NullHistogram)
    assert "Too many unique values" in label_hist.reason
    assert not res.file_exists("statistics/histogram_label.png")
    assert res.file_exists("statistics/histogram_pli.png")
    return gene_score


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


def test_a_build_nullified_gene_score_histogram_puts_no_image_in_the_help(
    built_gene_score: GeneScore,
) -> None:
    help_text = gene_score_help(built_gene_score, "label")

    assert "![HISTOGRAM]" not in help_text
    assert "None" not in help_text


def test_a_gene_score_histogram_the_build_drew_is_embedded_in_the_help(
    built_gene_score: GeneScore,
) -> None:
    assert "![HISTOGRAM](" in gene_score_help(built_gene_score, "pli")


def test_an_annulled_gene_score_histogram_has_no_image_address(
    gene_score: GeneScore,
) -> None:
    assert gene_score.get_histogram_image_public_url("nullified") is None


def test_an_annulled_gene_score_histogram_puts_no_image_in_the_help(
    gene_score: GeneScore,
) -> None:
    assert "![HISTOGRAM]" not in gene_score_help(gene_score, "nullified")
