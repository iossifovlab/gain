# pylint: disable=C0114,C0116
from gain import logging
from gain.utils.log_levels import TRACE
from gain.utils.verbosity_configuration import VerbosityConfiguration


def test_verbosity_zero_is_warning() -> None:
    assert VerbosityConfiguration.verbosity(0) == logging.WARNING


def test_verbosity_one_is_info() -> None:
    assert VerbosityConfiguration.verbosity(1) == logging.INFO


def test_verbosity_two_is_debug() -> None:
    assert VerbosityConfiguration.verbosity(2) == logging.DEBUG


def test_verbosity_three_is_trace() -> None:
    assert VerbosityConfiguration.verbosity(3) == TRACE


def test_verbosity_above_three_stays_trace() -> None:
    assert VerbosityConfiguration.verbosity(4) == TRACE
