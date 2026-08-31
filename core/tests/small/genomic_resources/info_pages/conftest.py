"""Builds the fixture GRR the info-page suite validates.

One build serves the whole session, including every ``pytest -n`` worker:
the pages are an expensive, read-only artifact and rebuilding them per
worker would multiply the suite's cost by the worker count.  See
:func:`built_grr` for how that is arranged without a lock library.
"""
from __future__ import annotations

import pathlib
import shutil
import time
from dataclasses import dataclass

import pytest
from filelock import FileLock
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.repository import GR_CONF_FILE_NAME

from tests.small.genomic_resources.info_pages.supplement import (
    add_supplement_resources,
)

#: The mini-GRR submodule, checked out at the repository root rather than
#: inside ``core``.  The root was chosen so that a Playwright suite driving
#: the same pages' JavaScript could share it, each ``<project>/Dockerfile``
#: copying only its own directory.  That suite arrived as
#: ``info_pages_e2e`` (gain#987) and shares
#: :mod:`gain.genomic_resources.testing.info_page_fixtures` instead -- the
#: sortable-table assertions need a contig its genome cannot measure, and
#: mini-GRR teaches rather than carrying traps.  So nothing outside this
#: suite reads the path today.
#:
#: ``parents[5]`` is the repository root: this file is at
#: ``core/tests/small/genomic_resources/info_pages/conftest.py``, and the
#: same arithmetic holds inside the CI image, where ``core`` sits at
#: ``/workspace/core`` and the fixture at ``/workspace/test_fixtures``.
MINI_GRR_SOURCE = (
    pathlib.Path(__file__).parents[5] / "test_fixtures" / "mini-GRR"
)

_SUBMODULE_PATH = "test_fixtures/mini-GRR"

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

# How long a worker waits for whichever worker is building.  The build is
# ~10s; this is a backstop.  It has to stay comfortably under
# `faulthandler_timeout` in pytest.ini (600s), because that timer is armed
# around each item's whole protocol including fixture setup -- a longer
# value here would be unreachable, and the run would die with a thread dump
# instead of the message below.
_BUILD_TIMEOUT_SECONDS = 300.0


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


@pytest.fixture(scope="session")
def built_grr(
    tmp_path_factory: pytest.TempPathFactory,
    worker_id: str,
) -> BuiltGRR:
    """The fixture GRR, built exactly once per session.

    A plain ``scope="session"`` fixture is once per *worker*, so under
    ``pytest -n 5`` it would run five builds of a ~10s artifact.  Instead the
    workers serialise on a lock in the run's shared temp root: the first in
    builds, the rest find the marker and reuse its output.

    ``filelock`` is a direct runtime dependency of ``gain-core``
    (``core/pyproject.toml``), so this adds none -- and it is held by the OS,
    which matters more than the convenience.  A worker killed mid-build
    (OOM, SIGKILL, segfault) releases it on death and leaves no marker, so
    the next worker in rebuilds; a hand-rolled ``O_CREAT | O_EXCL`` lock
    would be left behind by that same death and wedge every other worker
    until the run timed out.

    The marker is written while the lock is held and read the same way, so
    no reader can catch it between creation and its contents.

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

    with FileLock(
        str(shared_root / "info_pages_grr.lock"),
        timeout=_BUILD_TIMEOUT_SECONDS,
    ):
        if done_marker.is_file():
            return BuiltGRR(
                path=repo_dir,
                build_started=float(done_marker.read_text()),
            )
        # No marker, but a directory: a previous worker died part-way
        # through its build.  Its output is half-written, so start over
        # rather than validate whatever it managed to produce.
        if repo_dir.exists():
            shutil.rmtree(repo_dir)
        started = time.time()
        _build_into(repo_dir)
        done_marker.write_text(str(started))

    return BuiltGRR(path=repo_dir, build_started=started)
