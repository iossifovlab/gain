# pylint: disable=W0621,C0114,C0116,W0212,W0613

from collections import Counter
from dataclasses import fields

import numpy
import pytest
from gain.genomic_resources.aggregators import (
    AGGREGATOR_CLASS_DICT,
    NUMERIC_ONLY_AGGREGATORS,
    Aggregator,
    AggregatorDefinition,
    BoolAggregator,
    ConcatAggregator,
    CountAggregator,
    CounterAggregator,
    JoinAggregator,
    ListAggregator,
    MaxAggregator,
    MeanAggregator,
    MedianAggregator,
    MinAggregator,
    ModeAggregator,
    PositionScoreAggregationQuery,
    ScoreAggregationQuery,
    aggregator_name,
)


def test_concat_aggregator() -> None:
    values = ["a", "b", "c", "d"]
    agg = ConcatAggregator()
    for val in values:
        agg.add(val)

    assert agg.get_final() == "abcd"


def test_min_aggregator() -> None:
    values = [5, 6, 1, 2, 7]
    agg = MinAggregator()
    for val in values:
        agg.add(val)

    assert agg.get_final() == 1


def test_min_aggregator_string() -> None:
    values = ["asdf", "ghjk", "zab", "zob"]
    agg = MinAggregator()
    for val in values:
        agg.add(val)

    assert agg.get_final() == "asdf"


def test_max_aggregator() -> None:
    values = [5, 6, 1, 2, 7]
    agg = MaxAggregator()
    for val in values:
        agg.add(val)

    assert agg.get_final() == 7


def test_max_aggregator_string() -> None:
    values = ["asdf", "ghjk", "zab", "zob"]
    agg = MaxAggregator()
    for val in values:
        agg.add(val)

    assert agg.get_final() == "zob"


def test_mean_aggregator() -> None:
    values = [1, 2, 3, 4]
    agg = MeanAggregator()
    for val in values:
        agg.add(val)

    assert numpy.isclose(agg.get_final(), 2.5)


def test_count_aggregator() -> None:
    generic_values = [1, 2, 3, 4]
    agg = CountAggregator()
    for val in generic_values:
        agg.add(val)

    assert agg.get_final() == 4

    advanced_values = [{"a": 1}, {"b": 2}, {"c": 3}]
    agg = CountAggregator()
    for val1 in advanced_values:
        agg.add(val1)

    assert agg.get_final() == 3


def test_median_aggregator_even() -> None:
    values = [2, 3, 4, 1]
    agg = MedianAggregator()
    for val in values:
        agg.add(val)

    assert numpy.isclose(agg.get_final(), 2.5)


def test_median_aggregator_odd() -> None:
    values = [2, 3, 4, 1, 5]
    agg = MedianAggregator()
    for val in values:
        agg.add(val)

    assert agg.get_final() == 3


def test_median_aggregator_string_even() -> None:
    values = ["a", "c", "b", "d"]
    agg = MedianAggregator()
    for val in values:
        agg.add(val)

    assert agg.get_final() == "bc"


def test_median_aggregator_string_odd() -> None:
    values = ["a", "c", "b", "d", "f"]
    agg = MedianAggregator()
    for val in values:
        agg.add(val)

    assert agg.get_final() == "c"


def test_mode_aggregator() -> None:
    values = [1, 2, 3, 1, 5, 6, 6, 1]
    agg = ModeAggregator()
    for val in values:
        agg.add(val)

    assert agg.get_final() == 1


def test_mode_aggregator_multimode() -> None:
    values = [6, 2, 3, 6, 5, 1, 1, 6, 1, 4, 4, 4]
    agg = ModeAggregator()
    for val in values:
        agg.add(val)

    assert agg.get_final() == 1


def test_join_aggregator() -> None:
    values = [1, 2, 3, 4, 5]
    agg = JoinAggregator(", ")
    for val in values:
        agg.add(val)

    assert agg.get_final() == "1, 2, 3, 4, 5"


def test_aggregator_used_counts() -> None:
    values = [1, 2, 3, None, None, 4, 5]
    agg = MinAggregator()
    for val in values:
        agg.add(val)

    assert agg.get_used_count() == 5
    assert agg.get_total_count() == 7
    agg.clear()
    assert agg.get_used_count() == 0
    assert agg.get_total_count() == 0

    agg.add("asdf")
    agg.add("ghjk")

    assert agg.get_used_count() == 2
    assert agg.get_total_count() == 2


def test_list_aggregator() -> None:
    values = [1, 2, 3, 4]
    agg = ListAggregator()
    for val in values:
        agg.add(val)

    assert agg.get_final() == values


def test_bool_aggregator_with_values() -> None:
    agg = BoolAggregator()
    agg.add("g1")
    agg.add("g2")
    assert agg.get_final() is True


def test_bool_aggregator_empty() -> None:
    agg = BoolAggregator()
    assert agg.get_final() is False


