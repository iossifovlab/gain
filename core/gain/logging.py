# pylint: disable=unused-import,useless-import-alias,wrong-import-position
# pylint: disable=wildcard-import,unused-wildcard-import,invalid-name
# pylint: disable=ungrouped-imports
"""Drop-in replacement for stdlib logging with GAIn's custom levels.

Import this instead of stdlib logging to guarantee TRACE and USER_INFO
are registered before any logger is created:

    from gain import logging
    logger = logging.getLogger(__name__)
    logger.trace("fine-grained diagnostic")
    logger.user_info("message for the end user")

Everything exported by stdlib ``logging`` is re-exported here (via a star
import that honours stdlib's ``__all__``), so this module tracks the stdlib
surface across Python versions instead of a hand-maintained name list. The
``config`` and ``handlers`` submodules are re-exported too, so
``from gain import logging; logging.config.dictConfig(...)`` keeps working.

The custom TRACE / USER_INFO levels and the ``trace`` / ``user_info`` logger
methods are registered as an import side effect of ``gain.utils.log_levels``;
its docstring says how, and what a later foreign ``Logger.trace`` can and
cannot displace.

The same import installs the url-userinfo log-record seam of
``gain.utils.url_redaction`` (ADR 0023, gain#1363). Both bootstraps also run
from ``gain/__init__``, so importing anything under ``gain`` is enough.

For type checkers, ``getLogger`` is declared to return ``GainLogger``. This is
a pure typing shim: it is declared *before* the star import so the type
checker adopts the richer return type, while at runtime the star import
rebinds ``getLogger`` to the stdlib function, which instantiates that same
class. Call sites therefore need no ``# type: ignore[attr-defined]`` for the
custom methods.
"""
from __future__ import annotations

import logging as _logging
from logging import (  # ruff: ignore[unused-import]
    Filterer,
    RootLogger,
    config,
    handlers,
    root,
)
from typing import TYPE_CHECKING

import gain.utils.log_levels  # ruff: ignore[unused-import]
import gain.utils.url_redaction  # ruff: ignore[unused-import]
from gain.utils.log_levels import (  # ruff: ignore[unused-import]
    TRACE,
    USER_INFO,
    GainLogger,
)

if TYPE_CHECKING:
    def getLogger(name: str | None = None) -> GainLogger:
        # Body present only so type checkers/linters infer a real return type
        # (a bare ``...`` makes pylint treat call sites as returning ``None``
        # and emit ``assignment-from-no-return``). Never executed: at runtime
        # the star import below rebinds ``getLogger`` to the stdlib function.
        return _logging.getLogger(name)  # type: ignore[return-value]

# Runtime re-export of the whole stdlib ``logging`` surface. This rebinds
# ``getLogger`` to the stdlib function at runtime (behaviourally identical);
# the typed declaration above stays authoritative for type checkers, which is
# why the incompatible-reassignment note is silenced here.
from logging import *  # type: ignore[assignment]  # ruff: ignore[undefined-local-with-import-star]
