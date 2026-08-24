# pylint: disable=W0621,C0114,C0115,C0116,W0212,W0613
from collections.abc import Generator

import pytest
from dask.distributed import Client
from gain.task_graph.dask_executor import DaskExecutor
from gain.task_graph.executor import (
    TaskGraphExecutor,
)
from gain.task_graph.process_pool_executor import ProcessPoolTaskExecutor
from gain.task_graph.sequential_executor import SequentialExecutor


@pytest.fixture(scope="session")
def dask_client() -> Generator[Client, None, None]:
    # The client needs to be threaded b/c the global ORDER variable is modified
    # dashboard_address=None skips the bokeh dashboard server; without it every
    # client fights over port 8787 and pays a free-port scan on startup.
    #
    # Session-scoped: starting a LocalCluster is ~the whole cost of these
    # tests, so one cluster is shared across the session instead of built per
    # test. The tests only submit small task graphs (resetting the module-level
    # ORDER before each run), so a shared threaded client is safe to reuse.
    client = Client(
        n_workers=2, threads_per_worker=1, processes=False,
        dashboard_address=None,
    )
    yield client
    client.shutdown()
    client.close()


# The dask param carries the ownership marker (#851), so only the dask
# third of each consuming test is excused by the guard -- and the client
# is fetched lazily, so the sequential and process_pool runs never boot
# the session cluster at all.
@pytest.fixture(params=[
    pytest.param("dask", marks=pytest.mark.dask_executor),
    "sequential",
    "process_pool",
])
def executor(
    request: pytest.FixtureRequest,
) -> Generator[TaskGraphExecutor, None, None]:
    if request.param == "dask":
        # DaskExecutor.close() calls client.shutdown(), which tears the whole
        # cluster down. The client is session-scoped and shared, so we must NOT
        # close the executor here -- the dask_client fixture owns teardown.
        yield DaskExecutor(request.getfixturevalue("dask_client"))
        return

    executor: TaskGraphExecutor
    if request.param == "sequential":
        executor = SequentialExecutor()
    elif request.param == "process_pool":
        if not request.config.getoption("enable_pp"):
            pytest.skip("process_pool executor not enabled")
        executor = ProcessPoolTaskExecutor()
    else:
        raise ValueError(f"unknown executor type: {request.param}")

    yield executor
    executor.close()