def test_bool_aggregator_none_values() -> None:
    agg = BoolAggregator()
    agg.add(None)
    agg.add(None)
    assert agg.get_final() is False


def test_bool_aggregator_mixed() -> None:
    agg = BoolAggregator()
    agg.add(None)
    agg.add("g1")
    assert agg.get_final() is True


def test_counter_aggregator() -> None:
    values = ["a", "b", "a", "c", "b", "a"]
    agg = CounterAggregator()
    for val in values:
        agg.add(val)

    assert agg.get_final() == {
        "a": 3,
        "b": 2,
        "c": 1,
    }


def test_counter_aggregator_with_none_values() -> None:
    values = ["a", None, "b", "a", None, "b", "a"]
    agg = CounterAggregator()
    for val in values:
        agg.add(val)

    assert agg.get_final() == {"a": 3, "b": 2}


def test_counter_aggregator_empty() -> None:
    agg = CounterAggregator()
    assert agg.get_final() == {}


def test_counter_aggregator_aggregate_method_string_values() -> None:
    agg = CounterAggregator()
    result = agg.aggregate(["pathogenic", "benign", "pathogenic", "vus"])
    assert result == {"pathogenic": 2, "benign": 1, "vus": 1}


def test_counter_aggregator_aggregate_method_with_none() -> None:
    agg = CounterAggregator()
    result = agg.aggregate(["x", None, "x", "y", None])
    assert result == {"x": 2, "y": 1}


def test_counter_aggregator_aggregate_method_empty_list() -> None:
    agg = CounterAggregator()
    assert agg.aggregate([]) == {}


def test_counter_aggregator_aggregate_method_none_input() -> None:
    agg = CounterAggregator()
    assert agg.aggregate(None) == {}


# The reduction requests.  What is pinned here is the SHAPE of the request
# types -- that the kind-neutral one carries only what every kind can ask,
# and that splitting it out left the position request's own shape alone.
# What a request MEANS once resolved belongs to test_score_aggregation and
# the score classes' own suites.


def test_a_position_query_is_not_a_kind_neutral_score_aggregation_query(
) -> None:
    """Re-introducing the base would re-open the substitution door.

    See ``ScoreAggregationQuery`` for what substitution costs (gain#1302).
    The type checker is what keeps the door shut, and no test here runs
    it; to re-check by hand under this repo's ``mypy.ini``, a position
    query placed in a ``list[ScoreAggregationQuery]`` is ``[list-item]``
    and a ``list[PositionScoreAggregationQuery]`` handed to the neutral
    resolver is ``[arg-type]``.
    """
    assert not issubclass(
        PositionScoreAggregationQuery, ScoreAggregationQuery)


def test_the_position_query_opens_with_the_neutral_query_s_fields() -> None:
    # Inheritance used to keep the two shared fields identical; as siblings
    # only this does.  A rename or a default changed on one side passes
    # every other test here and breaks a keyword call site of the other
    # kind at runtime.
    shared = [
        (f.name, f.type, f.default) for f in fields(ScoreAggregationQuery)
    ]

    assert [
        (f.name, f.type, f.default)
        for f in fields(PositionScoreAggregationQuery)
    ][:len(shared)] == shared


def test_the_kind_neutral_query_carries_no_none_value_replacement() -> None:
    # Hoisting the field onto the neutral query would break no call site
    # and pass every other test here, while quietly undoing the split: a
    # fragment or allele request would carry a field that can only speak
    # for an uncovered position, which neither kind has.
    assert [f.name for f in fields(ScoreAggregationQuery)] == [
        "score", "aggregator",
    ]


def test_the_position_query_still_binds_three_positional_arguments() -> None:
    # The whole reason splitting the neutral type out needed no migration,
    # and unpicking the subclassing (gain#1302) needed none either: the
    # position query's own field lands third, exactly where it was on the
    # flat dataclass, so every positional call site keeps its meaning.  If
    # this breaks, callers do not fail -- they silently bind the wrong
    # field.
    query = PositionScoreAggregationQuery("s", "max", 0.0)

    assert (
        query.score, query.aggregator, query.none_value_replacement,
    ) == ("s", "max", 0.0)


def test_the_three_aggregator_spellings_collapse_to_one_name() -> None:
    """An attribute may write its aggregator three ways; a query holds one.

    An annotation pipeline accepts a name, a ``{aggregator_type,
    parameters}`` mapping, or an already-parsed ``AggregatorDefinition``,
    but everything downstream of the config -- a ``ScoreDef``'s field, a
    ``ScoreAggregationQuery``'s -- is typed to the NAME alone, so the
    three have to meet somewhere.

    ``join`` is the case that can tell them apart, being the only
    parametrized aggregator: an unparametrized one collapses to the same
    string however it was written, so a canonicalisation that dropped the
    parameter would still look right for every other aggregator.
    """
    spellings = [
        "join(|)",
        {"aggregator_type": "join", "parameters": ["|"]},
        AggregatorDefinition("join", ["|"]),
    ]

    assert [aggregator_name(s) for s in spellings] == ["join(|)"] * 3


