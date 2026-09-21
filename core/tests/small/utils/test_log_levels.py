# pylint: disable=W0621,C0114,C0116
import importlib
import json
import logging
import pathlib
import subprocess
import sys

import gain  # ruff: ignore[unused-import] — registers custom levels as a side effect
import gain.utils.log_levels
import pytest
from gain.utils.log_levels import TRACE, USER_INFO

FOREIGN_REGISTRANT_PROBE = (
    pathlib.Path(__file__).with_name("foreign_level_registrant.py"))


@pytest.fixture(scope="module")
def records_under_foreign_registrant() -> dict[str, dict]:
    """Records a gain logger emits after bokeh's shape of monkeypatch.

    Runs the probe in its own interpreter: the collider re-registers
    level *names*, which the parent process could not undo.
    """
    result = subprocess.run(
        [sys.executable, str(FOREIGN_REGISTRANT_PROBE)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    return {r["message"]: r for r in json.loads(result.stdout)}


def test_trace_level_value() -> None:
    assert TRACE == 5


def test_user_info_level_value() -> None:
    assert USER_INFO == 25


def test_trace_level_name() -> None:
    assert logging.getLevelName(TRACE) == "TRACE"


def test_user_info_level_name() -> None:
    assert logging.getLevelName(USER_INFO) == "USER_INFO"


def test_trace_emits_record(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(TRACE):
        logger = logging.getLogger(__name__)
        logger.trace("trace msg")  # type: ignore[attr-defined]
    assert any(
        r.levelno == TRACE and r.message == "trace msg"
        for r in caplog.records
    )


def test_user_info_emits_record(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(USER_INFO):
        logger = logging.getLogger(__name__)
        logger.user_info("user msg")  # type: ignore[attr-defined]
    assert any(
        r.levelno == USER_INFO and r.message == "user msg"
        for r in caplog.records
    )


def test_trace_not_emitted_above_level(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG):
        logger = logging.getLogger(__name__)
        logger.trace("should be silent")  # type: ignore[attr-defined]
    assert not any(r.levelno == TRACE for r in caplog.records)


def test_user_info_not_emitted_above_level(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        logger = logging.getLogger(__name__)
        logger.user_info("should be silent")  # type: ignore[attr-defined]
    assert not any(r.levelno == USER_INFO for r in caplog.records)


def test_trace_record_points_at_caller(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(TRACE):
        logger = logging.getLogger(__name__)
        logger.trace("trace caller")  # type: ignore[attr-defined]
    record = next(r for r in caplog.records if r.message == "trace caller")
    assert record.filename == "test_log_levels.py"
    assert record.funcName == "test_trace_record_points_at_caller"


def test_user_info_record_points_at_caller(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(USER_INFO):
        logger = logging.getLogger(__name__)
        logger.user_info("user caller")  # type: ignore[attr-defined]
    record = next(r for r in caplog.records if r.message == "user caller")
    assert record.filename == "test_log_levels.py"
    assert record.funcName == "test_user_info_record_points_at_caller"


def test_trace_honors_caller_supplied_stacklevel(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def indirection(logger: logging.Logger) -> None:
        # stacklevel=2 => report *this* function's caller, i.e. the test.
        logger.trace(  # type: ignore[attr-defined]
            "via indirection", stacklevel=2)

    with caplog.at_level(TRACE):
        logger = logging.getLogger(__name__)
        indirection(logger)
    record = next(r for r in caplog.records if r.message == "via indirection")
    assert record.funcName == "test_trace_honors_caller_supplied_stacklevel"


def test_trace_survives_a_later_foreign_logger_trace(
    records_under_foreign_registrant: dict[str, dict],
) -> None:
    record = records_under_foreign_registrant["trace direct"]
    assert record["levelno"] == TRACE
    assert record["filename"] == FOREIGN_REGISTRANT_PROBE.name
    assert record["funcName"] == "direct_caller"


def test_trace_stacklevel_survives_a_later_foreign_logger_trace(
    records_under_foreign_registrant: dict[str, dict],
) -> None:
    record = records_under_foreign_registrant["trace via indirection"]
    assert record["levelno"] == TRACE
    assert record["funcName"] == "indirect_caller"


def test_user_info_survives_a_later_foreign_logger_user_info(
    records_under_foreign_registrant: dict[str, dict],
) -> None:
    record = records_under_foreign_registrant["user_info direct"]
    assert record["levelno"] == USER_INFO
    assert record["filename"] == FOREIGN_REGISTRANT_PROBE.name
    assert record["funcName"] == "direct_caller"


def test_root_logger_created_before_bootstrap_has_trace(
    records_under_foreign_registrant: dict[str, dict],
) -> None:
    # Emitted before the foreign registrant: the root logger is never a
    # GainLogger, so this is the class-level fallback at work.
    record = records_under_foreign_registrant["root trace before"]
    assert record["levelno"] == TRACE
    assert record["funcName"] == "main"


def test_foreign_logger_trace_does_reach_a_plain_logger(
    records_under_foreign_registrant: dict[str, dict],
) -> None:
    # The boundary of what gain owns: a logger that is not a GainLogger
    # logs through the foreign method once it is installed -- which is
    # also what proves the probe's collider was live for the tests above.
    record = records_under_foreign_registrant["root trace after"]
    assert record["levelno"] == 9


def test_levels_registration_is_idempotent() -> None:
    # Re-running the registration side effect must not clobber or error.
    importlib.reload(gain.utils.log_levels)
    assert logging.getLevelName(TRACE) == "TRACE"
    assert logging.getLevelName(USER_INFO) == "USER_INFO"
    logger = logging.getLogger(__name__)
    assert callable(logger.trace)  # type: ignore[attr-defined]
    assert callable(logger.user_info)  # type: ignore[attr-defined]
