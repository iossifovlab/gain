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
    # The NA token must *parse successfully* so this test actually exercises
    # the na_values branch: ``"nan"`` is a configured NA value for a float
    # score AND ``float("nan")`` returns ``nan`` (it does not raise).  A
    # token like ``"."`` would be masking -- it also fails to parse, so the
    # value would come back ``None`` via the except path even if the NA
    # check were deleted, and the test could not tell the difference.
    score = _open_position(tmp_path, """
        chrom  pos_begin  s_float  s_str
        1      10         nan      hello
    """)
    with score:
        line = next(iter(score.fetch_lines("1", 10, 10)))
        # "nan" is a configured NA value for a float score, and float("nan")
        # does not raise -- so only the na_values check makes this None.
        assert "nan" in score.score_definitions["s_float"].na_values
        per_score = [line.get_score(s) for s in score.get_all_scores()]
        bulk = line.get_values(_defs(score))
        # The absolute assertions are the real guard here.  While both paths
        # share ``_extract_value``, ``bulk == per_score`` is tautological --
        # dropping the na_values check makes BOTH return ``nan``, and the
        # comparison then fails only incidentally, because ``nan != nan``.
        # Assert the absolute values first so a broken NA branch fails for
        # the right reason.  The equivalence assertion earns its keep only
        # if the single-value logic is ever forked again.
        assert bulk[0] is None
        assert per_score[0] is None
        assert bulk[1] == "hello"
        assert bulk == per_score


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


# --- empty-region + unknown-score-id: the score-name resolution must not
# happen when no line is extracted.  Base only resolved a name inside the
# per-line loop (or after a `if not lines: return` guard), so an empty
# region never rejected an unknown score id.  Hoisting the resolution out
# of the loop must preserve that: an empty region with an unknown score id
# must behave exactly as base -- no KeyError.

def test_region_fetch_empty_region_unknown_score_is_not_an_error(
    tmp_path,
) -> None:
    # Regression for the eager-resolution divergence: on base an empty
    # region with an unknown score id yields no rows; it must not raise.
    score = _open_position(tmp_path, """
        chrom  pos_begin  s_float  s_str
        1      10         0.5      hello
    """)
    with score:
        assert list(score.fetch_region_values(
            "1", 5000, 5001, scores=["NOPE"])) == []


def test_region_fetch_nonempty_region_unknown_score_still_raises(
    tmp_path,
) -> None:
    # Behaviour preservation on the other side: a region that does yield a
    # line still rejects an unknown score id (base raised KeyError inside
    # the loop via score_defs[...]).
    score = _open_position(tmp_path, """
        chrom  pos_begin  s_float  s_str
        1      10         0.5      hello
    """)
    with score, pytest.raises(KeyError):
        list(score.fetch_region_values("1", 10, 10, scores=["NOPE"]))


def test_point_fetch_empty_region_unknown_score_returns_none(
    tmp_path,
) -> None:
    # PositionScore.fetch_scores resolves after `if not lines: return None`,
    # so an empty region short-circuits before touching the unknown score id.
    score = _open_position(tmp_path, """
        chrom  pos_begin  s_float  s_str
        1      10         0.5      hello
    """)
    with score:
        assert score.fetch_scores("1", 5000, scores=["NOPE"]) is None


def test_allele_point_fetch_empty_region_unknown_score_returns_none(
    tmp_path,
) -> None:
    # AlleleScore.fetch_scores resolves after its `if not lines`/
    # `if not selected_line` guards -- an empty region returns None.
    builder = a_vcf_info_score().with_data("""
##fileformat=VCFv4.1
##INFO=<ID=scoreA,Number=1,Type=Float,Description="score A">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   10  .  A   T   .    .      scoreA=0.1
""")
    repo = a_grr().with_resource("vcf", builder).build_repo(tmp_path)
    score = AlleleScore(repo.get_resource("vcf")).open()
    with score:
        assert score.fetch_scores(
            "chr1", 5000, "A", "T", scores=["NOPE"]) is None