def test_numeric_aggregators_appear_first_in_dict() -> None:
    keys = list(AGGREGATOR_CLASS_DICT.keys())
    numeric_indices = [keys.index(a) for a in NUMERIC_ONLY_AGGREGATORS]
    non_numeric_indices = [
        i for i, k in enumerate(keys) if k not in NUMERIC_ONLY_AGGREGATORS
    ]
    assert max(numeric_indices) < min(non_numeric_indices)


def _fresh(name: str) -> Aggregator:
    """A freshly built ``name``, a parametrized one with its default."""
    aggregator_class = AGGREGATOR_CLASS_DICT[name]
    if aggregator_class.parametrized:
        return Aggregator.build(f"{name}({aggregator_class.default_parameter})")
    return Aggregator.build(name)


def _add_sample(agg: Aggregator) -> None:
    """Values every registered aggregator accepts, one of them weighted."""
    agg.add(3)
    agg.add(None)
    agg.add(5, count=4)
    agg.add(2)


@pytest.mark.parametrize("name", list(AGGREGATOR_CLASS_DICT))
def test_clear_returns_every_aggregator_to_its_fresh_state(name: str) -> None:
    # ``Aggregator.__eq__`` compares finals only, so the whole instance
    # state is compared: state ``clear()`` forgot but ``get_final()``
    # happens not to read would otherwise go unnoticed.
    agg = _fresh(name)
    _add_sample(agg)

    agg.clear()

    assert vars(agg) == vars(_fresh(name))


@pytest.mark.parametrize("name", list(AGGREGATOR_CLASS_DICT))
def test_a_cleared_aggregator_refolds_like_a_fresh_one(name: str) -> None:
    agg = _fresh(name)
    _add_sample(agg)
    agg.clear()
    _add_sample(agg)
    fresh = _fresh(name)
    _add_sample(fresh)

    assert agg.get_final() == fresh.get_final()
    assert agg.get_used_count() == fresh.get_used_count()
    assert agg.get_total_count() == fresh.get_total_count()


def _most_common(k: int, values: list[tuple[object, int]]) -> object:
    agg = Aggregator.build(f"most_common({k})")
    for value, count in values:
        agg.add(value, count)
    return agg.get_final()


def test_most_common_ranks_values_by_weighted_frequency() -> None:
    values = [("a", 1), ("b", 5), ("c", 2), ("a", 3)]

    assert _most_common(2, values) == ["b", "a"]


def test_most_common_breaks_a_tie_by_first_appearance() -> None:
    values = [("a", 1), ("c", 2), ("b", 2)]

    assert _most_common(3, values) == ["c", "b", "a"]


def test_most_common_skips_none() -> None:
    values = [(None, 10), ("a", 1), (None, 1), ("b", 2)]

    assert _most_common(3, values) == ["b", "a"]


def test_most_common_flattens_tuples_and_nested_lists() -> None:
    values = [(("a", "b"), 1), (["b", ["c", None, ["b"]]], 2), ("a", 1)]

    assert _most_common(2, values) == ["b", "a"]


def test_most_common_of_no_values_is_an_empty_list() -> None:
    assert _most_common(3, []) == []
    assert _most_common(3, [(None, 4)]) == []


@pytest.mark.parametrize("values", [
    [("a", 1), ("b", 5), ("c", 2), ("a", 3)],
    [("a", 1), ("c", 2), ("b", 2)],
    [(None, 10), ("a", 1), (None, 1), ("b", 2)],
    [(("a", "b"), 1), (["b", ["c", None, ["b"]]], 2), ("a", 1)],
    [((1, 2), 3), (2, 1), (["x", ("y", 1)], 2)],
    [],
])
@pytest.mark.parametrize("k", [1, 2, 3, 10])
def test_most_common_is_the_top_of_a_counter_over_the_list_result(
    values: list[tuple[object, int]], k: int,
) -> None:
    listed = Aggregator.build("list")
    for value, count in values:
        listed.add(value, count)
    leaves = [leaf for leaf in listed.get_final() if leaf is not None]

    expected = [value for value, _ in Counter(leaves).most_common(k)]

    assert _most_common(k, values) == expected


def test_most_common_applies_a_weight_without_a_loop_over_it() -> None:
    assert _most_common(1, [("a", 10**12), ("b", 1)]) == ["a"]


@pytest.mark.parametrize("spelling", [
    "most_common",
    "most_common()",
    "most_common(0)",
    "most_common(-1)",
    "most_common(3.0)",
    "most_common(abc)",
])
def test_most_common_refuses_a_k_that_is_not_a_positive_integer(
    spelling: str,
) -> None:
    with pytest.raises((ValueError, TypeError)):
        Aggregator.build(spelling)
