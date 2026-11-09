# pylint: disable=C0114,C0116
from typing import Any

import numpy as np
import pytest
from gain.utils.stringify import stringify


@pytest.mark.parametrize("vcf, expected", [(False, ""), (True, ".")])
def test_stringify_none_is_the_missing_marker_of_the_sink(
    vcf: bool, expected: str,
) -> None:
    assert stringify(None, vcf=vcf) == expected


@pytest.mark.parametrize("vcf, expected", [(False, ""), (True, ".")])
def test_stringify_empty_string_is_dot_only_in_a_vcf(
    vcf: bool, expected: str,
) -> None:
    assert stringify("", vcf=vcf) == expected


@pytest.mark.parametrize("vcf", [False, True])
def test_stringify_bool_spells_yes_and_no(
    vcf: bool,
) -> None:
    assert stringify(True, vcf=vcf) == "yes"  # ruff: ignore[boolean-positional-value-in-call]
    assert stringify(False, vcf=vcf) == "no"  # ruff: ignore[boolean-positional-value-in-call]


@pytest.mark.parametrize("value, expected", [
    (99.123, "99.1"),
    (0.0001234, "0.000123"),
    (-500.0, "-500"),
    (100.5, "100.5"),
    (123.456789, "123.457"),
    (99999.94, "99999.9"),
    (100000.0, "1e+05"),
    (np.float32(1234.5), "1234.5"),
    (np.float64(100000.0), "1e+05"),
])
def test_stringify_float_keeps_six_digits_only_from_100_to_100000(
    value: float, expected: str,
) -> None:
    assert stringify(value) == expected


@pytest.mark.parametrize("value, expected", [
    (3, "3"),
    (123456, "123456"),
    ("text", "text"),
])
def test_stringify_other_values_use_str(value: Any, expected: str) -> None:
    assert stringify(value) == expected
    assert stringify(value, vcf=True) == expected


@pytest.mark.parametrize("value, expected", [
    (["a", 1], "['a', 1]"),
    (("a", 1), "['a', 1]"),
    ([], "[]"),
])
def test_stringify_list_and_tuple_render_as_a_list(
    value: list | tuple, expected: str,
) -> None:
    assert stringify(value) == expected


@pytest.mark.parametrize("value", [["a", 1], ("a", 1)])
def test_stringify_list_and_tuple_are_url_quoted_in_a_vcf(
    value: list | tuple,
) -> None:
    assert stringify(value, vcf=True) == "%5B%27a%27%2C%201%5D"


def test_stringify_dict_joins_its_stringified_items() -> None:
    value = {"a": 1, "b": True, "c": None, 4: 1234.5}
    assert stringify(value) == "a:1;b:yes;c:;4:1234.5"


def test_stringify_dict_is_url_quoted_in_a_vcf() -> None:
    assert stringify({"a": 1, "b": None}, vcf=True) == \
        "%7B%27a%27%3A%201%2C%20%27b%27%3A%20None%7D"
