"""A histogram read with no score id reads the only score (gain#1977).

``get_score_histogram()`` with the score id omitted reads the histogram of
the only score of the resource, the same histogram the call that names
that id reads.  A resource with several scores, or none, has no only
score, and the call is refused with the ids the resource does define.
"""
from __future__ import annotations

import pathlib

import pytest
from gain.gene_scores.gene_scores import build_gene_score_from_resource
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.genomic_scores import PositionScore
from gain.genomic_resources.histogram import (
    CategoricalHistogram,
    NumberHistogram,
)
from gain.genomic_resources.testing import build_filesystem_test_repository
from gain.genomic_resources.testing.builders import (
    a_gene_score,
    a_grr,
    a_position_score,
)
from gain.genomic_resources.testing.statistics import publish_statistics


def test_a_one_score_position_score_reads_its_only_score_histogram(
    tmp_path: pathlib.Path,
) -> None:
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
    score = PositionScore(repo.get_resource("pos"))

    hist = score.get_score_histogram()

    assert isinstance(hist, NumberHistogram)
    assert hist.to_dict() == score.get_score_histogram("s").to_dict()


def test_a_truncated_read_of_the_only_score_reads_its_sidecar(
    tmp_path: pathlib.Path,
) -> None:
    # A STATED categorical histogram past the limit is truncated and gets
    # a sidecar; the default one is nulled instead and has none.
    past_limit = CategoricalHistogram.UNIQUE_VALUES_LIMIT + 1
    rows = "\n".join(f"1  {10 + i}  v{i:03d}" for i in range(past_limit))
    a_grr().with_resource("cats", (
        a_position_score()
        .with_score("cell", "str")
        .with_histogram({"type": "categorical", "value_order": []})
        .with_data("chrom  pos_begin  cell\n" + rows)
    )).build_repo(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])
    repo = build_filesystem_test_repository(tmp_path)
    score = PositionScore(repo.get_resource("cats"))

    hist = score.get_score_histogram(truncated=True)

    assert isinstance(hist, CategoricalHistogram)
    assert hist.truncated is True
    assert hist.to_dict() == score.get_score_histogram(
        "cell", truncated=True).to_dict()


def test_a_one_score_gene_score_reads_its_only_score_histogram(
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

    hist = score.get_score_histogram()

    assert isinstance(hist, NumberHistogram)
    assert hist.to_dict() == score.get_score_histogram("pli").to_dict()


def test_a_two_score_position_score_has_no_only_score(
    tmp_path: pathlib.Path,
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

    with pytest.raises(ValueError) as excinfo:
        score.get_score_histogram()

    assert str(excinfo.value) == (
        "resource 'pos' has no only score to read without a score id; "
        "it has ['s', 't']")
