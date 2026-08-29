"""Builds the fixture GRR the info-page suite validates.

One build serves the whole session, including every ``pytest -n`` worker:
the pages are an expensive, read-only artifact and rebuilding them per
worker would multiply the suite's cost by the worker count.  See
:func:`built_grr` for how that is arranged without a lock library.
"""
from __future__ import annotations

import errno
import os
import pathlib
import shutil
import time
from dataclasses import dataclass

import pytest
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.repository import GR_CONF_FILE_NAME

from tests.small.genomic_resources.info_pages.supplement import (
    add_supplement_resources,
)

#: The mini-GRR submodule, as checked out under ``core/tests/fixtures``.
#: ``parents[3]`` is ``core/tests`` -- this file is at
#: ``core/tests/small/genomic_resources/info_pages/conftest.py``.
MINI_GRR_SOURCE = (
    pathlib.Path(__file__).parents[3] / "fixtures" / "mini-GRR"
)

_SUBMODULE_PATH = "core/tests/fixtures/mini-GRR"

_NOT_CHECKED_OUT = (
    f"the mini-GRR fixture submodule is not checked out at "
    f"{_SUBMODULE_PATH}.\n"
    f"run: git submodule update --init {_SUBMODULE_PATH}"
)

_NOT_A_GRR = (
    f"{_SUBMODULE_PATH} exists but contains no {GR_CONF_FILE_NAME}, so it is "
    f"not a genomic resource repository.  If the checkout is stale or "
    f"partial, re-run: git submodule update --init --force "
    f"{_SUBMODULE_PATH}"
)

# How long a worker that did not win the build waits for the winner.  The
# build is ~15s on a developer machine; the margin is for a loaded CI agent,
# and it is a backstop rather than an expected wait -- a build that fails
# publishes a marker and every waiter fails immediately.
_BUILD_TIMEOUT_SECONDS = 900.0
_BUILD_POLL_SECONDS = 0.25


@dataclass(frozen=True)
class BuiltGRR:
    """A freshly built fixture GRR, and when its build started.

    ``build_started`` exists so the suite can prove a page was *regenerated*
    rather than served from the stale ``index.html`` files mini-GRR commits.
    Those committed pages predate ADR 0020 and carry no Coverage section, so
    a page that ``repo-info`` silently skipped would otherwise be validated
    as though it were fresh.
    """

    path: pathlib.Path
    build_started: float


def check_source_available() -> None:
    """Fail with instructions when the fixture submodule is missing.

    A hard failure, never a skip: a suite that quietly skips in CI is exactly
    the "green but meaningless" state gain#991 exists to remove.
    """
    if not MINI_GRR_SOURCE.is_dir() or not any(MINI_GRR_SOURCE.iterdir()):
        raise AssertionError(_NOT_CHECKED_OUT)
    if next(MINI_GRR_SOURCE.rglob(GR_CONF_FILE_NAME), None) is None:
        raise AssertionError(_NOT_A_GRR)


def _build_into(repo_dir: pathlib.Path) -> None:
    """Copy the fixture GRR to ``repo_dir`` and generate its pages.

    Two things here are load-bearing.  The copy: ``repo-stats`` and
    ``repo-info`` write into the repository they are pointed at, so running
    them against the submodule working tree would leave it dirty after every
    test run.  And ``-f``: mini-GRR ships committed ``statistics/stats_hash``
    files, so a plain ``repo-stats`` is a no-op and the suite would validate
    stale committed output instead of freshly generated pages.
    """
    shutil.copytree(
        MINI_GRR_SOURCE, repo_dir,
        ignore=shutil.ignore_patterns(".git"),
    )
    add_supplement_resources(repo_dir)
    cli_manage(["repo-stats", "-f", "-R", str(repo_dir), "-j", "1"])
    cli_manage(["repo-info", "-R", str(repo_dir), "-j", "1"])


def _wait_for(done_marker: pathlib.Path, failed_marker: pathlib.Path) -> None:
    """Block until the worker that won the build publishes its outcome."""
    deadline = time.monotonic() + _BUILD_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if done_marker.exists():
            return
        if failed_marker.exists():
            raise AssertionError(
                "the worker building the fixture GRR failed; see its "
                f"error above.  Marker: {failed_marker}")
        time.sleep(_BUILD_POLL_SECONDS)
    raise AssertionError(
        f"timed out after {_BUILD_TIMEOUT_SECONDS}s waiting for another "
        f"worker to build the fixture GRR at {done_marker.parent}")


@pytest.fixture(scope="session")
def built_grr(
    tmp_path_factory: pytest.TempPathFactory,
    worker_id: str,
) -> BuiltGRR:
    """The fixture GRR, built exactly once per session.

    A plain ``scope="session"`` fixture is once per *worker*, so under
    ``pytest -n 5`` it would run five builds of a ~15s artifact.  Instead the
    workers race for a lock in the run's shared temp root: the winner builds
    and publishes a marker, the losers wait and reuse its output.

    The lock is an ``O_CREAT | O_EXCL`` file rather than a ``filelock``
    dependency -- gain#991 requires that this suite add no new test
    dependency, and ``filelock`` reaches the environment only transitively.

    Without xdist there is no race and no shared root to put a lock in:
    ``getbasetemp().parent`` is then ``/tmp/pytest-of-<user>``, which is
    shared across *runs*, and a lock there would let one run reuse another
    run's build.  So the single-worker case simply builds into its own
    temp dir.
    """
    check_source_available()

    if worker_id == "master":
        repo_dir = tmp_path_factory.mktemp("info_pages_grr") / "grr"
        started = time.time()
        _build_into(repo_dir)
        return BuiltGRR(path=repo_dir, build_started=started)

    shared_root = tmp_path_factory.getbasetemp().parent
    repo_dir = shared_root / "info_pages_grr"
    done_marker = shared_root / "info_pages_grr.done"
    failed_marker = shared_root / "info_pages_grr.failed"
    lock_path = shared_root / "info_pages_grr.lock"

    started = time.time()
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except OSError as exc:
        if exc.errno != errno.EEXIST:
            raise
        _wait_for(done_marker, failed_marker)
        return BuiltGRR(
            path=repo_dir,
            build_started=float(done_marker.read_text()),
        )

    os.close(fd)
    try:
        _build_into(repo_dir)
    except BaseException:
        # Publish the failure before propagating, so the waiting workers
        # report this build's error instead of each timing out in turn.
        failed_marker.touch()
        raise
    done_marker.write_text(str(started))
    return BuiltGRR(path=repo_dir, build_started=started)
