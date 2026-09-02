# pylint: disable=W0621,C0114,C0116,W0212,W0613

from dataclasses import fields

import numpy
from gain.genomic_resources.aggregators import (
    AGGREGATOR_CLASS_DICT,
    NUMERIC_ONLY_AGGREGATORS,
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


def test_a_position_query_is_a_kind_neutral_score_aggregation_query() -> None:
    # A reader should not have to infer the relationship from three
    # overlapping fields: asking to reduce a score with an aggregator is
    # the general request, and the position query IS one.
    query = PositionScoreAggregationQuery("s", "max", 0.0)

    assert isinstance(query, ScoreAggregationQuery)
    assert (query.score, query.aggregator) == ("s", "max")


def test_the_kind_neutral_query_carries_no_none_value_replacement() -> None:
    # Hoisting the field onto the base would break no call site and pass
    # every other test here, while quietly undoing the split: a fragment
    # or allele request would carry a field that can only speak for an
    # uncovered position, which neither kind has.
    assert [f.name for f in fields(ScoreAggregationQuery)] == [
        "score", "aggregator",
    ]


def test_the_position_query_still_binds_three_positional_arguments() -> None:
    # The whole reason splitting a base type out needed no migration: the
    # subclass's own field lands third, exactly where it was on the flat
    # dataclass, so every positional call site keeps its meaning.  If this
    # breaks, callers do not fail -- they silently bind the wrong field.
    query = PositionScoreAggregationQuery("s", "max", 0.0)

    assert (
        query.score, query.aggregator, query.none_value_replacement,
    ) == ("s", "max", 0.0)


def test_numeric_aggregators_appear_first_in_dict() -> None:
    keys = list(AGGREGATOR_CLASS_DICT.keys())
    numeric_indices = [keys.index(a) for a in NUMERIC_ONLY_AGGREGATORS]
    non_numeric_indices = [
        i for i, k in enumerate(keys) if k not in NUMERIC_ONLY_AGGREGATORS
    ]
    assert max(numeric_indices) < min(non_numeric_indices)
