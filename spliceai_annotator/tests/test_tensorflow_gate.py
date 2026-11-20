# pylint: disable=W0621,C0114,C0116
"""The shared rule for a missing TensorFlow in the test suite (#2442).

An environment that lost TensorFlow by accident must fail the tests that
need it. Only an environment that sets ``SPLICEAI_EXPECT_NO_TENSORFLOW=1``
skips them.
"""
import inspect
import sys
from collections.abc import Callable

import pytest

from tests.tensorflow_gate import (
    EXPECT_NO_TENSORFLOW_ENV,
    import_tensorflow,
    require_tensorflow,
)
from tests.test_spliceai_backends import load_tensorflow_backend


@pytest.fixture
def tensorflow_unimportable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every import of TensorFlow fail, as in a build without the extra.

    The annotator imports the selected backend at import time, so the
    TensorFlow backend module can sit in `sys.modules` already. Remove it too,
    or a later import returns the cached module and never fails.
    """
    monkeypatch.setitem(sys.modules, "tensorflow", None)
    monkeypatch.delitem(
        sys.modules, "spliceai_annotator.spliceai_backend_tensorflow",
        raising=False)


def _raised_by(call: Callable[[], object]) -> BaseException:
    """What `call` raises, a skip included.

    `pytest.raises(ImportError)` lets a skip escape, and the test then shows
    as skipped rather than failed. Catch everything and assert the type.
    """
    with pytest.raises(BaseException) as info:
        call()
    return info.value


@pytest.mark.parametrize("flag", [None, "0", ""])
def test_missing_tensorflow_fails_without_the_flag(
    tensorflow_unimportable: None,
    monkeypatch: pytest.MonkeyPatch,
    flag: str | None,
) -> None:
    # Only the exact value "1" skips: "0" and "" must fail like an unset flag.
    if flag is None:
        monkeypatch.delenv(EXPECT_NO_TENSORFLOW_ENV, raising=False)
    else:
        monkeypatch.setenv(EXPECT_NO_TENSORFLOW_ENV, flag)

    error = _raised_by(import_tensorflow)

    assert isinstance(error, ImportError), repr(error)
    assert "tensorflow" in str(error)


def test_missing_tensorflow_skips_with_the_flag(
    tensorflow_unimportable: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(EXPECT_NO_TENSORFLOW_ENV, "1")

    error = _raised_by(import_tensorflow)

    assert isinstance(error, pytest.skip.Exception), repr(error)
    assert EXPECT_NO_TENSORFLOW_ENV in str(error)


def test_flag_does_not_skip_an_import_that_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(EXPECT_NO_TENSORFLOW_ENV, "1")
    loaded = object()

    assert require_tensorflow(lambda: loaded) is loaded


def test_missing_tensorflow_backend_fails_without_the_flag(
    tensorflow_unimportable: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(EXPECT_NO_TENSORFLOW_ENV, raising=False)

    error = _raised_by(load_tensorflow_backend)

    assert isinstance(error, ImportError), repr(error)
    assert "TensorFlow" in str(error)


def test_missing_tensorflow_backend_skips_with_the_flag(
    tensorflow_unimportable: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(EXPECT_NO_TENSORFLOW_ENV, "1")

    error = _raised_by(load_tensorflow_backend)

    assert isinstance(error, pytest.skip.Exception), repr(error)
    assert EXPECT_NO_TENSORFLOW_ENV in str(error)


def test_keras_fixture_fails_without_the_flag(
    tensorflow_unimportable: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Imported here: the module skips itself without `onnx`, and that skip
    # must cover this test only, not the whole gate module.
    pytest.importorskip("onnx")
    from tests import test_onnx_equivalence  # pylint: disable=C0415
    monkeypatch.delenv(EXPECT_NO_TENSORFLOW_ENV, raising=False)

    # The body of the `keras` fixture itself, not a helper that it calls:
    # a fixture that went back to `importorskip` must turn this test red.
    keras_fixture_body = inspect.unwrap(test_onnx_equivalence.keras)

    error = _raised_by(keras_fixture_body)

    assert isinstance(error, ImportError), repr(error)
    assert "tensorflow" in str(error)
