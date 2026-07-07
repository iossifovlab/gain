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

The custom TRACE / USER_INFO levels (and the ``Logger.trace`` /
``Logger.user_info`` methods) are registered as an import side effect of
``gain.utils.log_levels``, which monkeypatches ``logging.Logger`` globally so
that *every* logger — including the root logger and any already created — gains
the methods at runtime.

For type checkers, ``getLogger`` is declared to return a ``Logger`` subclass
advertising ``.trace`` / ``.user_info``. This is a pure typing shim: it is
declared *before* the star import so the type checker adopts the richer return
type, while at runtime the star import rebinds ``getLogger`` to the stdlib
function (identical behaviour — the methods come from the monkeypatch above).
Call sites therefore need no ``# type: ignore[attr-defined]`` for the custom
methods.
"""
from __future__ import annotations

import logging as _logging
from logging import config, handlers  # noqa: F401
from typing import TYPE_CHECKING, Any

import gain.utils.log_levels  # noqa: F401
from gain.utils.log_levels import TRACE, USER_INFO  # noqa: F401

if TYPE_CHECKING:
    class GainLogger(_logging.Logger):
        """Typing view of a logger carrying GAIn's custom-level methods."""

        def trace(
            self, msg: object, *args: object, **kwargs: Any,
        ) -> None: ...

        def user_info(
            self, msg: object, *args: object, **kwargs: Any,
        ) -> None: ...

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
from logging import *  # type: ignore[assignment]  # noqa: F403
