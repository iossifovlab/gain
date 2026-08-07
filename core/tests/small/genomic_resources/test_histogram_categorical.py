# pylint: disable=W0621,C0114,C0116,W0212,W0613
import json
from typing import Any

import pytest
from gain.genomic_resources.histogram import (
    CategoricalHistogram,
    CategoricalHistogramConfig,
    HistogramError,
    build_histogram_config,
)


def test_categorical_histogram() -> None:
    config = CategoricalHistogramConfig(
        value_order=["value1", "value2", "value3"])

    hist = CategoricalHistogram(config)

    assert set(hist.display_values.keys()) == {"value1", "value2", "value3"}

    assert hist.display_values["value1"] == 0
    assert hist.display_values["value2"] == 0

    hist.add_value("value1")

    hist.add_value("value2")
    hist.add_value("value2")

    hist.add_value("value3")

    assert hist.display_values["value1"] == 1
    assert hist.display_values["value2"] == 2
    assert hist.display_values["value3"] == 1


def test_categorical_histogram_default_config_add_value_raises() -> None:
    config = CategoricalHistogramConfig.default_config()

    hist = CategoricalHistogram(config)
    assert not hist.enforce_type

    for i in range(100):
        hist.add_value(f"value{i}")
    with pytest.raises(HistogramError):
        hist.add_value("value100")


def test_categorical_histogram_add_value_does_not_raise() -> None:
    config = CategoricalHistogramConfig()

    hist = CategoricalHistogram(config)
    assert hist.enforce_type

    for i in range(100):
        hist.add_value(f"value{i}")
    hist.add_value("value100")


def test_categorical_histogram_merge() -> None:
    config = CategoricalHistogramConfig(
        value_order=["value1", "value2", "value3", "value4"])

    hist1 = CategoricalHistogram(config)

    hist2 = CategoricalHistogram(config)

    hist1.add_value("value1")
    hist1.add_value("value1")
    hist1.add_value("value2")
    hist1.add_value("value2")
    hist1.add_value("value4")

    hist2.add_value("value2")
    hist2.add_value("value2")
    hist2.add_value("value3")
    hist2.add_value("value3")

    assert hist1.display_values["value1"] == 2
    assert hist1.display_values["value2"] == 2
    assert hist1.display_values["value4"] == 1

    assert hist2.display_values["value1"] == 0
    assert hist2.display_values["value2"] == 2
    assert hist2.display_values["value3"] == 2

    hist1.merge(hist2)
    assert hist1.display_values["value1"] == 2
    assert hist1.display_values["value2"] == 4
    assert hist1.display_values["value3"] == 2
    assert hist1.display_values["value4"] == 1


def test_histogram_error_is_an_exception() -> None:
    """``HistogramError`` must derive from ``Exception`` (gain#465).

    As a ``BaseException`` it slipped past the task-graph executor's
    ``except Exception``, which is what converts a task's failure into the
    task's result; it escaped into the dask worker instead and the caller
    blocked in ``Future.result()`` forever.
    """
    assert issubclass(HistogramError, Exception)


def test_categorical_histogram_merge_raises() -> None:
    config = CategoricalHistogramConfig.default_config()

    hist1 = CategoricalHistogram(config)
    for i in range(50):
        hist1.add_value(f"value{i}")
    hist2 = CategoricalHistogram(config)
    for i in range(51):
        hist2.add_value(f"value{i + 50}")
    with pytest.raises(HistogramError):
        hist1.merge(hist2)


@pytest.mark.parametrize("conf", [
    {
        "histogram": {"type": "categorical"},
    },
])
def test_build_categorical_histogram_config(conf: dict[str, Any]) -> None:
    hist_conf = build_histogram_config(conf)
    assert isinstance(hist_conf, CategoricalHistogramConfig)


@pytest.mark.parametrize("value_order, expected_bars", [
    (None, {"1": 2, "2": 1}),
    (["2", "1"], {"2": 1, "1": 2}),
])
def test_categorical_histogram_values_order(
        value_order: list[str | int] | None,
        expected_bars: dict[str, int]) -> None:
    hist_conf = CategoricalHistogramConfig(value_order=value_order)
    hist = CategoricalHistogram(hist_conf)

    hist.add_value("2")
    hist.add_value("1")
    hist.add_value("1")

    assert hist.display_values == expected_bars


@pytest.mark.parametrize("value_order, expected_bars", [
    (None, {"1": 2, "2": 1}),
    (["2", "1"], {"2": 1, "1": 2}),
])
def test_categorical_histogram_merge_values_order(
        value_order: list[str | int] | None,
        expected_bars: dict[str, int]) -> None:
    hist_conf = CategoricalHistogramConfig(value_order=value_order)
    hist = CategoricalHistogram(hist_conf)

    hist.add_value("2")
    hist.add_value("1")

    hist2 = CategoricalHistogram(hist_conf)
    hist2.add_value("1")

    hist.merge(hist2)

    assert hist.display_values == expected_bars


@pytest.mark.parametrize("displayed_values_count, expected_bars", [
    (None, {"1": 1, "2": 1, "3": 1}),
    (3, {"1": 1, "2": 1, "3": 1}),
    (2, {"1": 1, "2": 1, "Other": 1}),
])
def test_categorical_histogram_number_of_displayed_values(
        displayed_values_count: int | None,
        expected_bars: dict[str, int]) -> None:
    hist_conf = CategoricalHistogramConfig(
        displayed_values_count=displayed_values_count)
    hist = CategoricalHistogram(hist_conf)

    hist.add_value("1")
    hist.add_value("2")
    hist.add_value("3")

    assert hist.display_values == expected_bars


