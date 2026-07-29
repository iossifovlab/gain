# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Guard the hang diagnostic configured in ``pytest.ini`` (#494).

A test that parks must end the run with a stack dump, instead of consuming
the enclosing CI job's timeout in silence. Two things can quietly take that
away: the ini keys being dropped, and the dump being emitted somewhere
``pytest -n`` cannot carry back. The tests here cover both -- the second by
running a genuinely hanging test in a subprocess.

See ``docs/adr/0004-core-test-hang-diagnostic.md``.
"""
import subprocess
import sys
from pathlib import Path

import pytest

# The inner test parks on an event that is never set, so it never finishes
# regardless of this value; it is small only to keep these tests quick.
INNER_TIMEOUT = 5.0

# Generous: it bounds interpreter start-up and collection, not the hang.
# Reaching it means the mechanism failed to stop the run at all.
OUTER_TIMEOUT = 120.0

HANGING_TEST = """\
import threading

_NEVER_SET = threading.Event()


def _background_worker():
    # The frame that must show up in the dump. In the shape this guards
    # (#480) the parked thread is a background worker, not the main one.
    _NEVER_SET.wait()


def test_parks_forever():
    worker = threading.Thread(
        target=_background_worker, name="hang-probe-worker", daemon=True)
    worker.start()
    _NEVER_SET.wait()
"""


def test_faulthandler_timeout_is_the_measured_threshold(
    pytestconfig: pytest.Config,
) -> None:
    """The margin, not just the presence, is the thing worth guarding.

    Pinned exactly rather than bounded because both directions are wrong in
    ways that are quiet: lowering it starts shooting legitimate work (a
    cold-cache ``tests/integration`` fixture downloads a ~787MB genome
    inside one item's setup), and raising it gives back the CI time this
    exists to save. Changing it means re-measuring -- see ADR 0004.
    """
    assert float(pytestconfig.getini("faulthandler_timeout")) == 600


def test_faulthandler_exits_rather_than_only_dumping(
    pytestconfig: pytest.Config,
) -> None:
    """Without this, pytest dumps a fine stack and goes on hanging.

    ``getini`` also raises on a pytest that does not know the key (< 9.0),
    which is the version floor this suite depends on.
    """
    assert pytestconfig.getini("faulthandler_exit_on_timeout") is True


def _run_hanging_test(
    tmp_path: Path, *extra_args: str,
) -> subprocess.CompletedProcess[str]:
    (tmp_path / "test_hang_probe.py").write_text(HANGING_TEST)
    argv = [
        sys.executable, "-m", "pytest",
        "-p", "no:cacheprovider",
        "-o", f"faulthandler_timeout={INNER_TIMEOUT}",
        "-o", "faulthandler_exit_on_timeout=true",
        *extra_args,
        "test_hang_probe.py",
    ]
    try:
        return subprocess.run(
            argv, cwd=tmp_path, capture_output=True, text=True,
            timeout=OUTER_TIMEOUT, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        # The mechanism failed to stop the run: exactly the bug this file
        # exists to catch. TimeoutExpired's message shows none of the
        # child's output, so surface it as a readable failure instead.
        msg = (
            f"the hanging test was not cut short within {OUTER_TIMEOUT}s\n"
            f"stdout: {exc.stdout!r}\n"
            f"stderr: {exc.stderr!r}"
        )
        raise AssertionError(msg) from exc


def test_a_hanging_test_is_cut_short_with_an_all_thread_dump(
    tmp_path: Path,
) -> None:
    result = _run_hanging_test(tmp_path)
    output = result.stdout + result.stderr

    assert result.returncode != 0, output
    # The parked *background* thread, not just the main one: a dump showing
    # only the main thread is useless for the #480 shape.
    assert "_background_worker" in output, output
    # The parked test's own frame. Note this is the frame, not a nodeid --
    # a serial run prints only the file name before dying, so what
    # identifies the test here is its presence in the dump.
    assert "test_parks_forever" in output, output


def test_a_hanging_test_under_xdist_is_cut_short_and_named(
    tmp_path: Path,
) -> None:
    """Not redundant with the serial case -- it is the one that matters.

    CI runs ``core`` with ``-n 5``, and an xdist worker is where
    ``pytest-timeout``'s dump was measured to vanish entirely (ADR 0004).
    It is also the only configuration that reports the hung test by
    *nodeid*, which is what makes a CI failure actionable on its own.
    """
    result = _run_hanging_test(tmp_path, "-n", "2")
    output = result.stdout + result.stderr

    assert result.returncode != 0, output
    assert "_background_worker" in output, output
    assert "test_hang_probe.py::test_parks_forever" in output, output
