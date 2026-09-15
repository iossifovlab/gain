"""``HistogramError`` is in ``RESOURCE_ERRORS``, and this is what says so.

The classes in the tuple are raised elsewhere and only *named* there, so
without this a class could drop out of it and every test would stay
green (gain#1293).
"""
# pylint: disable=C0116
import pytest
from gain import logging
from gain.genomic_resources.cli_errors import report_resource_failure
from gain.genomic_resources.resource_errors import HistogramError


def test_a_histogram_error_is_reported_as_one_line_naming_the_resource(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.ERROR, logger="grr_manage"):
        report_resource_failure(
            HistogramError("histogram_cell is not pulled"),
            "compute statistics", "one/score")

    [record] = caplog.records
    assert record.getMessage() == (
        "compute statistics <one/score>: histogram_cell is not pulled")
    assert record.exc_info is None, (
        "the one-line tier carries no traceback at ERROR")
