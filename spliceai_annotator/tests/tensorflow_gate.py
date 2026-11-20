"""One rule for a missing TensorFlow, shared by every test that needs it.

TensorFlow is an optional extra, and the `.h5` models are the source of truth
that the TensorFlow backend and the ONNX equivalence checks compare against.
An environment that lost TensorFlow by accident must therefore fail those
tests, not turn them into silent skips the way `importorskip` would.

Only an environment that declares it was built without the extra, by setting
`SPLICEAI_EXPECT_NO_TENSORFLOW=1`, skips them -- and then only if the import
really fails.

Kept free of annotator imports, so that `test_onnx_equivalence` stays
importable in a lean environment that has `onnx` and nothing else.
"""
import importlib
import os
from collections.abc import Callable
from types import ModuleType

import pytest

# Set only in the CI cell that builds without the `tensorflow` extra (the
# Python 3.14 matrix cell, #299) and in its local twin, `.venv-notf`.
EXPECT_NO_TENSORFLOW_ENV = "SPLICEAI_EXPECT_NO_TENSORFLOW"


def require_tensorflow[T](load: Callable[[], T]) -> T:
    """Run `load`; skip on its `ImportError` only where that is expected."""
    try:
        return load()
    except ImportError as error:
        if os.environ.get(EXPECT_NO_TENSORFLOW_ENV) != "1":
            raise
        # Raised, not `pytest.skip(...)`: pylint does not read that call as
        # NoReturn and flags the function for inconsistent returns.
        raise pytest.skip.Exception(
            f"{EXPECT_NO_TENSORFLOW_ENV}=1: {error}") from error


def import_tensorflow() -> ModuleType:
    """Import TensorFlow itself, under the rule of `require_tensorflow`."""
    return require_tensorflow(lambda: importlib.import_module("tensorflow"))
