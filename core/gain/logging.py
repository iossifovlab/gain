"""Drop-in replacement for stdlib logging with GAIn's custom levels.

Import this instead of stdlib logging to guarantee TRACE and USER_INFO
are registered before any logger is created:

    import gain.logging as logging
    logger = logging.getLogger(__name__)
"""
from __future__ import annotations

from logging import (  # noqa: F401
    BASIC_FORMAT,
    CRITICAL,
    DEBUG,
    ERROR,
    FATAL,
    INFO,
    NOTSET,
    WARN,
    WARNING,
    BufferingFormatter,
    FileHandler,
    Filter,
    Filterer,
    Formatter,
    Handler,
    Logger,
    LoggerAdapter,
    LogRecord,
    NullHandler,
    RootLogger,
    StreamHandler,
    addLevelName,
    basicConfig,
    captureWarnings,
    critical,
    debug,
    disable,
    error,
    exception,
    fatal,
    getLevelName,
    getLevelNamesMapping,
    getLogger,
    getLoggerClass,
    getLogRecordFactory,
    info,
    log,
    makeLogRecord,
    raiseExceptions,
    root,
    setLoggerClass,
    setLogRecordFactory,
    shutdown,
    warn,
    warning,
)

import gain.utils.log_levels  # noqa: F401
from gain.utils.log_levels import TRACE, USER_INFO  # noqa: F401
