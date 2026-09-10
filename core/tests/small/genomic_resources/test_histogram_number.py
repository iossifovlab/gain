# pylint: disable=W0621,C0114,C0116,W0212,W0613
import logging
import pathlib
import re
from decimal import Decimal
from typing import Any

import numpy as np
import pytest
import yaml
from gain.genomic_resources.histogram import (
    NullHistogram,
    NullHistogramConfig,
    NumberHistogram,
    NumberHistogramConfig,
    build_histogram_config,
    load_histogram,
)
from gain.genomic_resources.testing.builders import a_position_score


def _a_number_histogram() -> NumberHistogram:
    return NumberHistogram(NumberHistogramConfig.from_dict({
        "type": "number",
        "view_range": {"min": 0, "max": 10},
        "number_of_bins": 10,
        "x_log_scale": False,
        "y_log_scale": False,
    }))


def test_number_histogram_refuses_text_with_its_own_message() -> None:
    """The refusal names the value and its type; numpy's names neither.

    The guard was written for exactly this type and could never fire for it:
    the ``np.isnan`` above it raised first, so a reader of a nullified
    score's reason got ``ufunc 'isnan' not supported`` (gain#1312).
    """
    histogram = _a_number_histogram()

    with pytest.raises(TypeError, match=r"non numerical value.*aaa.*str"):
        histogram.add_value("aaa")  # type: ignore[arg-type]


def test_number_histogram_still_skips_none_after_stating_the_refusal() -> None:
    """``None`` is an NA cell, and must not become a raise.

    Fixing gain#1312 by hoisting the existing isinstance check above the
    ``None``/nan skip would do exactly that -- ``None`` is in no allow-list.
    """
    histogram = _a_number_histogram()

    histogram.add_value(None)

    assert histogram.bars.sum() == 0


def test_number_histogram_still_skips_nan_after_stating_the_refusal() -> None:
    """A nan is a non-value here, not a contract breach."""
    histogram = _a_number_histogram()

    histogram.add_value(np.nan)

    assert histogram.bars.sum() == 0


@pytest.mark.parametrize(
    "value", [np.float32(0.5), np.float16(0.5), np.int64(3)])
def test_number_histogram_folds_a_numpy_scalar_as_its_python_value(
    value: Any,
) -> None:
    """numpy's own scalars fold as the Python value they hold.

    ``np.float32`` and ``np.float16`` are not ``float`` -- only
    ``np.float64`` subclasses it -- so the allow-list below the nan skip
    refused them as non-numeric and nullified the score, while the same
    class's ``add_batch`` folded them happily (gain#1338).  ``np.int64`` is
    here as the one numpy scalar that allow-list already accepted: the
    normalization must not disturb it.
    """
    from_numpy = _a_number_histogram()
    from_python = _a_number_histogram()

    from_numpy.add_value(value)
    from_python.add_value(value.item())

    assert from_python.bars.sum() == 1, "two empty histograms match trivially"
    assert np.array_equal(from_numpy.bars, from_python.bars)
    assert from_numpy.min_value == from_python.min_value
    assert from_numpy.max_value == from_python.max_value


def test_number_histogram_folds_numpy_bool_as_zero_and_one() -> None:
    """``np.bool_`` is a number to numpy, and folds as the 0/1 it holds.

    ``np.bool_`` is not an ``np.integer``, so the allow-list refused it --
    while ``_NUMBER_HISTOGRAM_VALUE_TYPES`` admits a ``"bool"`` score and
    ``add_batch`` bins one as 0/1 (gain#1338).
    """
    histogram = _a_number_histogram()
    numpy_true, numpy_false = np.bool_(1), np.bool_(0)

    histogram.add_value(numpy_true)
    histogram.add_value(numpy_false)

    assert histogram.bars[1] == 1, "True folds as 1"
    assert histogram.bars[0] == 1, "False folds as 0"
    assert histogram.min_value == 0
    assert histogram.max_value == 1


@pytest.mark.parametrize(
    ("value", "type_name"),
    [
        # Refused by the type gate: ``np.isnan`` accepts all of these.
        (0.5 + 0j, "complex"),
        (np.complex128(0.5), "numpy.complex128"),
        (np.array(0.5), "numpy.ndarray"),
        # A 1-element array, unlike the 2-element one that raises ValueError
        # from the truthiness of ``np.isnan(...)`` above and escapes the
        # scan's per-value ``except TypeError`` -- that is gain#1337, and it
        # is this line whoever fixes it will be editing.
        (np.array([0.5]), "numpy.ndarray"),
        # Refused earlier, by the ``np.isnan`` re-wording (gain#1312).
        # Here to pin that BOTH refusal routes carry the shared wording.
        (np.str_("aaa"), "numpy.str_"),
        (Decimal("0.5"), "decimal.Decimal"),
    ],
)
def test_number_histogram_refuses_a_non_number_naming_what_it_was_given(
    value: Any, type_name: str,
) -> None:
    """Admitting numpy's numeric scalars must not admit everything numpy has.

    This is the boundary gain#1338 had to hold.  Dropping the type check in
    favour of "whatever ``np.isnan`` accepts" -- the twin ``MinMaxValue``'s
    rule -- would let ``np.complex128`` through to be binned by its REAL
    PART alone (numpy only warns), and would fold a 0-d array.

    ``np.complex128`` is the row that pins the wording of the refusal and
    not just its existence: it is the only value here that is normalized
    before being refused, so it is the one that catches a refusal reporting
    the ``complex`` it became instead of the ``np.complex128`` the caller
    handed over.
    """
    histogram = _a_number_histogram()

    with pytest.raises(
        TypeError,
        match=rf"non numerical value.*{re.escape(type_name)}",
    ):
        histogram.add_value(value)

    assert histogram.bars.sum() == 0, "a refused value folds nothing"


