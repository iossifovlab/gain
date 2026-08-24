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

The probe wraps ``Client.__init__`` once per process, so it sees every
in-process construction -- ``setup_client``, a conftest fixture, or a
direct ``Client(...)`` -- whichever test's setup/call/teardown it lands
in.  A CLI run in a *subprocess* is out of its reach; those call sites
are policed by review and by the cost showing up in ``--durations``.
"""
import functools
from collections.abc import Generator

import pytest
from distributed import Client

DASK_EXECUTOR_MARKER = "dask_executor"

_client_constructions: list[str] = []


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{DASK_EXECUTOR_MARKER}: mark test as deliberately exercising "
        f"the dask distributed executor path; unmarked tests that "
        f"construct a dask Client fail the ownership guard (gain#851)")
    _install_client_probe()


def _install_client_probe() -> None:
    # ``functools.wraps`` stamps ``__wrapped__`` on the probe, which is
    # also the idempotency check: a second ``pytest_configure`` (e.g. a
    # nested pytester run in the same process) finds it and leaves the
    # already-wrapped ``__init__`` alone.
    original_init = Client.__init__
    if hasattr(original_init, "__wrapped__"):
        return

    @functools.wraps(original_init)
    def recording_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        _client_constructions.append(type(self).__name__)
        return original_init(self, *args, **kwargs)

    Client.__init__ = recording_init  # type: ignore[method-assign]


@pytest.fixture(autouse=True)
def dask_clusters_are_owned(
    request: pytest.FixtureRequest,
) -> Generator[None, None, None]:
    """Fail a test that boots a dask cluster without owning one.

    See the module docstring: the delta in recorded ``Client``
    constructions over this test's whole protocol (setup + call +
    teardown) is charged to it, so a cluster built inside a fixture is
    caught at the first test that requests it.
    """
    before = len(_client_constructions)
    yield
    if len(_client_constructions) == before:
        return
    if request.node.get_closest_marker(DASK_EXECUTOR_MARKER) is not None:
        return
    pytest.fail(
        "constructed a dask distributed Client without owning it; pass "
        "'-j', '1' to the cli_manage/TaskGraphCli invocation so it runs "
        "the SequentialExecutor, or -- only if the dask executor path is "
        "what this test exists to exercise -- mark it with "
        f"@pytest.mark.{DASK_EXECUTOR_MARKER}")
