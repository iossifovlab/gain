import pytest
from gain.genomic_resources.statistics.min_max import (
    MinMaxValue,
    MinMaxValueStatisticMixin,
)


def test_min_max_value_add_value() -> None:
    """Test adding value to min max value statistic"""
    min_max_value = MinMaxValue("test_score", 5, 10)

    assert min_max_value.min == 5
    assert min_max_value.max == 10

    min_max_value.add_value(None)
    assert min_max_value.min == 5
    assert min_max_value.max == 10

    min_max_value.add_value(3)
    assert min_max_value.min == 3
    assert min_max_value.max == 10

    min_max_value.add_value(12)
    assert min_max_value.min == 3
    assert min_max_value.max == 12


def test_min_max_value_merge_with_min_max_statistics() -> None:
    """Test merging min max value statistic with another statistic"""
    min_max_value = MinMaxValue("test_score", 5, 10)
    other_min_max_value = MinMaxValue("test_score", 7, 15)
    min_max_value.merge(other_min_max_value)
    assert min_max_value.min == 5
    assert min_max_value.max == 15


def test_min_max_value_merge_with_different_scores() -> None:
    min_max_value = MinMaxValue("test_score", 5, 10)
    other_min_max_value = MinMaxValue("other_test_score", 7, 15)
    with pytest.raises(ValueError) as error_msg:
        min_max_value.merge(other_min_max_value)
    assert "different scores" in str(error_msg.value)


def test_min_max_value_serialize() -> None:
    """Test min max value serialize"""
    min_max_value = MinMaxValue("test_score", 5, 10)

    min_max_serialized = min_max_value.serialize()
    assert min_max_serialized == "max: 10\nmin: 5\nscore_id: test_score\n"


def test_min_max_value_deserialize() -> None:
    """Test min max value deserialize"""
    min_max_value = MinMaxValue.deserialize(
        "max: 10\nmin: 5\nscore_id: test_score\n",
    )

    assert min_max_value.score_id == "test_score"
    assert min_max_value.min == 5
    assert min_max_value.max == 10


def test_min_max_value_deserialize_ignores_a_legacy_count() -> None:
    """A fragment score's record count (gain#421) is no longer read back."""
    min_max_value = MinMaxValue.deserialize(
        "count: 7\nmax: 10\nmin: 5\nscore_id: test_score\n",
    )

    assert min_max_value.score_id == "test_score"
    assert min_max_value.min == 5
    assert min_max_value.max == 10
    assert not hasattr(min_max_value, "count")


def test_min_max_value_statistic_mixin() -> None:
    """Test min max value deserialize"""
    min_max_file = MinMaxValueStatisticMixin.get_min_max_file("test_score")
    assert min_max_file == "min_max_test_score.yaml"
