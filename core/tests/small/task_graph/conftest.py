# pylint: disable=W0621,C0114,C0115,C0116,W0212,W0613
import pathlib
import textwrap
from collections.abc import Generator

import pytest
from dask.distributed import Client
from gain.genomic_resources.genomic_context import register_context_provider
from gain.genomic_resources.genomic_context_cli import (
    CLIGenomicContextProvider,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing import (
    build_filesystem_test_resource,
    setup_genome,
)
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


# The fixture genome of the docs examples of the task graph chapter. The
# chromosomes have 5, 0 and 2 ``N`` bases, and the examples count them.
_N_COUNT_GENOME = textwrap.dedent("""
    >chr1
    NNACGTACGT
    ACGTNNNACG
    >chr2
    ACGTACGTAC
    >chr3
    NACGTACGTN
""")


@pytest.fixture(name="genome_resource")
def fixture_genome_resource(tmp_path: pathlib.Path) -> GenomicResource:
    """Return a genome resource with three chromosomes.

    The chromosomes have 5, 0 and 2 ``N`` bases. The docs examples of the
    task graph chapter count them.
    """
    setup_genome(tmp_path / "genome.fa", _N_COUNT_GENOME)
    return build_filesystem_test_resource(tmp_path)


@pytest.fixture(name="genome_grr_dir")
def fixture_genome_grr_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """Return a directory GRR that holds the fixture genome.

    The genome has the resource id ``genome``, and its chromosomes have
    5, 0 and 2 ``N`` bases.
    """
    grr_dir = tmp_path / "grr"
    setup_genome(grr_dir / "genome" / "genome.fa", _N_COUNT_GENOME)
    return grr_dir


@pytest.fixture(name="work_in_tmp_path")
def fixture_work_in_tmp_path(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Make ``tmp_path`` the current directory of the test.

    A task executor writes its task log directory into the current
    directory, so the docs examples keep it out of the source tree.
    """
    monkeypatch.chdir(tmp_path)


@pytest.fixture(name="process_pool_enabled")
def fixture_process_pool_enabled(request: pytest.FixtureRequest) -> None:
    """Skip a process pool test unless pytest gets ``--pp``."""
    if not request.config.getoption("enable_pp"):
        pytest.skip("process_pool executor not enabled")


@pytest.fixture(name="cli_context_provider")
def fixture_cli_context_provider(
    clean_genomic_context_providers: None,
) -> None:
    """Register only the CLI context provider, for the test alone.

    ``clean_genomic_context_providers`` empties the registered providers,
    so the providers of one test do not reach the next test.
    """
    register_context_provider(CLIGenomicContextProvider())
