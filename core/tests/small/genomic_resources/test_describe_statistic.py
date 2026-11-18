# pylint: disable=C0114,C0116
import math
from fractions import Fraction
from typing import Any

import numpy as np
import pandas as pd
import pytest
from gain.genomic_resources.implementations.describe_statistic import (
    format_describe_float,
    stable_describe,
)

ROWS = 500_000


def _values() -> np.ndarray:
    return np.random.default_rng(0).lognormal(10, 3, ROWS)


def _reordered(frame: pd.DataFrame) -> pd.DataFrame:
    order = np.random.default_rng(1).permutation(ROWS)
    return frame.iloc[order].reset_index(drop=True)


def _cell(description: pd.DataFrame, row: str) -> Any:
    """The ``row`` statistic of the one column, ``value``."""
    return description.loc[row, "value"]


def test_reordered_float_rows_give_the_same_describe_statistic() -> None:
    frame = pd.DataFrame({"value": _values()})
    shuffled = _reordered(frame)

    stable = (
        stable_describe(frame).to_csv(), stable_describe(shuffled).to_csv())
    plain = (
        frame.describe(include="all").to_csv(),
        shuffled.describe(include="all").to_csv())

    assert stable[0] == stable[1]
    assert plain[0] != plain[1]


@pytest.mark.parametrize("dtype", [
    "timedelta64[ns]", "datetime64[ns]", "datetime64[us]",
])
def test_reordered_time_rows_give_the_same_describe_statistic(
    dtype: str,
) -> None:
    ticks = (_values() * 1_000).astype(np.int64)
    frame = pd.DataFrame({"value": ticks.astype(dtype)})
    assert str(frame["value"].dtype) == dtype

    stable = stable_describe(frame).to_csv()

    assert stable_describe(_reordered(frame)).to_csv() == stable


CANCELLING_TICKS = np.array([2**60, 3000, -(2**60)], dtype=np.int64)


def test_timedelta_mean_and_std_come_from_exact_sums() -> None:
    frame = pd.DataFrame({"value": CANCELLING_TICKS.astype("m8[ns]")})

    description = stable_describe(frame)

    assert _cell(description, "mean") == pd.Timedelta(1000, unit="ns")
    deviations = (CANCELLING_TICKS.astype(np.float64) - 1000.0) ** 2
    expected_std = math.sqrt(math.fsum(deviations) / 2)
    assert _cell(description, "std") == pd.Timedelta(
        round(expected_std), unit="ns")


def test_datetime_mean_comes_from_an_exact_sum_in_the_column_unit() -> None:
    frame = pd.DataFrame({"value": CANCELLING_TICKS.astype("M8[us]")})

    mean = _cell(stable_describe(frame), "mean")

    assert mean == pd.Timestamp(np.datetime64(1000, "us"))
    assert mean.unit == "us"


def test_a_tz_aware_datetime_mean_keeps_its_time_zone() -> None:
    column = pd.Series(CANCELLING_TICKS.astype("M8[ns]")).dt.tz_localize(
        "UTC").dt.tz_convert("America/New_York")
    frame = pd.DataFrame({"value": column})

    mean = _cell(stable_describe(frame), "mean")

    assert mean == pd.Timestamp(1000, unit="ns", tz="UTC")
    assert str(mean.tz) == "America/New_York"


def test_an_all_na_float_column_has_no_mean_and_no_std() -> None:
    frame = pd.DataFrame({"value": [np.nan, np.nan]})

    description = stable_describe(frame)

    assert _cell(description, "count") == 0
    assert math.isnan(_cell(description, "mean"))
    assert math.isnan(_cell(description, "std"))


def test_a_one_row_column_has_no_std() -> None:
    frame = pd.DataFrame({"value": [3.5]})

    description = stable_describe(frame)

    assert _cell(description, "mean") == 3.5
    assert math.isnan(_cell(description, "std"))


def test_a_column_with_both_infinities_has_a_nan_mean() -> None:
    frame = pd.DataFrame({"value": [1.0, np.inf, -np.inf]})

    description = stable_describe(frame)

    assert math.isnan(_cell(description, "mean"))


@pytest.mark.parametrize("dtype", ["Int64", "Float64"])
def test_a_nullable_column_matches_its_float_values(dtype: str) -> None:
    nullable = pd.DataFrame({"value": pd.Series(
        [4, None, 7, 12, None], dtype=dtype)})
    assert _cell(stable_describe(nullable), "count") == 3
    floats = pd.DataFrame({"value": [4.0, 7.0, 12.0]})

    description = stable_describe(nullable)
    expected = stable_describe(floats)

    assert _cell(description, "mean") == _cell(expected, "mean")
    assert _cell(description, "std") == _cell(expected, "std")


def test_an_int_column_gets_the_exactly_rounded_mean() -> None:
    values = np.random.default_rng(2).integers(
        -(2**40), 2**40, 1_000, dtype=np.int64)
    frame = pd.DataFrame({"value": values})

    mean = _cell(stable_describe(frame), "mean")

    assert abs(int(values.sum())) < 2**53
    assert mean == float(Fraction(int(values.sum()), len(values)))


@pytest.mark.parametrize(("value", "expected"), [
    (248906440.0, "248906440"),
    (75802178.943001, "7.58022e+07"),
    (1e300, "1e+300"),
    (0.25, "0.25"),
])
def test_format_describe_float(value: float, expected: str) -> None:
    assert format_describe_float(value) == expected


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_format_describe_float_prints_non_finite_values(value: float) -> None:
    assert isinstance(format_describe_float(value), str)
