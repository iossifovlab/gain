"""Probe: gain's custom log levels after a foreign ``Logger.trace``.

Run as a script in a subprocess by ``test_log_levels``. It imports gain,
then does to the stdlib what bokeh's ``bokeh.util.logconfig`` does on
import -- assigns its own ``trace`` onto ``logging.Logger`` (a different
level number, no ``stacklevel`` arithmetic) and registers the name
``TRACE`` for that number -- and the same again for ``user_info``, which
bokeh leaves alone but a package of the same shape need not. It logs
through a gain logger, and through the root logger both before and after
that, and prints the records it collected as JSON, one object per message.
"""
import json
import sys
from collections.abc import Callable
from typing import Any

from gain import logging

FOREIGN_TRACE = 9
FOREIGN_USER_INFO = 26


def _foreign_method(level: int) -> Callable[..., None]:
    def method(
        self: logging.Logger, message: str, *args: Any, **kws: Any,
    ) -> None:
        if self.isEnabledFor(level):
            self._log(level, message, args, **kws)
    return method


def install_foreign_registrant() -> None:
    """Replace the class-level methods and re-register the level names."""
    logging.Logger.trace = (  # type: ignore[attr-defined]
        _foreign_method(FOREIGN_TRACE))
    logging.Logger.user_info = (  # type: ignore[attr-defined]
        _foreign_method(FOREIGN_USER_INFO))
    logging.addLevelName(FOREIGN_TRACE, "TRACE")
    logging.addLevelName(FOREIGN_USER_INFO, "USER_INFO")


class _Collect(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def main() -> None:
    """Install the collider between two root-logger emits; print the records."""
    handler = _Collect()
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(1)
    logger = logging.getLogger("gain.tests.foreign_level_registrant")
    logger.setLevel(1)

    # The root logger predates every import and is never a GainLogger.
    root.trace("root trace before")

    install_foreign_registrant()

    def direct_caller() -> None:
        logger.trace("trace direct")
        logger.user_info("user_info direct")

    def indirection() -> None:
        # stacklevel=2 => report this function's caller.
        logger.trace("trace via indirection", stacklevel=2)

    def indirect_caller() -> None:
        indirection()

    direct_caller()
    indirect_caller()
    root.trace("root trace after")

    json.dump(
        [
            {
                "message": r.getMessage(),
                "levelno": r.levelno,
                "filename": r.filename,
                "funcName": r.funcName,
            }
            for r in handler.records
        ],
        sys.stdout,
    )


if __name__ == "__main__":
    main()
