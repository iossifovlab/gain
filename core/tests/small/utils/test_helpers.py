# pylint: disable=C0114,C0116
import math

import numpy as np
import pytest
from gain.utils.helpers import convert_size, isnan, str2bool


def test_str2bool() -> None:

    assert str2bool("True")
    assert not str2bool("False")


def test_str2bool_none_is_false() -> None:
    assert str2bool(None) is False


@pytest.mark.parametrize("value", [True, False])
def test_str2bool_passes_a_bool_through(value: bool) -> None:
    # The annotation of str2bool omits bool, but its isinstance branch
    # returns a bool unchanged, and this test covers that branch.
    assert str2bool(value) is value  # type: ignore[arg-type]


@pytest.mark.parametrize("value", ["yes", "YES", "1", "1.0", "t", "Y"])
def test_str2bool_true_spellings(value: str) -> None:
    assert str2bool(value) is True


@pytest.mark.parametrize("value", ["no", "0", "f", "", "truthy"])
def test_str2bool_other_strings_are_false(value: str) -> None:
    assert str2bool(value) is False


@pytest.mark.parametrize("value", [None, math.nan, np.float64("nan")])
def test_isnan_none_and_nan_are_nan(value: float | None) -> None:
    assert isnan(value)


@pytest.mark.parametrize("value", [0.0, 1.5, -2.0, math.inf])
def test_isnan_a_number_is_not_nan(value: float) -> None:
    assert not isnan(value)


@pytest.mark.parametrize("size_bytes, expected", [
    (0, "0B"),
    (1, "1.0 B"),
    (1023, "1023.0 B"),
    (1024, "1.0 KB"),
    (1536, "1.5 KB"),
    (1024 ** 2, "1.0 MB"),
    (5 * 1024 ** 3 + 1024 ** 3 // 3, "5.33 GB"),
    (1024 ** 8, "1.0 YB"),
])
def test_convert_size(size_bytes: int, expected: str) -> None:
    assert convert_size(size_bytes) == expected
