"""The running example, part 4, of the docs page "Named clusters".

The page ``docs/source/development/task_graph/named_clusters.rst`` pulls
the example code from this module with ``literalinclude``. The example runs
the graph of the page "Graphs", which ``test_docs_graphs`` builds, on a
dask cluster.
"""
import copy
import pathlib
from collections.abc import Generator
from typing import Any

import pytest
import pytest_mock
import yaml
from dask import config
from dask.distributed import Client
from gain.dask.named_cluster import setup_client, setup_client_from_config
from gain.genomic_resources.repository import GenomicResource
from gain.task_graph.cli_tools import task_graph_run_with_results
from gain.task_graph.dask_executor import DaskExecutor

from .test_docs_graphs import build_n_count_graph
from .test_docs_running_from_a_cli import (
    N_COUNT,
    grr_args,
    main,
    read_records,
)

# Keep the task log directory of each task executor out of the tree.
pytestmark = pytest.mark.usefixtures("work_in_tmp_path")


# The docs example of a low-cost cluster definition starts here.
# Two workers that are threads of this process, with no dashboard.
LOW_COST_CLUSTER: dict[str, Any] = {
    "type": "local",
    "number_of_workers": 2,
    "params": {"processes": False, "dashboard_address": None},
}
# The docs example of a low-cost cluster definition ends here.


# The docs example of a run on a dask client starts here.
def run_on_a_dask_client(resource: GenomicResource, client: Client) -> int:
    """Count the ``N`` bases of the genome on the cluster of ``client``."""
    graph, _ = build_n_count_graph(resource)
    # The task executor owns the client. Closing it shuts down the cluster.
    with DaskExecutor(client) as executor:
        results = list(task_graph_run_with_results(graph, executor))
    total: int = results[-1]
    return total
# The docs example of a run on a dask client ends here.


def named_cluster_config() -> dict[str, Any]:
    """Return a ``gain_named_cluster`` mapping with two named clusters."""
    return {
        "distributed": {
            "scheduler": {"unknown-task-duration": "5m"},
            "worker": {"memory": {"pause": 0.99}},
        },
        "default": "small",
        "clusters": {
            "small": {"type": "local", "params": {"memory_limit": "1GB"}},
            "large": {"type": "local", "params": {"memory_limit": "8GB"}},
        },
    }


def test_setup_client_with_no_name_selects_the_default_named_cluster(
    mocker: pytest_mock.MockerFixture,
) -> None:
    """``None`` as the name builds the cluster of the default."""
    local_cluster = mocker.patch(
        "dask.distributed.LocalCluster", autospec=True)
    mocker.patch("gain.dask.named_cluster.Client", autospec=True)

    with config.set({"gain_named_cluster": named_cluster_config()}):
        _, cluster_definition = setup_client(None)

    assert local_cluster.call_args.kwargs["memory_limit"] == "1GB"
    assert cluster_definition["params"]["memory_limit"] == "1GB"


def test_setup_client_from_config_gives_the_worker_count_to_the_cluster(
    mocker: pytest_mock.MockerFixture,
) -> None:
    """The worker count of the call wins over the cluster definition."""
    local_cluster = mocker.patch(
        "dask.distributed.LocalCluster", autospec=True)
    mocker.patch("gain.dask.named_cluster.Client", autospec=True)
    cluster_definition = {
        "type": "local", "number_of_workers": 5, "params": {}}

    with config.set({"gain_named_cluster": named_cluster_config()}):
        setup_client_from_config(cluster_definition, number_of_workers=3)

    assert local_cluster.call_args.kwargs["n_workers"] == 3
    local_cluster.return_value.scale.assert_called_once_with(n=3)


@pytest.fixture(name="restore_dask_config")
def fixture_restore_dask_config() -> Generator[None, None, None]:
    """Restore the dask config after a test that builds a real cluster.

    ``setup_client_from_config`` changes the global dask config, so the
    changes of one test do not reach the next test.
    """
    saved = copy.deepcopy(config.config)
    yield
    config.config.clear()
    config.config.update(saved)


@pytest.mark.dask_executor
@pytest.mark.usefixtures("restore_dask_config")
def test_dask_executor_runs_the_graph_and_closes_the_client(
    genome_resource: GenomicResource,
) -> None:
    """The task executor runs the graph, then shuts down its client."""
    # The docs example of a client from a cluster definition starts here.
    client, _ = setup_client_from_config(copy.deepcopy(LOW_COST_CLUSTER))
    # The docs example of a client from a cluster definition ends here.

    total = run_on_a_dask_client(genome_resource, client)

    assert total == N_COUNT
    assert client.status == "closed"


@pytest.mark.dask_executor
@pytest.mark.usefixtures("cli_context_provider", "restore_dask_config")
def test_cluster_config_file_run_writes_the_sum_into_the_summary_record(
    genome_grr_dir: pathlib.Path, tmp_path: pathlib.Path,
) -> None:
    """A run with ``-c`` on the low-cost cluster computes the sum."""
    cluster_file = tmp_path / "cluster.yaml"
    cluster_file.write_text(yaml.safe_dump(LOW_COST_CLUSTER))
    status_dir = tmp_path / "task-status"

    assert main([
        *grr_args(genome_grr_dir),
        "-c", str(cluster_file), "-j", "2", "-d", str(status_dir)])

    records = read_records(genome_grr_dir, status_dir)
    assert records["sum_n"][1] == N_COUNT
