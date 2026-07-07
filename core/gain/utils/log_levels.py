"""Custom logging levels for the GAIn package.

TRACE (5): below DEBUG, for the finest-grained diagnostic output.
USER_INFO (25): between INFO and WARNING, for messages directed at end users.
"""
from __future__ import annotations

import logging
from typing import Any

TRACE = 5
USER_INFO = 25

logging.addLevelName(TRACE, "TRACE")
logging.addLevelName(USER_INFO, "USER_INFO")


def _trace(
    self: logging.Logger,
    msg: object,
    *args: object,
    **kwargs: Any,
) -> None:
    if self.isEnabledFor(TRACE):
        self._log(
            TRACE, msg, args,
            stacklevel=kwargs.pop("stacklevel", 1) + 1, **kwargs)


def _user_info(
    self: logging.Logger,
    msg: object,
    *args: object,
    **kwargs: Any,
) -> None:
    if self.isEnabledFor(USER_INFO):
        self._log(
            USER_INFO, msg, args,
            stacklevel=kwargs.pop("stacklevel", 1) + 1, **kwargs)


logging.Logger.trace = _trace  # type: ignore[attr-defined]
logging.Logger.user_info = _user_info  # type: ignore[attr-defined]
