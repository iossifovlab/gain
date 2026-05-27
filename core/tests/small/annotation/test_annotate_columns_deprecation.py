"""Tests for the deprecated `gain.annotation.annotate_columns` shim.

These tests exist solely to keep the deprecation surface (re-exports,
DeprecationWarning, CLI banner) honest. When the shim is removed in a
future major release, delete this file alongside `annotate_columns.py`.
"""
from __future__ import annotations

import subprocess
import sys


def test_annotate_columns_module_reexports_tabular_function() -> None:
    import gain.annotation.annotate_columns as legacy
    import gain.annotation.annotate_tabular as canonical

    assert legacy.annotate_columns is canonical.annotate_tabular


def test_importing_annotate_columns_emits_deprecation_warning() -> None:
    result = subprocess.run(
        [
            sys.executable, "-W", "always::DeprecationWarning",
            "-c", "import gain.annotation.annotate_columns",
        ],
        capture_output=True, text=True, check=True,
    )
    assert "DeprecationWarning" in result.stderr
    assert "annotate_columns" in result.stderr
    assert "annotate_tabular" in result.stderr


def test_annotate_columns_cli_prints_deprecation_banner_on_stderr() -> None:
    result = subprocess.run(
        [
            sys.executable, "-c",
            "from gain.annotation.annotate_columns import cli; "
            "cli(['--version'])",
        ],
        capture_output=True, text=True, check=False,
    )
    assert "DEPRECATION" in result.stderr
    assert "annotate_columns" in result.stderr
    assert "annotate_tabular" in result.stderr
