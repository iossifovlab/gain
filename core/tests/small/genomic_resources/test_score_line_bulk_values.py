"""Equivalence tests for the bulk value-extraction path on ``ScoreLine``.

``ScoreLine.get_values`` extracts the values for a whole line given a list
of already-resolved score definitions, hoisting the name->definition lookup
out of the per-line loop.  These tests pin it to the single-score
``ScoreLine.get_score`` path: for every input the bulk method must return
*exactly* what looping ``get_score`` returns -- including ``None`` for an
absent key, ``None`` for a configured NA value, and ``None`` (plus a logged
parse failure) for an unparseable value.
"""
from __future__ import annotations

import logging

import pytest
from gain.genomic_resources.genomic_scores import (
    AlleleScore,
    PositionScore,
    ScoreLine,
    _ScoreDef,
)
from gain.genomic_resources.testing.builders import (
    a_grr,
    a_position_score,
    a_vcf_info_score,
)


def _defs(score: PositionScore | AlleleScore) -> list[_ScoreDef]:
    return [
        score.score_definitions[score_id]
        for score_id in score.get_all_scores()
    ]


def _open_position(tmp_path, data: str) -> PositionScore:
    builder = (
        a_position_score()
        .with_score("s_float", "float")
        .with_score("s_str", "str")
        .with_data(data)
    )
    repo = a_grr().with_resource("pos", builder).build_repo(tmp_path)
    score = PositionScore(repo.get_resource("pos")).open()
    assert isinstance(score, PositionScore)
    return score


def test_bulk_matches_per_score_on_tabular(tmp_path) -> None:
    score = _open_position(tmp_path, """
        chrom  pos_begin  s_float  s_str
        1      10         0.5      hello
        1      11         0.25     world
    """)
    with score:
        for line in score.fetch_lines("1", 10, 11):
            per_score = [line.get_score(s) for s in score.get_all_scores()]
            bulk = line.get_values(_defs(score))
            assert bulk == per_score
        line = next(iter(score.fetch_lines("1", 10, 10)))
        assert line.get_values(_defs(score)) == [0.5, "hello"]


def test_bulk_na_value_yields_none(tmp_path) -> None:
    score = _open_position(tmp_path, """
        chrom  pos_begin  s_float  s_str
        1      10         .        hello
    """)
    with score:
        line = next(iter(score.fetch_lines("1", 10, 10)))
        per_score = [line.get_score(s) for s in score.get_all_scores()]
        bulk = line.get_values(_defs(score))
        assert bulk == per_score
        assert bulk[0] is None


def test_bulk_unparseable_value_logs_and_yields_none(
    tmp_path, caplog: pytest.LogCaptureFixture,
) -> None:
    score = _open_position(tmp_path, """
        chrom  pos_begin  s_float  s_str
        1      10         not_a_number  hello
    """)
    with score:
        line = next(iter(score.fetch_lines("1", 10, 10)))
        defs = _defs(score)

        with caplog.at_level(logging.ERROR):
            per_score = [line.get_score(s) for s in score.get_all_scores()]
        per_score_records = len(caplog.records)
        assert per_score_records >= 1
        assert per_score[0] is None

        caplog.clear()
        with caplog.at_level(logging.ERROR):
            bulk = line.get_values(defs)
        assert bulk == per_score
        assert bulk[0] is None
        assert len(caplog.records) == per_score_records


def test_bulk_matches_per_score_on_vcf_absent_info_key(tmp_path) -> None:
    builder = a_vcf_info_score().with_data("""
##fileformat=VCFv4.1
##INFO=<ID=scoreA,Number=1,Type=Float,Description="score A">
##INFO=<ID=scoreB,Number=1,Type=Float,Description="score B">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   10  .  A   T   .    .      scoreA=0.1
chr1   11  .  A   T   .    .      scoreA=0.2;scoreB=0.5
""")
    repo = a_grr().with_resource("vcf", builder).build_repo(tmp_path)
    score = AlleleScore(repo.get_resource("vcf")).open()
    with score:
        line = next(iter(score.fetch_lines("chr1", 10, 10)))
        per_score = [line.get_score(s) for s in score.get_all_scores()]
        bulk = line.get_values(_defs(score))
        assert bulk == per_score
        # scoreB is absent from this record's INFO -> null raw value -> None
        assert None in bulk


def test_get_values_returns_new_ordered_list(tmp_path) -> None:
    score = _open_position(tmp_path, """
        chrom  pos_begin  s_float  s_str
        1      10         0.5      hello
    """)
    with score:
        line = next(iter(score.fetch_lines("1", 10, 10)))
        reversed_defs = list(reversed(_defs(score)))
        assert isinstance(line, ScoreLine)
        assert line.get_values(reversed_defs) == ["hello", 0.5]
