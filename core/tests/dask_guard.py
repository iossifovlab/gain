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
still lands inside some test's protocol.  Both raise, with the marker
and the ``-j 1`` remedy in the message.  A CLI run in a *subprocess* is
out of the probe's reach; those call sites are policed by review and by
the cost showing up in ``--durations``.
"""
import functools
from collections.abc import Generator

import pytest
from distributed import Client

DASK_EXECUTOR_MARKER = "dask_executor"

# Single-assignment holder (pylint treats a reassigned module-level name
# as a mis-cased constant); the "item" slot is the test whose runtest
# protocol is currently executing in this process, if any.
_CURRENT: dict[str, pytest.Item | None] = {"item": None}


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{DASK_EXECUTOR_MARKER}: mark test as deliberately exercising "
        f"the dask distributed executor path; unmarked tests that "
        f"construct a dask Client fail the ownership guard (gain#851)")
    _install_client_probe()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(
    item: pytest.Item,
) -> Generator[None, object, object]:
    """Track the test whose protocol is running, for the probe."""
    _CURRENT["item"] = item
    try:
        return (yield)
    finally:
        _CURRENT["item"] = None


def _install_client_probe() -> None:
    # ``functools.wraps`` stamps ``__wrapped__`` on the probe, which is
    # also the idempotency check: a second ``pytest_configure`` (e.g. a
    # nested pytester run in the same process) finds it and leaves the
    # already-wrapped ``__init__`` alone.
    original_init = Client.__init__
    if hasattr(original_init, "__wrapped__"):
        return

    @functools.wraps(original_init)
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

    Client.__init__ = guarding_init  # type: ignore[method-assign]
