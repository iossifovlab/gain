"""A test must own the dask cluster it boots (#851).

``TaskGraphCli.create_executor`` answers ``jobs=None`` -- what every
task-graph-bearing ``grr_manage`` subcommand passes when a test omits
``-j`` -- with a dask ``LocalCluster`` sized to the machine.  Booting
and tearing one down costs ~60 CPU-seconds, so a test that does it by
accident is two orders of magnitude more expensive than the same test
run sequentially, and 32 xdist workers doing it concurrently starve
the host.

So a test that constructs a dask ``Client`` must say the dask executor
path is its subject, with ``@pytest.mark.dask_executor``.  Everything
else must pass ``"-j", "1"`` to its ``cli_manage`` calls; if this guard
fails a test, the fix is almost always the ``-j 1``, not the marker.

The probe wraps ``Client.__init__`` once per process and refuses the
construction on the spot whenever the test currently running is not
marked.  Enforcing at construction time is what makes fixture scope
irrelevant: a session- or module-scoped fixture instantiates during its
first requesting test's setup -- before any function-scoped autouse
fixture could snapshot state -- and a construction in a late finalizer
still lands inside some test's protocol.

The probe installs only once ``distributed`` is in ``sys.modules``
(checked again before each test): importing it here unconditionally
would bill ~0.2s of startup to every targeted run that never goes near
dask.  Out of the probe's reach, by construction: a CLI run in a
*subprocess*, and a construction inside the very test that first
imports ``distributed`` in a run whose collection never did (a
whole-suite run always does, via the task_graph conftest).  Those are
policed by review and by the cost showing up in ``--durations``.

Registered from ``pytest.ini``'s ``addopts`` (``-p tests.dask_guard``);
the meta-tests' nested runs load it via ``pytest_plugins`` instead, so
the marker is registered here rather than in the ini.
"""
import sys
from collections.abc import Generator

import pytest

DASK_EXECUTOR_MARKER = "dask_executor"

# Single-assignment holder (pylint reads a reassigned module-level name
# as a mis-cased constant) for the test whose protocol is running.
_CURRENT: dict[str, pytest.Item | None] = {"item": None}


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{DASK_EXECUTOR_MARKER}: mark test as deliberately exercising "
        f"the dask distributed executor path; unmarked tests that "
        f"construct a dask Client fail the ownership guard (gain#851)")
    _install_client_probe_if_loaded()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(
    item: pytest.Item,
) -> Generator[None, object, object]:
    """Track the test whose protocol is running, for the probe."""
    _install_client_probe_if_loaded()
    previous = _CURRENT["item"]
    _CURRENT["item"] = item
    try:
        return (yield)
    finally:
        _CURRENT["item"] = previous


def _install_client_probe_if_loaded() -> None:
    if "distributed" not in sys.modules:
        return
    from distributed import (  # pylint: disable=import-outside-toplevel
        Client,
    )

    original_init = Client.__init__
    if getattr(original_init, "gain_dask_guard", False):
        return

    def guarding_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        item = _CURRENT["item"]
        if item is not None and \
                item.get_closest_marker(DASK_EXECUTOR_MARKER) is None:
            raise RuntimeError(
                f"{item.nodeid} constructed a dask distributed Client "
                "without owning it; pass '-j', '1' to the "
                "cli_manage/TaskGraphCli invocation so it runs the "
                "SequentialExecutor, or -- only if the dask executor "
                "path is what this test exists to exercise -- mark it "
                f"with @pytest.mark.{DASK_EXECUTOR_MARKER} (gain#851)")
        return original_init(self, *args, **kwargs)

    guarding_init.gain_dask_guard = True  # type: ignore[attr-defined]
    Client.__init__ = guarding_init  # type: ignore[method-assign]