def test_histogram_simple_input() -> None:
    config = NumberHistogramConfig.from_dict({
        "type": "number",
        "view_range": {"min": 0, "max": 10},
        "number_of_bins": 10,
        "x_log_scale": False,
        "y_log_scale": True,
    })
    assert config.y_log_scale

    hist = NumberHistogram(config)
    assert (hist.bins == np.arange(0, 11)).all()

    hist.add_value(0)
    assert (hist.bars == np.array([1, 0, 0, 0, 0, 0, 0, 0, 0, 0])).all()

    for i in range(1, 11):
        hist.add_value(i)
    assert (hist.bars == np.array([1, 1, 1, 1, 1, 1, 1, 1, 1, 2])).all()

    hist.add_value(12)
    hist.add_value(-1)
    assert hist.out_of_range_bins == [1, 1]
    assert hist.min_value == -1
    assert hist.max_value == 12


def test_histogram_simple_input2() -> None:
    config = NumberHistogramConfig.from_dict({
        "type": "number",
        "view_range": {"min": 1, "max": 11},
        "number_of_bins": 10,
        "x_log_scale": False,
        "y_log_scale": True,
    })
    assert config.y_log_scale

    hist = NumberHistogram(config)
    assert (hist.bins == np.arange(1, 12)).all()

    hist.add_value(1)
    assert (hist.bars == np.array([1, 0, 0, 0, 0, 0, 0, 0, 0, 0])).all()

    for i in range(2, 12):
        hist.add_value(i)
    assert (hist.bars == np.array([1, 1, 1, 1, 1, 1, 1, 1, 1, 2])).all()

    hist.add_value(13)
    hist.add_value(0)
    assert hist.out_of_range_bins == [1, 1]
    assert hist.min_value == 0
    assert hist.max_value == 13


def test_histogram_choose_bin_lin() -> None:
    config = NumberHistogramConfig.from_dict({
        "type": "number",
        "view_range": {"min": 1, "max": 11},
        "number_of_bins": 10,
        "x_log_scale": False,
        "y_log_scale": True,
    })
    assert config.y_log_scale

    hist = NumberHistogram(config)

    assert hist.choose_bin_lin(1.0) == 0
    assert hist.choose_bin_lin(2.0) == 1
    assert hist.choose_bin_lin(10.0) == 9

    assert hist.choose_bin_lin(12.0) == -1
    assert hist.choose_bin_lin(0.5) == -2

    assert hist.choose_bin_lin(11.0) == 9


def test_histogram_log_scale() -> None:
    config = NumberHistogramConfig.from_dict({
        "type": "number",
        "view_range": {"min": 0, "max": 1000},
        "number_of_bins": 4,
        "x_log_scale": True,
        "y_log_scale": False,
        "x_min_log": 1,
    })
    hist = NumberHistogram(config)
    assert (hist.bins == np.array([0, 1, 10, 100, 1000])).all()

    hist.add_value(0)
    assert (hist.bars == np.array([1, 0, 0, 0])).all()

    for i in [0.5, 2, 10, 200]:
        hist.add_value(i)
    assert (hist.bars == np.array([2, 1, 1, 1])).all()

    hist.add_value(2000)
    hist.add_value(-1)
    assert hist.out_of_range_bins == [1, 1]
    assert hist.min_value == -1
    assert hist.max_value == 2000


def test_histogram_choose_bin_log() -> None:
    config = NumberHistogramConfig.from_dict({
        "type": "number",
        "view_range": {"min": 0, "max": 1000},
        "number_of_bins": 4,
        "x_log_scale": True,
        "y_log_scale": False,
        "x_min_log": 1,
    })
    hist = NumberHistogram(config)
    assert (hist.bins == np.array([0, 1, 10, 100, 1000])).all()

    assert hist.choose_bin_log(-1) == -2
    assert hist.choose_bin_log(1001) == -1

    assert hist.choose_bin_log(0) == 0
    assert hist.choose_bin_log(0.5) == 0

    assert hist.choose_bin_log(1.0) == 1
    assert hist.choose_bin_log(10.0) == 2
    assert hist.choose_bin_log(20.0) == 2
    assert hist.choose_bin_log(99.999) == 2

    assert hist.choose_bin_log(100.0) == 3
    assert hist.choose_bin_log(200.0) == 3
    assert hist.choose_bin_log(500.0) == 3
    assert hist.choose_bin_log(999.0) == 3
    assert hist.choose_bin_log(999.999) == 3

    assert hist.choose_bin_log(1000) == 3


