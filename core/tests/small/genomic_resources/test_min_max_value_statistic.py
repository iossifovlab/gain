import numpy as np
import pytest
from gain.genomic_resources.statistics.min_max import (
    MinMaxValue,
    MinMaxValueStatisticMixin,
)


def test_min_max_value_refuses_text_naming_the_value_and_its_type() -> None:
    """The reducer states its numeric contract, as its histogram twin does.

    Without the statement the caller is handed numpy's ``ufunc 'isnan' not
    supported`` instead, which names neither the value nor its type
    (gain#1312's complaint, on gain#1313's side of the pair).
    """
    min_max_value = MinMaxValue("test_score")

    with pytest.raises(TypeError, match=r"non numerical value.*aaa.*str"):
        min_max_value.add_value("aaa")  # type: ignore[arg-type]


def test_min_max_value_still_skips_none_after_the_refusal_is_stated() -> None:
    """``None`` is the normal shape of an NA cell, not a contract breach.

    Stating the refusal by widening a type check instead of excluding text
    would turn every NA cell into a raise.
    """
    min_max_value = MinMaxValue("test_score", 5, 10)

    min_max_value.add_value(None)

    assert (min_max_value.min, min_max_value.max) == (5, 10)


@pytest.mark.parametrize("value", [
    np.float32(3.0), np.float64(3.0), np.True_, True, 3,
])
def test_min_max_value_folds_every_numeric_flavour(value: object) -> None:
    """Only TEXT is refused -- numpy's numeric scalars still fold.

    ``np.float32`` is not a ``float`` and ``np.bool_`` is not an
    ``np.integer``, so an isinstance INCLUSION list stating the contract here
    would silently stop folding them.
    """
    min_max_value = MinMaxValue("test_score", 5, 10)

    min_max_value.add_value(value)  # type: ignore[arg-type]

    assert min_max_value.min == value


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


def test_min_max_value_deserialize_ignores_an_unknown_key() -> None:
    """An unrecognised key in a serialized min/max is ignored, not fatal."""
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
