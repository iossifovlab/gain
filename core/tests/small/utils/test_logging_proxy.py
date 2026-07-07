# pylint: disable=C0114,C0116
from gain import logging


def test_common_stdlib_names_present() -> None:
    for name in (
        "getLogger", "Handler", "Formatter", "StreamHandler", "FileHandler",
        "Logger", "LogRecord", "NullHandler", "basicConfig", "addLevelName",
        "getLevelName", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL",
        "NOTSET",
    ):
        assert hasattr(logging, name), f"missing stdlib name: {name}"


def test_non_all_stdlib_names_present() -> None:
    # These are not in stdlib ``logging.__all__``, so the star import does not
    # re-export them; they must be explicitly re-imported for the drop-in
    # replacement to keep working (``logging.root`` especially is common).
    for name in ("root", "RootLogger", "Filterer"):
        assert hasattr(logging, name), f"missing stdlib name: {name}"


def test_custom_levels_present() -> None:
    assert logging.TRACE == 5
    assert logging.USER_INFO == 25


def test_getlogger_returns_logger_with_custom_methods() -> None:
    logger = logging.getLogger("gain.test.proxy")
    assert callable(logger.trace)  # type: ignore[attr-defined]
    assert callable(logger.user_info)  # type: ignore[attr-defined]


def test_getlevelnamesmapping_reexported() -> None:
    # 3.11+ stdlib helper; must survive the robust re-export.
    mapping = logging.getLevelNamesMapping()
    assert mapping["TRACE"] == 5
    assert mapping["USER_INFO"] == 25