def populate_categorical_histogram(hist: CategoricalHistogram) -> None:
    for i in range(1, 11):
        for _ in range(i * 10):
            hist.add_value(str(i))


@pytest.mark.parametrize("displayed_values_count, expected_bars", [
    (3, {"10": 100, "9": 90, "8": 80, "Other": 280}),
    (4, {"10": 100, "9": 90, "8": 80, "7": 70, "Other": 210}),
    (5, {"10": 100, "9": 90, "8": 80, "7": 70, "6": 60, "Other": 150}),
    (2, {"10": 100, "9": 90, "Other": 360}),
])
def test_categorical_histogram_number_of_displayed_values_populated(
        displayed_values_count: int | None,
        expected_bars: dict[str, int]) -> None:
    hist_conf = CategoricalHistogramConfig(
        displayed_values_count=displayed_values_count)
    hist = CategoricalHistogram(hist_conf)
    populate_categorical_histogram(hist)

    assert hist.display_values == expected_bars


def populate_categorical_histogram_with_int(
    hist: CategoricalHistogram,
) -> None:
    for i in range(1, 11):
        for _ in range(i * 10):
            hist.add_value(i)


@pytest.mark.parametrize("displayed_values_count, expected_bars", [
    (3, {10: 100, 9: 90, 8: 80, "Other": 280}),
])
def test_categorical_histogram_number_of_displayed_values_int_populated(
        displayed_values_count: int | None,
        expected_bars: dict[str, int]) -> None:
    hist_conf = CategoricalHistogramConfig(
        displayed_values_count=displayed_values_count)
    hist = CategoricalHistogram(hist_conf)
    populate_categorical_histogram_with_int(hist)

    assert hist.display_values == expected_bars


def a_big_categorical_histogram(
    unique_values: int = 150,
) -> CategoricalHistogram:
    """A histogram past UNIQUE_VALUES_LIMIT: value000..valueNNN, count i+1."""
    hist = CategoricalHistogram(
        CategoricalHistogramConfig(displayed_values_count=5))
    for i in range(unique_values):
        hist.add_value(f"value{i:03d}", count=i + 1)
    return hist


def test_truncated_sidecar_roundtrip_carries_totals() -> None:
    hist = a_big_categorical_histogram(unique_values=150)

    sidecar = CategoricalHistogram.deserialize(hist.serialize_truncated())

    assert sidecar.truncated
    assert sidecar.unique_values == 150
    assert sidecar.total_count == sum(range(1, 151))
    assert sidecar.raw_values == {
        "value149": 150,
        "value148": 149,
        "value147": 148,
        "value146": 147,
        "value145": 146,
    }
    assert sidecar.config == hist.config


@pytest.mark.parametrize("truncated_side", ["self", "other"])
def test_merging_a_truncated_histogram_raises(truncated_side: str) -> None:
    full = a_big_categorical_histogram()
    sidecar = CategoricalHistogram.deserialize(full.serialize_truncated())
    left, right = (sidecar, full) if truncated_side == "self" \
        else (full, sidecar)

    with pytest.raises(HistogramError, match="truncated"):
        left.merge(right)


def test_truncated_histogram_values_domain_reports_totals() -> None:
    hist = a_big_categorical_histogram(unique_values=150)
    sidecar = CategoricalHistogram.deserialize(hist.serialize_truncated())

    domain = sidecar.values_domain()

    assert "value149" in domain
    assert "top 5 of 150 values" in domain


def test_full_histogram_values_domain_is_unchanged() -> None:
    hist = a_big_categorical_histogram(unique_values=150)

    domain = hist.values_domain()

    assert "of 150 values" not in domain


def test_percent_config_sidecar_carries_the_displayed_values() -> None:
    config = CategoricalHistogramConfig(
        displayed_values_count=None, displayed_values_percent=95.0)
    hist = CategoricalHistogram(config)
    for i in range(150):
        hist.add_value(f"value{i:03d}", count=i + 1)

    sidecar = CategoricalHistogram.deserialize(hist.serialize_truncated())

    displayed_by_full = set(hist.display_values) - {"Other"}
    assert displayed_by_full <= set(sidecar.raw_values)


def test_truncated_histogram_display_values_are_not_recomputed() -> None:
    config = CategoricalHistogramConfig(
        displayed_values_count=None, displayed_values_percent=95.0)
    hist = CategoricalHistogram(config)
    for i in range(150):
        hist.add_value(f"value{i:03d}", count=i + 1)

    sidecar = CategoricalHistogram.deserialize(hist.serialize_truncated())

    assert sidecar.display_values == sidecar.raw_values


def test_value_order_config_sidecar_renders_like_the_full_histogram() -> None:
    order: list[str | int] = [f"value{i:03d}" for i in range(150)]
    config = CategoricalHistogramConfig(value_order=order)
    hist = CategoricalHistogram(config)
    for i in range(150):
        hist.add_value(f"value{i:03d}", count=i + 1)

    sidecar = CategoricalHistogram.deserialize(hist.serialize_truncated())

    assert sidecar.raw_values == hist.raw_values
    assert sidecar.display_values == hist.display_values
    assert sidecar.values_domain() == hist.values_domain()


def test_full_histogram_serialization_has_no_truncation_fields() -> None:
    hist = a_big_categorical_histogram()

    data = json.loads(hist.serialize())

    assert set(data) == {"config", "values"}
