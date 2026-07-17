"""Tests for scalar ``na_values`` normalization (gain issue #268).

A configured ``na_values`` sentinel is permitted by the resource schema as a
bare scalar (``na_values: "-1"``).  Left un-normalized it stays a Python
``str``, and the NA check in ``ScoreLineBase._extract_value`` --
``value in score_def.na_values`` -- then degrades from a membership test into a
SUBSTRING test: ``"1" in "-1"`` is ``True``, so a real score of ``1`` is
silently turned into ``None``.  On a bigWig backend, whose raw payload is a
``float``, the same expression raises ``TypeError`` outright.

These tests pin the fix: a scalar sentinel is normalized to a type-aware
collection and matched against the raw value's own type, never by substring, on
both a text/tabix backend and bigWig.
"""
import pathlib

import pytest
from gain.genomic_resources.genomic_scores import PositionScore
from gain.genomic_resources.testing.builders import (
    a_bigwig_score,
    a_grr,
    a_position_score,
)


def _open_position(
    tmp_path: pathlib.Path, builder, *, resource_id: str = "pos",
) -> PositionScore:
    repo = a_grr().with_resource(resource_id, builder).build_repo(tmp_path)
    score = PositionScore(repo.get_resource(resource_id)).open()
    assert isinstance(score, PositionScore)
    return score


def test_scalar_na_value_marks_only_the_sentinel(
    tmp_path: pathlib.Path,
) -> None:
    # Acceptance criterion 1: na_values "-1" marks -1 as NA and does NOT
    # substring-match a real 1 into NA.
    builder = (
        a_position_score()
        .with_score("s", "int")
        .with_na_values("-1")
        .with_data("""
            chrom  pos_begin  s
            1      10         -1
            1      11         1
        """)
    )
    score = _open_position(tmp_path, builder)
    with score:
        values = [
            line.get_score("s")
            for line in score.fetch_lines("1", 10, 11)
        ]
    assert values == [None, 1]


@pytest.mark.parametrize("tabix", [False, True])
def test_scalar_na_value_never_substring_matches(
    tmp_path: pathlib.Path, tabix: bool,
) -> None:
    # Acceptance criteria 2 & 7 (text/tabix): na_values "-999" must NA only
    # -999 and never substring-match 9, 99, 999, -9 or -99.  Runs on both the
    # in-memory and the tabix record backends; rows are position-sorted so the
    # tabix index build accepts them.
    builder = (
        a_position_score()
        .with_score("s", "int")
        .with_na_values("-999")
        .with_data("""
            chrom  pos_begin  s
            1      10         -999
            1      11         9
            1      12         99
            1      13         999
            1      14         -9
            1      15         -99
        """)
    )
    if tabix:
        builder = builder.with_tabix()
    score = _open_position(tmp_path, builder)
    with score:
        values = [
            line.get_score("s")
            for line in score.fetch_lines("1", 10, 15)
        ]
    assert values == [None, 9, 99, 999, -9, -99]


def test_scalar_and_one_element_list_behave_identically(
    tmp_path: pathlib.Path,
) -> None:
    # Acceptance criterion 3: a scalar sentinel and the equivalent one-element
    # list produce the same NA behaviour.
    data = """
        chrom  pos_begin  s
        1      10         -1
        1      11         1
    """
    scalar = (
        a_position_score().with_score("s", "int")
        .with_na_values("-1").with_data(data)
    )
    listed = (
        a_position_score().with_score("s", "int")
        .with_na_values(["-1"]).with_data(data)
    )
    scalar_score = _open_position(tmp_path / "a", scalar)
    list_score = _open_position(tmp_path / "b", listed)
    with scalar_score, list_score:
        scalar_values = [
            line.get_score("s")
            for line in scalar_score.fetch_lines("1", 10, 11)
        ]
        list_values = [
            line.get_score("s")
            for line in list_score.fetch_lines("1", 10, 11)
        ]
    assert scalar_values == list_values == [None, 1]


