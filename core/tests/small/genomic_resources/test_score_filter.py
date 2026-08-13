# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pathlib

import pytest
from gain.genomic_resources.genomic_scores import (
    AlleleScore,
    FragmentScore,
    PositionScore,
    build_allele_score_from_resource,
    build_position_score_from_resource,
)
from gain.genomic_resources.score_filter import ScoreFilterError
from gain.genomic_resources.testing.builders import (
    a_fragment_score,
    a_position_score,
    an_allele_score,
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
    # The orderings that include equality answer no differently: a value
    # that is not there is not >= or <= anything either.
    "freq >= -1",
    "freq <= 1",
    # `!=` is the one where "false" reads as a choice rather than the only
    # option -- a record carrying nothing is arguably "not 0.5".  It is
    # still false: the rule is per CLAUSE, and a clause cannot speak about
    # a value that is absent.  `not (freq == 0.5)` is how you ask the other
    # question; see the test below for the two parting company.
    "freq != 0.5",
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


def test_inequality_and_a_negated_equality_disagree_on_missing(
    score_with_missing_values: PositionScore,
) -> None:
    """``x != v`` is not a synonym for ``not (x == v)``.

    Over a record that carries a value the two agree, and over one that does
    not they cannot: a comparison with a missing operand is false, so ``!=``
    declines to select -- while ``not`` negates that false and selects.

    De Morgan therefore does not carry across the comparison boundary.  It
    is a consequence of the missing-value rule rather than a separate
    decision, but it is the consequence people trip over, so it is pinned
    rather than left to be rediscovered.
    """
    with score_with_missing_values.open() as score:
        inequality = score.compile_filter("freq != 0.5")
        negated_equality = score.compile_filter("not (freq == 0.5)")

        by_inequality = list(score.fetch_records(
            "1", 10, 11, score_filter=inequality))
        by_negated_equality = list(score.fetch_records(
            "1", 10, 11, score_filter=negated_equality))

    # Position 11 carries no freq at all; position 10 carries 0.1.
    assert [record[1] for record in by_inequality] == [10]
    assert [record[1] for record in by_negated_equality] == [10, 11]


def test_fetch_records_keeps_only_records_the_filter_accepts(
    position_score: PositionScore,
) -> None:
    with position_score.open() as score:
        score_filter = score.compile_filter("freq > 0.15")

        records = list(score.fetch_records(
            "1", 10, 12, score_filter=score_filter))

    assert [record[1] for record in records] == [11, 12]


@pytest.fixture
def allele_score(tmp_path: pathlib.Path) -> AlleleScore:
    resource = (
        an_allele_score()
        .with_score("freq", "float")
        .with_data("""
            chrom  pos_begin  reference  alternative  freq
            1      10         A          G            0.1
            1      10         A          C            0.2
        """)
        .build_resource(tmp_path)
    )
    return build_allele_score_from_resource(resource)


def test_a_matched_but_filtered_allele_reads_as_absent(
    allele_score: AlleleScore,
) -> None:
    """The allele reads are per-allele, so a rejected one is ``None``.

    Same answer as an allele the resource does not carry: the caller asked
    for values it is not to have, and there is nothing else to say.  Nothing
    is extracted for it either -- the filter runs on the record.
    """
    with allele_score.open() as score:
        score_filter = score.compile_filter("freq > 0.15")

        kept = score.fetch_allele_scores(
            "1", 10, "A", "C", score_filter=score_filter)
        rejected = score.fetch_allele_scores(
            "1", 10, "A", "G", score_filter=score_filter)
        absent = score.fetch_allele_scores(
            "1", 10, "A", "T", score_filter=score_filter)

    assert kept == {"freq": pytest.approx(0.2)}
    # The rejected allele and the one the resource does not carry at all are
    # the same answer, which is the point.
    assert rejected is None
    assert absent is None


def test_an_allele_read_without_a_filter_is_unchanged(
    allele_score: AlleleScore,
) -> None:
    """``score_filter=None`` is the whole of the old behaviour."""
    with allele_score.open() as score:
        scores = score.fetch_allele_scores("1", 10, "A", "G")

    assert scores == {"freq": pytest.approx(0.1)}


@pytest.fixture
def fragment_score(tmp_path: pathlib.Path) -> FragmentScore:
    resource = (
        a_fragment_score()
        .with_score("freq", "float")
        .with_score("collection", "str")
        .with_data("""
            chrom  pos_begin  pos_end  freq  collection
            1      10         20       0.02  SSC
            1      15         30       0.10  AGRE
        """)
        .build_resource(tmp_path)
    )
    return FragmentScore(resource)


def test_fragment_reads_drop_the_fragments_the_filter_rejects(
    fragment_score: FragmentScore,
) -> None:
    """Filtered per fragment, and filtered before anything is extracted.

    A region read reports one dict per overlapping fragment, so a rejected
    fragment simply is not among them -- the empty list stays reserved for
    a region no fragment overlaps.
    """
    with fragment_score.open() as score:
        score_filter = score.compile_filter('collection == "AGRE"')

        fragments = score.fetch_fragment_scores(
            "1", 10, 30, score_filter=score_filter)

    assert fragments == [{"freq": pytest.approx(0.1), "collection": "AGRE"}]


def test_the_new_syntax_reaches_the_fragment_read(
    fragment_score: FragmentScore,
) -> None:
    """Grouping and the new operators work through a real read path.

    The grammar is shared, but the read paths are not: this one applies the
    predicate to the record before extracting anything.
    """
    with fragment_score.open() as score:
        score_filter = score.compile_filter(
            '(collection == "SSC" or collection == "AGRE") and freq >= 0.1')

        fragments = score.fetch_fragment_scores(
            "1", 10, 30, score_filter=score_filter)

    assert fragments == [{"freq": pytest.approx(0.1), "collection": "AGRE"}]


def test_a_negated_clause_reaches_the_allele_read(
    allele_score: AlleleScore,
) -> None:
    """A negation selects per allele, and a rejected allele still reads None."""
    with allele_score.open() as score:
        score_filter = score.compile_filter("not (freq >= 0.15)")

        kept = score.fetch_allele_scores(
            "1", 10, "A", "G", score_filter=score_filter)
        rejected = score.fetch_allele_scores(
            "1", 10, "A", "C", score_filter=score_filter)

    assert kept == {"freq": pytest.approx(0.1)}
    assert rejected is None


def test_a_fragment_filter_may_name_a_score_that_was_not_requested(
    fragment_score: FragmentScore,
) -> None:
    """The filter reads the record, not the dict that is handed back.

    Naming a score outside ``scores`` used to read ``None`` out of the
    extracted dict and silently match nothing; the record carries every
    score the resource defines, so the filter answers on the real value.
    """
    with fragment_score.open() as score:
        score_filter = score.compile_filter('collection == "AGRE"')

        fragments = score.fetch_fragment_scores(
            "1", 10, 30, scores=["freq"], score_filter=score_filter)

    assert fragments == [{"freq": pytest.approx(0.1)}]


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


@pytest.mark.parametrize("expression, expected_positions", [
    ("freq > 0.15 and freq < 0.25", [11]),
    ("freq < 0.15 or freq > 0.25", [10, 12]),
    # Chains of three: the compiler nests these, and a nesting that
    # associated them wrongly would still answer correctly for two terms.
    ("freq > 0.05 and freq > 0.15 and freq > 0.25", [12]),
    ("freq < 0.15 or freq == 0.2 or freq > 0.25", [10, 11, 12]),
    # Mixed: `and` binds tighter than `or`. The second clause is false for
    # every record, so this discriminates -- `A or (B and C)` keeps the
    # record `A` selects, while `(A or B) and C` would keep nothing.
    ("freq == 0.1 or freq > 0.15 and freq < 0.05", [10]),
])
def test_the_connectives_combine_clauses(
    position_score: PositionScore,
    expression: str,
    expected_positions: list[int],
) -> None:
    """``and``/``or``, including chains longer than the two-term case.

    The two-term case is what a broken nesting still gets right, so the
    three-term chains are the ones that actually pin the compiler's
    recursion.
    """
    with position_score.open() as score:
        score_filter = score.compile_filter(expression)

        records = list(score.fetch_records(
            "1", 10, 12, score_filter=score_filter))

    assert [record[1] for record in records] == expected_positions


@pytest.mark.parametrize("expression, expected_positions", [
    # Each pairs with its strict form, which excludes the boundary record:
    # `freq > 0.2` is [12] and `freq < 0.2` is [10].  Without position 11
    # in the answer these would pass against the strict operator.
    ("freq >= 0.2", [11, 12]),
    ("freq <= 0.2", [10, 11]),
])
def test_the_orderings_include_the_boundary(
    position_score: PositionScore,
    expression: str,
    expected_positions: list[int],
) -> None:
    """``>=`` and ``<=`` order like their strict forms but keep equality."""
    with position_score.open() as score:
        score_filter = score.compile_filter(expression)

        records = list(score.fetch_records(
            "1", 10, 12, score_filter=score_filter))

    assert [record[1] for record in records] == expected_positions


def test_inequality_selects_what_equality_does_not(
    position_score: PositionScore,
) -> None:
    """``!=`` is the complement of ``==`` over records that carry a value.

    Over records that do NOT carry one the two are not complements, which
    :func:`test_inequality_and_a_negated_equality_disagree_on_missing`
    pins separately.
    """
    with position_score.open() as score:
        score_filter = score.compile_filter("freq != 0.2")

        records = list(score.fetch_records(
            "1", 10, 12, score_filter=score_filter))

    assert [record[1] for record in records] == [10, 12]


def test_not_negates_the_clause_it_applies_to(
    position_score: PositionScore,
) -> None:
    """``not`` selects exactly the records the bare clause does not."""
    with position_score.open() as score:
        negated = score.compile_filter("not freq > 0.15")
        bare = score.compile_filter("freq > 0.15")

        negated_records = list(score.fetch_records(
            "1", 10, 12, score_filter=negated))
        bare_records = list(score.fetch_records(
            "1", 10, 12, score_filter=bare))

    assert [record[1] for record in negated_records] == [10]
    # Complementary over these records, which all carry a value.
    assert [record[1] for record in bare_records] == [11, 12]


@pytest.mark.parametrize("expression, expected_positions", [
    # Grouped, the `and` applies to both arms of the `or`: position 10 is
    # dropped because it fails `freq > 0.15`.
    ("(freq == 0.1 or freq == 0.2) and freq > 0.15", [11]),
    # Ungrouped, `and` binds tighter, so the `or`'s left arm stands alone
    # and position 10 survives.  The two readings differ HERE, which is
    # what makes this pair a test rather than a pair of assertions.
    ("freq == 0.1 or freq == 0.2 and freq > 0.15", [10, 11]),
])
def test_parentheses_regroup_what_precedence_would_bind(
    position_score: PositionScore,
    expression: str,
    expected_positions: list[int],
) -> None:
    """Grouping overrides the default binding, and only where written."""
    with position_score.open() as score:
        score_filter = score.compile_filter(expression)

        records = list(score.fetch_records(
            "1", 10, 12, score_filter=score_filter))

    assert [record[1] for record in records] == expected_positions


@pytest.mark.parametrize("expression, expected_positions", [
    # `not` tighter than `and`: this is `(not freq > 0.15) and freq < 0.25`.
    # Read the other way -- `not (freq > 0.15 and freq < 0.25)` -- it would
    # be [10, 12].
    ("not freq > 0.15 and freq < 0.25", [10]),
    # `not` tighter than `or`: this is `(not freq > 0.25) or freq > 0.15`,
    # which is every record.  Read as `not (freq > 0.25 or freq > 0.15)`
    # it would be [10].
    ("not freq > 0.25 or freq > 0.15", [10, 11, 12]),
    # Parentheses buy back the looser reading.
    ("not (freq > 0.15 and freq < 0.25)", [10, 12]),
])
def test_not_binds_tighter_than_the_connectives(
    position_score: PositionScore,
    expression: str,
    expected_positions: list[int],
) -> None:
    """Precedence runs ``not`` then ``and`` then ``or``.

    Each case is chosen so the two readings give DIFFERENT records; an
    expression both readings agree on would pin nothing.
    """
    with position_score.open() as score:
        score_filter = score.compile_filter(expression)

        records = list(score.fetch_records(
            "1", 10, 12, score_filter=score_filter))

    assert [record[1] for record in records] == expected_positions


def test_a_score_named_with_punctuation_can_no_longer_be_filtered_on(
    tmp_path: pathlib.Path,
) -> None:
    """What making ``(``, ``)`` and ``!`` mean something cost.

    They were legal in a variable name, along with ``@#$%^&*+`` -- which is
    why a parenthesised expression used to parse as a comparison between two
    oddly-named scores rather than as a group.  A resource may still DEFINE
    such a score; it can no longer be named in a filter.

    The refusal is a PARSE error, not the unknown-name one: the score is
    asserted to exist first, so this cannot pass for the wrong reason.
    """
    resource = (
        a_position_score()
        .with_score("a!b", "float")
        .with_data("""
            chrom  pos_begin  a!b
            1      10         0.1
        """)
        .build_resource(tmp_path)
    )
    score = build_position_score_from_resource(resource)

    with score.open() as opened:
        assert "a!b" in opened.score_definitions

        with pytest.raises(ScoreFilterError) as excinfo:
            opened.compile_filter("a!b > 0.05")

    assert "cannot parse" in str(excinfo.value)


def test_a_quoted_literal_still_takes_the_punctuation_it_always_did(
    tmp_path: pathlib.Path,
) -> None:
    """Only the variable name narrowed; the literal kept its characters.

    The two were one terminal, so narrowing identifiers would have silently
    narrowed what a quoted literal may hold -- and the annotator docs
    advertise ``!@#$%^&*()+`` in a literal.  Quotes delimit it, so it needs
    no narrowing to keep ``(`` and ``!`` unambiguous.
    """
    resource = (
        a_position_score()
        .with_score("CLNSIG", "str")
        .with_data("""
            chrom  pos_begin  CLNSIG
            1      10         path!o(genic)
            1      11         benign
        """)
        .build_resource(tmp_path)
    )
    score = build_position_score_from_resource(resource)

    with score.open() as opened:
        score_filter = opened.compile_filter('CLNSIG == "path!o(genic)"')

        records = list(opened.fetch_records(
            "1", 10, 11, score_filter=score_filter))

    assert [record[1] for record in records] == [10]


def test_a_filter_refuses_the_records_of_a_different_score(
    position_score: PositionScore,
    tmp_path: pathlib.Path,
) -> None:
    """A filter belongs to the score that compiled it.

    Its variables are bound to that score's definitions -- a column index,
    a value type, an NA set -- so reading another score's records through
    it extracts by the wrong column. Two resources both defining ``freq``
    is exactly when that goes unnoticed, so it is refused rather than
    answered with values nobody can tell are wrong.
    """
    other = build_position_score_from_resource(
        a_position_score()
        .with_score("padding", "str")
        .with_score("freq", "float")
        .with_data("""
            chrom  pos_begin  padding  freq
            1      10         x        0.9
        """)
        .build_resource(tmp_path / "other"))

    with position_score.open() as score, other.open() as other_score:
        foreign = score.compile_filter("freq > 0.15")

        with pytest.raises(ScoreFilterError) as excinfo:
            list(other_score.fetch_records(
                "1", 10, 10, score_filter=foreign))

    assert "compiled against" in str(excinfo.value)


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
