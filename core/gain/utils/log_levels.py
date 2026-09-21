"""Custom logging levels for the GAIn package.

TRACE (5): below DEBUG, for the finest-grained diagnostic output.
USER_INFO (25): between INFO and WARNING, for messages directed at end users.

Importing this module registers both level names and makes ``trace`` /
``user_info`` available on loggers, two ways:

- ``GainLogger`` becomes the class ``logging.getLogger`` instantiates, so
  every logger created afterwards carries the two methods as its own. A
  later assignment to ``logging.Logger.trace`` by another package (bokeh
  does this on import, with a different level number and no ``stacklevel``
  handling) does not reach them: the subclass attribute shadows it.
- The same functions are also assigned onto ``logging.Logger`` itself, so
  a logger created before this module was imported -- the root logger,
  or a third-party module logger -- has them too, until such a foreign
  assignment replaces them.

Two things this module does not own:

- The *name* -> level direction of the stdlib tables.
  ``getLevelName(5) == "TRACE"`` holds for the life of the process, but if
  another package registers the name ``TRACE`` for a different number,
  ``getLevelName("TRACE")`` and ``getLevelNamesMapping()["TRACE"]`` follow
  the later registration. Resolve gain's levels through ``TRACE`` /
  ``USER_INFO`` here, never by name.
- The logger class of a host that called ``logging.setLoggerClass`` before
  importing gain: the registration here replaces it, so loggers created
  after the import are ``GainLogger`` and not the host's class.
"""
from __future__ import annotations

import logging
from typing import Any

TRACE = 5
USER_INFO = 25

logging.addLevelName(TRACE, "TRACE")
logging.addLevelName(USER_INFO, "USER_INFO")


class GainLogger(logging.Logger):
    """A ``Logger`` whose ``trace`` and ``user_info`` are its own methods."""

    def trace(
        self,
        msg: object,
        *args: object,
        **kwargs: Any,
    ) -> None:
        """Log ``msg`` at TRACE, attributed to the caller."""
        if self.isEnabledFor(TRACE):
            self._log(
                TRACE, msg, args,
                stacklevel=kwargs.pop("stacklevel", 1) + 1, **kwargs)

    def user_info(
        self,
        msg: object,
        *args: object,
        **kwargs: Any,
    ) -> None:
        """Log ``msg`` at USER_INFO, attributed to the caller."""
        if self.isEnabledFor(USER_INFO):
            self._log(
                USER_INFO, msg, args,
                stacklevel=kwargs.pop("stacklevel", 1) + 1, **kwargs)


logging.setLoggerClass(GainLogger)

# The same methods for loggers that predate this import.
logging.Logger.trace = GainLogger.trace  # type: ignore[attr-defined]
logging.Logger.user_info = GainLogger.user_info  # type: ignore[attr-defined]