def test_non_numeric_sentinel_is_na_tested_before_parsing(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Acceptance criterion 4: a non-numeric sentinel ("NA", ".", "") on a
    # numeric score keeps working -- and does so because the NA test runs
    # BEFORE value parsing.  If parsing ran first, float("NA") would raise and
    # log an "unable to parse" record; asserting no such record is emitted
    # pins the NA-test-then-parse ordering.
    builder = (
        a_position_score()
        .with_score("s", "float")
        .with_na_values(["NA", ".", ""])
        .with_data("""
            chrom  pos_begin  s
            1      10         NA
            1      11         .
            1      12         1.5
        """)
    )
    score = _open_position(tmp_path, builder)
    with caplog.at_level("ERROR"), score:
        values = [
            line.get_score("s")
            for line in score.fetch_lines("1", 10, 12)
        ]
    assert values == [None, None, 1.5]
    assert "unable to parse" not in caplog.text


def test_default_na_values_unchanged_when_absent(
    tmp_path: pathlib.Path,
) -> None:
    # Acceptance criterion 6: with no na_values configured, the per-type
    # default set still applies -- "nan" (which parses) is NA'd, a real value
    # is not.
    builder = (
        a_position_score()
        .with_score("s", "float")
        .with_data("""
            chrom  pos_begin  s
            1      10         nan
            1      11         0.5
        """)
    )
    score = _open_position(tmp_path, builder)
    with score:
        na_values = score.score_definitions["s"].na_values
        values = [
            line.get_score("s")
            for line in score.fetch_lines("1", 10, 11)
        ]
    assert values == [None, 0.5]
    # The default float NA set is preserved verbatim.
    assert {"", "nan", ".", "NA"} <= set(na_values)


def test_bigwig_scalar_na_value_matches_float_payload(
    tmp_path: pathlib.Path,
) -> None:
    # Acceptance criteria 5 & 7 (bigWig): a bigWig-backed score with a scalar
    # na_values no longer raises TypeError (the raw payload is a float, and
    # ``float in "-1"`` used to raise) and correctly NA-matches -1 against its
    # float payload while leaving a real 1 untouched.  bedGraph intervals are
    # 0-based half-open, so 1-based position p reads the interval containing
    # p-1: pos 1 -> [0,10) -> -1, pos 11 -> [10,20) -> 1.
    builder = (
        a_bigwig_score()
        .with_score("bw", "float")
        .with_na_values("-1")
        .with_data("""
            chr1  0   10  -1
            chr1  10  20  1
        """)
        .with_chrom_lens({"chr1": 1000})
    )
    score = _open_position(tmp_path, builder, resource_id="bw")
    with score:
        na_line = next(iter(score.fetch_lines("chr1", 1, 1)))
        real_line = next(iter(score.fetch_lines("chr1", 11, 11)))
        assert na_line.get_score("bw") is None
        assert real_line.get_score("bw") == pytest.approx(1.0)


def test_bigwig_scalar_na_value_never_substring_matches(
    tmp_path: pathlib.Path,
) -> None:
    # Acceptance criteria 5 & 7 (bigWig): na_values "-999" on a bigWig must NA
    # only -999 and never substring-match 99 (which pre-fix would also raise
    # TypeError against the float payload).  pos 1 -> [0,10) -> -999,
    # pos 11 -> [10,20) -> 99.
    builder = (
        a_bigwig_score()
        .with_score("bw", "float")
        .with_na_values("-999")
        .with_data("""
            chr1  0   10  -999
            chr1  10  20  99
        """)
        .with_chrom_lens({"chr1": 1000})
    )
    score = _open_position(tmp_path, builder, resource_id="bw")
    with score:
        na_line = next(iter(score.fetch_lines("chr1", 1, 1)))
        real_line = next(iter(score.fetch_lines("chr1", 11, 11)))
        assert na_line.get_score("bw") is None
        assert real_line.get_score("bw") == pytest.approx(99.0)
