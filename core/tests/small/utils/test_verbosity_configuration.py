# pylint: disable=C0114,C0116,W0621
import argparse
from collections.abc import Callable, Iterator

import pytest
from gain import logging
from gain.effect_annotation.effect_checkers import coding
from gain.utils.log_levels import TRACE
from gain.utils.verbosity_configuration import VerbosityConfiguration


@pytest.fixture
def configure_cli_verbosity(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[[int], None]]:
    """Run ``VerbosityConfiguration.set`` for a ``-v`` count the way a
    CLI does, and restore every logger level afterwards.

    pytest re-adds its capture handlers to the root logger around each
    test phase, which makes ``basicConfig`` a no-op, so the root
    handler list is emptied at the moment of the call.
    """
    root = logging.getLogger()

    def loggers() -> list[logging.Logger]:
        return [
            lg for lg in (root, *logging.Logger.manager.loggerDict.values())
            if isinstance(lg, logging.Logger)
        ]

    levels = {lg: lg.level for lg in loggers()}

    def configure(verbose: int) -> None:
        monkeypatch.setattr(root, "handlers", [])
        VerbosityConfiguration.set(
            argparse.Namespace(verbose=verbose, logfile=None))

    yield configure
    for lg in loggers():
        level = levels.get(lg, logging.NOTSET)
        if lg.level != level:
            lg.setLevel(level)


@pytest.mark.parametrize(
    ("verbose", "level"), [(3, TRACE), (2, logging.DEBUG)],
    ids=["-vvv", "-vv"])
def test_verbose_count_sets_effect_checker_logger_level(
    configure_cli_verbosity: Callable[[int], None],
    verbose: int, level: int,
) -> None:
    configure_cli_verbosity(verbose)

    assert coding.logger.getEffectiveLevel() == level


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
