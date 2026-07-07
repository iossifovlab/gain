# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pathlib

from gain.genomic_resources.genomic_scores import PositionScore
from gain.genomic_resources.testing.builders import (
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