def test_histogram_choose_bin_log2() -> None:
    config = NumberHistogramConfig.from_dict({
        "type": "number",
        "view_range": {"min": 0, "max": 1000},
        "number_of_bins": 5,
        "x_log_scale": True,
        "y_log_scale": False,
        "x_min_log": 0.1,
    })
    hist = NumberHistogram(config)
    assert (hist.bins == np.array([0, 0.1, 1, 10, 100, 1000])).all()

    assert hist.choose_bin_log(-1) == -2
    assert hist.choose_bin_log(1001) == -1

    assert hist.choose_bin_log(0) == 0
    assert hist.choose_bin_log(0.05) == 0

    assert hist.choose_bin_log(0.1) == 1
    assert hist.choose_bin_log(0.5) == 1
    assert hist.choose_bin_log(0.9999) == 1

    assert hist.choose_bin_log(1.0) == 2

    assert hist.choose_bin_log(10.0) == 3
    assert hist.choose_bin_log(20.0) == 3
    assert hist.choose_bin_log(99.999) == 3

    assert hist.choose_bin_log(100.0) == 4
    assert hist.choose_bin_log(200.0) == 4
    assert hist.choose_bin_log(500.0) == 4
    assert hist.choose_bin_log(999.0) == 4
    assert hist.choose_bin_log(999.999) == 4

    assert hist.choose_bin_log(1000) == 4


def test_histogram_merge() -> None:
    config = NumberHistogramConfig.from_dict({
        "type": "number",
        "view_range": {"min": 0, "max": 10},
        "number_of_bins": 10,
        "x_log_scale": False,
        "y_log_scale": False,
    })
    hist1 = NumberHistogram(
        config,
        bins=np.arange(0, 11),
        bars=np.array([0, 0, 0, 1, 0, 0, 0, 1, 0, 2]),
    )
    hist2 = NumberHistogram(
        config,
        bins=np.arange(0, 11),
        bars=np.array([0, 0, 0, 0, 0, 1, 0, 1, 0, 0]),
    )

    hist1.merge(hist2)
    assert (hist1.bars == np.array([0, 0, 0, 1, 0, 1, 0, 2, 0, 2])).all()


def test_histogram_serialize_deserialize() -> None:
    config = NumberHistogramConfig.from_dict({
        "type": "number",
        "view_range": {"min": 0, "max": 10},
        "number_of_bins": 10,
        "x_log_scale": False,
        "y_log_scale": False,
    })
    hist1 = NumberHistogram(
        config,
        bins=np.arange(0, 11),
        bars=np.array([0, 0, 0, 1, 0, 0, 0, 1, 0, 2]),
    )

    serialized = hist1.serialize()
    print(serialized)

    loaded = yaml.safe_load(serialized)
    print(loaded)
    assert loaded["bins"] == [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert loaded["bars"] == [0, 0, 0, 1, 0, 0, 0, 1, 0, 2]

    hist2 = NumberHistogram.deserialize(serialized)

    assert hist1.bins is not None
    assert hist1.bars is not None
    assert hist2.bins is not None
    assert hist2.bars is not None
    assert np.array_equal(hist2.bins, hist1.bins)
    assert np.array_equal(hist2.bars, hist1.bars)


@pytest.mark.parametrize("conf", [
    {
        "histogram": {"type": "number"},
    },
])
def test_build_number_histogram_config(conf: dict[str, Any]) -> None:
    hist_conf = build_histogram_config(conf)
    assert isinstance(hist_conf, NumberHistogramConfig)


def test_build_histogram_config_without_type_is_not_a_key_error() -> None:
    # Validated call sites can no longer reach this, but a direct caller
    # passing a histogram block with no type must get a null config carrying
    # a reason -- not a bare KeyError.
    hist_conf = build_histogram_config({"histogram": {}})

    assert isinstance(hist_conf, NullHistogramConfig)
    assert "type" in hist_conf.reason


def test_load_histogram_missing_file_warns_without_traceback(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # gain#364: the missing-histogram case is HANDLED (a null histogram is
    # returned), so its traceback is noise -- and during a repair run it was
    # the only traceback shown, pointing away from the actual fault.  It
    # stays at WARNING, not DEBUG, so a missing histogram on an otherwise
    # healthy resource remains visible.
    resource = a_position_score().build_resource(tmp_path)

    with caplog.at_level(
            logging.DEBUG, logger="gain.genomic_resources.histogram"):
        histogram = load_histogram(
            resource, "statistics/histogram_score.json")

    assert isinstance(histogram, NullHistogram)
    records = [
        record for record in caplog.records
        if "unable to load histogram file" in record.getMessage()
    ]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert records[0].exc_info is None
