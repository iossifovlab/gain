# pylint: disable=C0114,C0116
import logging

import gain  # noqa: F401 — registers custom levels as a side effect
import pytest
from gain.utils.log_levels import TRACE, USER_INFO


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
