# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pathlib

import pytest
from gain.genomic_resources.genomic_scores import (
    PositionScore,
    build_position_score_from_resource,
)
from gain.genomic_resources.score_filter import ScoreFilterError
from gain.genomic_resources.testing.builders import (
    a_position_score,
)


@pytest.fixture
def position_score(tmp_path: pathlib.Path) -> PositionScore:
    resource = (
        a_position_score()
        .with_score("freq", "float")
        .with_data("""
            chrom  pos_begin  freq
            1      10         0.1
            1      11         0.2
            1      12         0.3
        """)
        .build_resource(tmp_path)
    )
    return build_position_score_from_resource(resource)


@pytest.fixture
def score_with_missing_values(tmp_path: pathlib.Path) -> PositionScore:
    """A score whose second record says nothing at all.

    Position 11 carries NA in every column, so a filter naming any of them
    has a missing operand on that record; position 10 answers normally.
    A ``str`` score has no default NA sentinel -- ``na`` in a string column
    is the two-character string -- so this one declares it.
    """
    resource = (
        a_position_score()
        .with_score("freq", "float")
        .with_score("other_freq", "float")
        .with_score("ID", "str")
        .with_na_values("na")
        .with_data("""
            chrom  pos_begin  freq  other_freq  ID
            1      10         0.1   0.1         abc
            1      11         na    na          na
        """)
        .build_resource(tmp_path)
    )
    return build_position_score_from_resource(resource)


@pytest.mark.parametrize("expression", [
    "freq > -1",
    "freq < 1",
    # Two missing operands: `None == None` is True in Python, so a record
    # saying nothing used to satisfy an equality between two things it does
    # not have.
    "freq == other_freq",
    '"a" in ID',
    'ID in "abc"',
])
def test_a_missing_operand_makes_its_clause_false(
    score_with_missing_values: PositionScore,
    expression: str,
) -> None:
    """A record that does not carry the value is not selected by it.

    Pinned per operator: each one is a separate comparison in the compiler,
    and each used to fail differently -- ordering raised TypeError on None,
    equality quietly answered True for two absent operands, and containment
    raised on a None container.
    """
    with score_with_missing_values.open() as score:
        score_filter = score.compile_filter(expression)

        records = list(score.fetch_records(
            "1", 10, 11, score_filter=score_filter))

    assert [record[1] for record in records] == [10]


def test_a_missing_operand_still_lets_the_other_arm_select(
    score_with_missing_values: PositionScore,
) -> None:
    """False, not "reject the record": an ``or`` can still carry it.

    The clause is what goes False; the record stays in play for the rest of
    the expression, which is the difference between defining the missing
    case and raising on it.
    """
    with score_with_missing_values.open() as score:
        score_filter = score.compile_filter("freq > 0.5 or 1 == 1")

        records = list(score.fetch_records(
            "1", 10, 11, score_filter=score_filter))

    assert [record[1] for record in records] == [10, 11]


def test_fetch_records_keeps_only_records_the_filter_accepts(
    position_score: PositionScore,
) -> None:
    with position_score.open() as score:
        score_filter = score.compile_filter("freq > 0.15")

        records = list(score.fetch_records(
            "1", 10, 12, score_filter=score_filter))

    assert [record[1] for record in records] == [11, 12]


def test_a_real_nan_is_missing_too(tmp_path: pathlib.Path) -> None:
    """``nan`` is absent data whether or not it arrived through an NA cell.

    A float score that declares its own NA sentinel stops treating the text
    ``nan`` as one, so the cell parses to an actual ``float('nan')`` -- which
    orders False against everything and would silently drop out of both
    arms of a comparison rather than being recognised as missing.
    """
    resource = (
        a_position_score()
        .with_score("freq", "float")
        .with_na_values("-1")
        .with_data("""
            chrom  pos_begin  freq
            1      10         0.1
            1      11         nan
        """)
        .build_resource(tmp_path)
    )
    score = build_position_score_from_resource(resource)

    with score.open() as opened:
        score_filter = opened.compile_filter("freq > -1000 or freq < 1000")

        records = list(opened.fetch_records(
            "1", 10, 11, score_filter=score_filter))

    assert [record[1] for record in records] == [10]


@pytest.mark.parametrize("expression, expected_positions", [
    # A digit-bearing identifier: `1000G` is a real score name in the GRR.
    ("1000G > 0.15", [11]),
    # A negative literal, on either side of the operator.
    ("1000G > -1", [10, 11]),
    ("-1 < 1000G", [10, 11]),
])
def test_the_grammar_takes_digits_in_names_and_negative_numbers(
    tmp_path: pathlib.Path,
    expression: str,
    expected_positions: list[int],
) -> None:
    """One grammar, the superset of the two it replaces.

    The fragment-side grammar forbade both of these: its identifiers were
    letters-only and its numbers unsigned, so a fragment score named after a
    population could not be filtered on at all.
    """
    resource = (
        a_position_score()
        .with_score("1000G", "float")
        .with_data("""
            chrom  pos_begin  1000G
            1      10         0.1
            1      11         0.2
        """)
        .build_resource(tmp_path)
    )
    score = build_position_score_from_resource(resource)

    with score.open() as opened:
        score_filter = opened.compile_filter(expression)

        records = list(opened.fetch_records(
            "1", 10, 11, score_filter=score_filter))

    assert [record[1] for record in records] == expected_positions


def test_compile_filter_refuses_a_name_the_score_does_not_define(
    position_score: PositionScore,
) -> None:
    """A typo is a configuration error, and it is caught while compiling.

    Left to read time it would be one exception per record, from deep in a
    fetch loop -- or, worse, a silent miss on a score that never selects
    anything.  The message lists the names that would have worked, because
    the whole failure is that the author meant one of them.
    """
    with position_score.open() as score, \
            pytest.raises(ScoreFilterError) as excinfo:
        score.compile_filter("frequency > 0.15")

    message = str(excinfo.value)
    assert "frequency" in message
    assert "'freq'" in message
