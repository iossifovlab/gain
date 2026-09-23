# pylint: disable=C0114,C0116,W0621
import argparse

import pytest
from gain import logging
from gain.effect_annotation.effect_checkers import coding
from gain.utils.log_levels import TRACE
from gain.utils.verbosity_configuration import VerbosityConfiguration


def configure_cli_verbosity(verbose: int) -> None:
    """Run ``VerbosityConfiguration.set`` for a ``-v`` count the way a
    CLI does (the autouse ``restore_logger_levels`` undoes it).

    pytest keeps its capture handlers on the root logger, so every call
    runs with the root already configured -- the case ``basicConfig``
    alone ignores.
    """
    VerbosityConfiguration.set(
        argparse.Namespace(verbose=verbose, logfile=None))


@pytest.mark.parametrize(
    ("verbose", "level"),
    [(1, logging.INFO), (2, logging.DEBUG), (3, TRACE)],
    ids=["-v", "-vv", "-vvv"])
def test_verbose_count_sets_root_level_when_root_has_a_handler(
    verbose: int, level: int,
) -> None:
    assert logging.getLogger().handlers

    configure_cli_verbosity(verbose)

    assert logging.getLogger().level == level


@pytest.mark.parametrize(
    "root_has_handler", [True, False], ids=["root-has-handler", "bare-root"])
def test_set_without_v_keeps_a_more_verbose_root_level(
    monkeypatch: pytest.MonkeyPatch, root_has_handler: bool,
) -> None:
    """An embedder (or ``caplog.at_level``) that opened the root up is
    not silenced by a CLI run without ``-v`` -- neither by the level
    ``set`` applies nor by ``basicConfig``, which runs on a bare root."""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    if not root_has_handler:
        monkeypatch.setattr(root, "handlers", [])

    configure_cli_verbosity(0)

    assert root.level == logging.DEBUG


class _ClosureRecordingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    def emit(self, record: logging.LogRecord) -> None:
        pass

    def close(self) -> None:
        self.closed = True
        super().close()


def test_set_keeps_a_handler_other_code_attached_to_the_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = logging.getLogger()
    owned = _ClosureRecordingHandler()
    monkeypatch.setattr(root, "handlers", [*root.handlers, owned])

    configure_cli_verbosity(2)

    assert owned in root.handlers
    assert not owned.closed


@pytest.mark.parametrize(
    ("verbose", "level"), [(3, TRACE), (2, logging.DEBUG)],
    ids=["-vvv", "-vv"])
def test_verbose_count_sets_effect_checker_logger_level(
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
