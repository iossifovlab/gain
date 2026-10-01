# pylint: disable=C0114,C0116
"""``sum`` and ``product``: type-preserving, numeric-only aggregators."""
import pathlib
from typing import Any

import pytest
from gain.genomic_resources.aggregators import (
    Aggregator,
    validate_aggregator,
)
from gain.genomic_resources.genomic_scores import (
    build_position_score_from_resource,
)
from gain.genomic_resources.testing.builders import a_position_score


def _fold(name: str, values: list[Any]) -> Any:
    agg = Aggregator.build(name)
    for value in values:
        agg.add(value)
    return agg.get_final()


def test_sum_of_ints_skips_none_and_stays_an_int() -> None:
    result = _fold("sum", [1, 2, None, 3])

    assert result == 6
    assert isinstance(result, int)


def test_product_of_ints_skips_none() -> None:
    result = _fold("product", [2, 3, None, 4])

    assert result == 24
    assert isinstance(result, int)


def test_sum_of_floats_is_a_float() -> None:
    result = _fold("sum", [0.5, 1.25, None])

    assert result == pytest.approx(1.75)
    assert isinstance(result, float)


def test_product_of_floats_is_a_float() -> None:
    result = _fold("product", [0.5, 3.0])

    assert result == pytest.approx(1.5)
    assert isinstance(result, float)


@pytest.mark.parametrize("name", ["sum", "product"])
@pytest.mark.parametrize("values", [[], [None, None]])
def test_nothing_but_none_gives_none(name: str, values: list[Any]) -> None:
    assert _fold(name, values) is None


@pytest.mark.parametrize("name", ["sum", "product"])
@pytest.mark.parametrize("value", [3, 2.5, -2])
def test_a_weighted_add_equals_that_many_single_adds(
    name: str, value: float,
) -> None:
    weighted = Aggregator.build(name)
    weighted.add(2)
    weighted.add(value, count=5)
    repeated = Aggregator.build(name)
    repeated.add(2)
    for _ in range(5):
        repeated.add(value)

    assert weighted.get_final() == pytest.approx(repeated.get_final())
    assert weighted.get_used_count() == repeated.get_used_count() == 6


@pytest.mark.parametrize("name", ["sum", "product"])
def test_the_output_type_is_the_input_type(name: str) -> None:
    assert Aggregator.resolve_class(name).output_value_type is None


def test_a_position_score_declaring_sum_folds_a_region_by_base_pair(
    tmp_path: pathlib.Path,
) -> None:
    resource = (
        a_position_score()
        .with_score("s", "int")
        .with_aggregator("sum")
        .with_data("""
            chrom  pos_begin  pos_end  s
            chr1   10         19       1
            chr1   20         29       2
        """)
        .build_resource(tmp_path)
    )
    score = build_position_score_from_resource(resource)

    with score.open() as opened:
        result = opened.get_score_in_region_agg("chr1", 15, 24, "s")

    assert result == 5 * 1 + 5 * 2
    assert isinstance(result, int)


@pytest.mark.parametrize("name", ["sum", "product"])
def test_a_str_score_is_refused(name: str) -> None:
    with pytest.raises(ValueError, match="requires a numeric value type"):
        validate_aggregator(name, "str")


@pytest.mark.parametrize("name", ["sum", "product"])
@pytest.mark.parametrize("value_type", ["int", "float"])
def test_a_numeric_score_is_accepted(name: str, value_type: str) -> None:
    validate_aggregator(name, value_type)
