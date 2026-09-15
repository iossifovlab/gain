import re
from decimal import Decimal
from typing import Any

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
    """Refusing non-numbers must not refuse numpy's numeric scalars.

    ``np.float32`` is not a ``float`` and ``np.bool_`` is not an
    ``np.integer``, so an isinstance allow-list checked BEFORE the numpy
    scalar is normalized would silently stop folding them -- the trap the
    histogram twin fell into (gain#1338).  The list this reducer keeps is
    checked after.
    """
    min_max_value = MinMaxValue("test_score", 5, 10)

    min_max_value.add_value(value)  # type: ignore[arg-type]

    assert min_max_value.min == value


@pytest.mark.parametrize(
    ("value", "python_type"),
    [
        (np.float32(3.0), float),
        (np.float16(3.0), float),
        (np.int64(3), int),
        (np.bool_(1), bool),
    ],
)
def test_min_max_value_folds_a_numpy_scalar_as_the_python_value_it_holds(
    value: Any, python_type: type,
) -> None:
    """The extremum is never left holding a numpy scalar.

    ``min(np.float32(3.0), 5)`` orders fine and hands back the float32, so
    without normalizing, ``min`` becomes whatever numpy type the caller
    used -- which then flows into a histogram's ``view_range`` and picks its
    bin edges in that precision.  The histogram twin folds ``value.item()``
    for exactly that reason (gain#1338); so does this.
    """
    min_max_value = MinMaxValue("test_score", 5, 10)

    min_max_value.add_value(value)

    # Exact type on purpose: ``np.float64`` SUBCLASSES ``float``, so an
    # isinstance check would pass with the numpy scalar still in place.
    assert type(min_max_value.min) is python_type  # pylint: disable=unidiomatic-typecheck


@pytest.mark.parametrize(
    ("value", "type_name"),
    [
        # Refused by the type gate: ``np.isnan`` accepts all of these, and
        # before the gate each one FOLDED -- ``min()`` orders a complex
        # without complaint and leaves ``min`` holding one, or an array
        # (gain#1358).  ``complex`` alone used to die in ``min()`` instead,
        # with numpy-free wording that named neither value nor type.
        (0.5 + 0j, "complex"),
        (np.complex128(0.5), "numpy.complex128"),
        (np.array(0.5), "numpy.ndarray"),
        # A 1-element sequence passes the nan skip (its ``np.isnan`` is a
        # 1-element array, which coerces to bool) and is refused here.
        (np.array([0.5]), "numpy.ndarray"),
        ([0.5], "list"),
        # Refused by the ``np.isnan`` re-wording, for its OTHER complaint: a
        # longer sequence's ``np.isnan`` is an array of ambiguous truth, and
        # that is ``ValueError`` (gain#1337).
        ([0.5, 1.5], "list"),
        ((0.5, 1.5), "tuple"),
        (np.array([0.5, 1.5]), "numpy.ndarray"),
        ([], "list"),
        # Refused earlier, by the ``np.isnan`` re-wording (gain#1313).
        # Here to pin that BOTH refusal routes carry the shared wording.
        (np.str_("aaa"), "numpy.str_"),
        (Decimal("0.5"), "decimal.Decimal"),
    ],
)
def test_min_max_value_refuses_a_non_number_naming_what_it_was_given(
    value: Any, type_name: str,
) -> None:
    """A value ``np.isnan`` accepts is not thereby a value a min/max folds.

    The mirror of the histogram twin's
    ``test_number_histogram_refuses_a_non_number_naming_what_it_was_given``,
    row for row: the shared wording promises a reader cannot tell which twin
    refused, so the two must refuse the same values.

    ``np.complex128`` is the row that pins the wording and not just the
    refusal: it is the only value here that is normalized before being
    refused, so it is the one that catches a refusal reporting the
    ``complex`` it became instead of the ``np.complex128`` the caller
    handed over.
    """
    min_max_value = MinMaxValue("test_score")

    with pytest.raises(
        TypeError,
        match=rf"non numerical value.*{re.escape(type_name)}",
    ):
        min_max_value.add_value(value)

    assert np.isnan(min_max_value.min), "a refused value folds nothing"
    assert np.isnan(min_max_value.max), "a refused value folds nothing"


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
