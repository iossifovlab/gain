"""Tests for the deprecated `gain.annotation.annotate_columns` shim.

These tests exist solely to keep the deprecation surface (re-exports,
DeprecationWarning, CLI banner) honest. When the shim is removed in a
future major release, delete this file alongside `annotate_columns.py`.
"""
from __future__ import annotations

import subprocess
import sys

import pytest


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


def test_annotate_columns_cli_prints_deprecation_banner_on_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from gain.annotation.annotate_columns import cli

    # The banner is printed to stderr before the wrapped CLI runs, so it is
    # captured regardless of how the underlying --version handling exits.
    with pytest.raises(SystemExit):
        cli(["--version"])

    stderr = capsys.readouterr().err
    assert "DEPRECATION" in stderr
    assert "annotate_columns" in stderr
    assert "annotate_tabular" in stderr
