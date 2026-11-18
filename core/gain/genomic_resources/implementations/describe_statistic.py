"""The describe statistic of a table, the same on every machine (gain#2439).

``DataFrame.describe`` sums with float64 additions whose order depends on
the SIMD path of the CPU, so the last digits of ``mean`` and ``std`` differ
between machines.  :func:`stable_describe` replaces those two rows with
exact sums, rounded once.  The data_frame and ann_data implementations
share these helpers.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from gain.genomic_resources.statistics.moments import _ExactSum


def _exact_sum(terms: np.ndarray) -> float:
    total = _ExactSum()
    total.add_array(terms)
    return total.value()


def _mean_and_std(values: np.ndarray) -> tuple[float, float]:
    """The mean and the sample standard deviation of float64 values."""
    count = len(values)
    if count == 0:
        return math.nan, math.nan
    mean = _exact_sum(values) / count
    if count == 1:
        return mean, math.nan
    deviations = (values - mean) ** 2
    return mean, math.sqrt(_exact_sum(deviations) / (count - 1))


def _as_time(
    value: float, unit: str, kind: str, tz: Any = None,
) -> pd.Timestamp | pd.Timedelta | Any:
    """The float ``value``, rounded to whole ticks of ``unit``.

    ``kind`` is ``"M8"`` for a timestamp and ``"m8"`` for a timedelta.
    A nan gives ``NaT``.
    """
    if math.isnan(value):
        return pd.NaT
    ticks = np.int64(round(value)).astype(f"{kind}[{unit}]")
    if kind == "m8":
        return pd.Timedelta(ticks)
    timestamp = pd.Timestamp(ticks)
    if tz is not None:
        timestamp = timestamp.tz_localize("UTC").tz_convert(tz)
    return timestamp


def _describe_time_column(
    description: pd.DataFrame, name: Any, column: pd.Series,
) -> None:
    """Set the mean, and the std of a timedelta, from the int64 ticks."""
    unit = column.dt.unit
    ticks = column.dropna().array.asi8.astype(np.float64)
    mean, std = _mean_and_std(ticks)
    if pd.api.types.is_timedelta64_dtype(column.dtype):
        description.loc["mean", name] = _as_time(mean, unit, "m8")
        description.loc["std", name] = _as_time(std, unit, "m8")
        return
    description.loc["mean", name] = _as_time(
        mean, unit, "M8", getattr(column.dtype, "tz", None))


def stable_describe(frame: pd.DataFrame) -> pd.DataFrame:
    """Return ``frame.describe(include="all")`` with a stable mean and std.

    The ``mean`` and ``std`` rows come from exact sums over the non-NA
    values of each column, so they do not depend on the order of the rows
    or on the CPU.  All other rows stay as pandas computes them.
    """
    description = frame.describe(include="all")
    for name, column in frame.items():
        if pd.api.types.is_bool_dtype(column.dtype):
            continue
        if pd.api.types.is_datetime64_any_dtype(column.dtype) or \
                pd.api.types.is_timedelta64_dtype(column.dtype):
            _describe_time_column(description, name, column)
            continue
        if pd.api.types.is_numeric_dtype(column.dtype):
            values = column.dropna().to_numpy(dtype=np.float64)
            mean, std = _mean_and_std(values)
            description.loc["mean", name] = mean
            description.loc["std", name] = std
    return description


#: Whole numbers below this magnitude print as integers: every one of
#: them is exact in a double.
_WHOLE_NUMBER_LIMIT = 1e15


def format_describe_float(value: float) -> str:
    """Format one float of a describe statistic for its summary page.

    A finite whole number below ``1e15`` in magnitude prints as an
    integer, so counts and genomic positions stay readable.  Any other
    value prints with six significant digits.
    """
    value = float(value)
    if math.isfinite(value) and value.is_integer() \
            and abs(value) < _WHOLE_NUMBER_LIMIT:
        return str(int(value))
    return f"{value:.6g}"
