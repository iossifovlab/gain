"""Deprecated alias for :mod:`gain.annotation.annotate_tabular`."""
from __future__ import annotations

import sys
import warnings

from gain.annotation.annotate_tabular import (
    annotate_tabular as annotate_columns,
)

warnings.warn(
    "gain.annotation.annotate_columns is deprecated; "
    "use gain.annotation.annotate_tabular instead.",
    DeprecationWarning,
    stacklevel=2,
)

_BANNER = (
    "DEPRECATION: 'annotate_columns' has been renamed to "
    "'annotate_tabular'.\n"
    "The old name will be removed in a future release. Please "
    "switch to:\n"
    "    annotate_tabular <same args>\n"
)


def cli(argv: list[str] | None = None) -> None:
    """Entry point for the deprecated ``annotate_columns`` CLI."""
    print(_BANNER, file=sys.stderr)
    _cli(argv)


__all__ = ["annotate_columns", "cli"]
