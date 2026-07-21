# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Weighted aggregation: ``add(value, count)`` weights the value (#260)."""

from fractions import Fraction

import pytest
from gain.genomic_resources.aggregators import (
    Aggregator,
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
)


def test_mean_aggregator_weights_the_value() -> None:
    agg = MeanAggregator()
    agg.add(2.0, 3)
    agg.add(1.0, 1)

    assert agg.get_final() == 1.75
    assert agg.get_used_count() == 4
    assert agg.get_total_count() == 4


@pytest.mark.parametrize("aggregator_class,expected", [
    (MinAggregator, 1.0),
    (MaxAggregator, 2.0),
])
def test_min_and_max_ignore_the_weight_but_count_it(
    aggregator_class: type[Aggregator], expected: float,
) -> None:
    agg = aggregator_class()
    agg.add(2.0, 3)
    agg.add(1.0, 1)

    assert agg.get_final() == expected
    assert agg.get_used_count() == 4
    assert agg.get_total_count() == 4


def test_count_aggregator_counts_the_weight() -> None:
    agg = CountAggregator()
    agg.add(0.1, 7)
    agg.add(0.2, 3)

    assert agg.get_final() == 10


def test_mode_aggregator_tallies_the_weight() -> None:
    agg = ModeAggregator()
    agg.add("a", 1)
    agg.add("b", 1)
    agg.add("a", 1)
    agg.add("b", 5)

    assert agg.get_final() == "b"
    assert agg.get_used_count() == 8


def test_counter_aggregator_tallies_the_weight() -> None:
    agg = CounterAggregator()
    agg.add("pathogenic", 4)
    agg.add("benign", 2)
    agg.add("pathogenic", 1)

    assert agg.get_final() == {"pathogenic": 5, "benign": 2}


def test_counter_aggregator_tallies_the_weight_of_every_list_item() -> None:
    agg = CounterAggregator()
    agg.add(["a", "b"], 3)
    agg.add(["b"], 1)

    assert agg.get_final() == {"a": 3, "b": 4}


def test_bool_aggregator_ignores_the_weight_but_counts_it() -> None:
    agg = BoolAggregator()
    agg.add(None, 5)
    assert agg.get_final() is False

    agg.add("g1", 3)
    assert agg.get_final() is True
    assert agg.get_used_count() == 3
    assert agg.get_total_count() == 8


def test_median_aggregator_weighted_even_total() -> None:
    agg = MedianAggregator()
    agg.add(1.0, 3)
    agg.add(2.0, 1)

    assert agg.get_final() == 1.0
    assert agg.get_used_count() == 4


def test_median_aggregator_weighted_odd_total() -> None:
    agg = MedianAggregator()
    agg.add(1.0, 1)
    agg.add(2.0, 2)

    assert agg.get_final() == 2.0


def test_median_aggregator_weighted_out_of_order_values() -> None:
    agg = MedianAggregator()
    agg.add(5.0, 2)
    agg.add(1.0, 3)
    agg.add(3.0, 2)

    assert agg.get_final() == 3.0


def test_median_aggregator_weighted_strings() -> None:
    agg = MedianAggregator()
    agg.add("b", 2)
    agg.add("a", 2)

    assert agg.get_final() == "ab"


def test_list_aggregator_expands_the_weight_in_order() -> None:
    agg = ListAggregator()
    agg.add(1.0, 2)
    agg.add(2.0, 1)
    agg.add(3.0, 3)

    assert agg.get_final() == [1.0, 1.0, 2.0, 3.0, 3.0, 3.0]
    assert agg.get_used_count() == 6


def test_join_aggregator_expands_the_weight_in_order() -> None:
    agg = JoinAggregator(", ")
    agg.add("a", 2)
    agg.add("b", 1)

    assert agg.get_final() == "a, a, b"


def test_concat_aggregator_expands_the_weight_in_order() -> None:
    agg = ConcatAggregator()
    agg.add("a", 2)
    agg.add("b", 1)

    assert agg.get_final() == "aab"


# One record per line, as the score layer sees them: (value, weight).
_RECORDS = [(0.1, 3), (0.25, 5), (0.1, 2)]
_REPLICATED = [value for value, weight in _RECORDS for _ in range(weight)]


@pytest.mark.parametrize("aggregator", [
    "min", "max", "count", "median", "mode", "value_count",
    "list", "join(;)", "concatenate", "bool",
])
def test_weighted_aggregation_is_bit_identical_to_replication(
    aggregator: str,
) -> None:
    weighted = Aggregator.build(aggregator).aggregate_weighted(_RECORDS)
    replicated = Aggregator.build(aggregator).aggregate(_REPLICATED)

    assert weighted == replicated
    assert repr(weighted) == repr(replicated)


# A 500 kb region backed by three records, as the issue quotes it.
_LARGE_REGION_RECORDS = [(0.51, 200_000), (0.51, 200_000), (0.51, 100_000)]


def test_weighted_mean_is_closer_to_the_exact_mean_than_replication() -> None:
    """Weighting rounds once per record, replication once per base pair."""
    total_weight = sum(weight for _, weight in _LARGE_REGION_RECORDS)
    exact = sum(
        Fraction(value) * weight for value, weight in _LARGE_REGION_RECORDS
    ) / total_weight

    weighted = MeanAggregator().aggregate_weighted(_LARGE_REGION_RECORDS)
    replicated = MeanAggregator().aggregate([
        value
        for value, weight in _LARGE_REGION_RECORDS
        for _ in range(weight)
    ])

    assert abs(Fraction(weighted) - exact) \
        < abs(Fraction(replicated) - exact)
    # The two agree to well within any tolerance a consumer could care
    # about -- but they are *not* the same float, and this is the whole
    # of the observable change.
    assert weighted == pytest.approx(replicated, rel=1e-9)
    assert weighted != replicated
