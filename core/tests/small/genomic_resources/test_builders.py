# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pathlib

from gain.genomic_resources.repository import GenomicResourceProtocolRepo
from gain.genomic_resources.genomic_scores import PositionScore
from gain.genomic_resources.testing.builders import (
    a_grr,
    a_position_score,
)


def test_bare_default_is_a_readable_minimal_score(
    tmp_path: pathlib.Path,
) -> None:
    res = a_position_score().build_resource(tmp_path)

    assert res.get_type() == "position_score"
    score = PositionScore(res).open()
    assert len(score.get_all_scores()) == 1
    (score_id,) = score.get_all_scores()
    values = score.fetch_scores("1", 10)
    assert values is not None
    assert isinstance(values[0], float)


def test_grr_resource_reads_back_authored_values(
    tmp_path: pathlib.Path,
) -> None:
    repo = (
        a_grr()
        .with_resource(
            "scores/pos",
            a_position_score()
            .with_score("phastCons100way", "float")
            .with_data("""
                chrom  pos_begin  phastCons100way
                1      10         0.02
                1      11         0.03
                1      15         0.46
                2      8          0.01
            """),
        )
        .build_repo(tmp_path)
    )

    assert isinstance(repo, GenomicResourceProtocolRepo)
    score = PositionScore(repo.get_resource("scores/pos")).open()
    assert score.get_all_scores() == ["phastCons100way"]
    assert score.fetch_scores("1", 11) == [0.03]
    assert score.fetch_scores("1", 15) == [0.46]
    assert score.fetch_scores("2", 8) == [0.01]
    assert score.fetch_scores("1", 12) is None
