"""The running example, part 2, of the docs page "Task executors".

The page ``docs/source/development/task_graph/task_executors.rst`` pulls
the example code from this module with ``literalinclude``. The example runs
the graph of the page "Graphs", which ``test_docs_graphs`` builds.
"""
import pathlib
from typing import Any

import pytest
from gain.genomic_resources.repository import GenomicResource
from gain.task_graph.cache import CacheRecord, CacheRecordType, FileTaskCache
from gain.task_graph.cli_tools import (
    task_graph_run,
    task_graph_run_with_results,
)
from gain.task_graph.graph import Task
from gain.task_graph.process_pool_executor import ProcessPoolTaskExecutor
from gain.task_graph.sequential_executor import SequentialExecutor

from .test_docs_graphs import build_n_count_graph

# The fixture genome has 5, 0 and 2 ``N`` bases in its three chromosomes.
N_COUNT = 7


# The docs example of a sequential run starts here.
def run_sequentially(resource: GenomicResource) -> int:
    """Count the ``N`` bases of the genome, one task at a time."""
    graph, _ = build_n_count_graph(resource)
    with SequentialExecutor() as executor:
        results = list(task_graph_run_with_results(graph, executor))
    # The summary task depends on all the other tasks, so it completes last.
    total: int = results[-1]
    return total
# The docs example of a sequential run ends here.


# The docs example of a process pool run starts here.
def run_in_process_pool(resource: GenomicResource) -> int:
    """Count the ``N`` bases of the genome in a pool of two processes."""
    graph, _ = build_n_count_graph(resource)
    with ProcessPoolTaskExecutor(max_workers=2) as executor:
        results = list(task_graph_run_with_results(graph, executor))
    total: int = results[-1]
    return total
# The docs example of a process pool run ends here.


# The docs example of a run with a task cache starts here.
def run_with_file_task_cache(
    resource: GenomicResource, status_dir: str,
) -> None:
    """Run the graph and write a record for each task into ``status_dir``."""
    graph, _ = build_n_count_graph(resource)
    task_cache = FileTaskCache(cache_dir=status_dir)
    with SequentialExecutor(task_cache=task_cache) as executor:
        assert task_graph_run(graph, executor)


def rerun_with_file_task_cache(
    resource: GenomicResource, status_dir: str,
) -> tuple[list[tuple[Task, Any]], dict[str, CacheRecord]]:
    """Run a fresh graph over the records of an earlier run."""
    graph, _ = build_n_count_graph(resource)
    task_cache = FileTaskCache(cache_dir=status_dir)
    records = {
        task.task_id: task_cache.get_record(graph.get_task_desc(task))
        for task in graph.tasks
    }
    with SequentialExecutor(task_cache=task_cache) as executor:
        completed = list(executor.execute(graph))
    return completed, records
# The docs example of a run with a task cache ends here.


@pytest.fixture(autouse=True)
def _work_in_tmp_path(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the task log directory of each task executor out of the tree."""
    monkeypatch.chdir(tmp_path)


@pytest.fixture(name="process_pool_enabled")
def fixture_process_pool_enabled(request: pytest.FixtureRequest) -> None:
    """Skip a process pool test unless pytest gets ``--pp``."""
    if not request.config.getoption("enable_pp"):
        pytest.skip("process_pool executor not enabled")


def test_sequential_run_returns_the_sum_as_the_last_result(
    genome_resource: GenomicResource,
) -> None:
    """A sequential run yields the sum of the ``N`` counts last."""
    assert run_sequentially(genome_resource) == N_COUNT


@pytest.mark.usefixtures("process_pool_enabled")
def test_process_pool_run_returns_the_sum_as_the_last_result(
    genome_resource: GenomicResource,
) -> None:
    """A run in a process pool yields the same sum last."""
    assert run_in_process_pool(genome_resource) == N_COUNT


def test_rerun_with_a_new_file_task_cache_yields_nothing(
    genome_resource: GenomicResource, tmp_path: pathlib.Path,
) -> None:
    """A fresh graph over the same task status directory runs no task."""
    status_dir = str(tmp_path / "task-status")
    run_with_file_task_cache(genome_resource, status_dir)

    completed, _ = rerun_with_file_task_cache(genome_resource, status_dir)

    assert not completed


def test_rerun_finds_a_computed_record_for_each_task(
    genome_resource: GenomicResource, tmp_path: pathlib.Path,
) -> None:
    """Each task has a computed record, and the summary holds the sum."""
    status_dir = str(tmp_path / "task-status")
    run_with_file_task_cache(genome_resource, status_dir)

    _, records = rerun_with_file_task_cache(genome_resource, status_dir)

    assert {task_id: record.type for task_id, record in records.items()} == {
        "count_n_chr1": CacheRecordType.COMPUTED,
        "count_n_chr2": CacheRecordType.COMPUTED,
        "count_n_chr3": CacheRecordType.COMPUTED,
        "sum_n": CacheRecordType.COMPUTED,
    }
    assert records["sum_n"].result == N_COUNT


def test_file_task_cache_writes_into_the_task_status_directory(
    genome_resource: GenomicResource, tmp_path: pathlib.Path,
) -> None:
    """The records go into the task status directory, one for each task."""
    status_dir = tmp_path / "task-status"

    run_with_file_task_cache(genome_resource, str(status_dir))

    assert sorted(path.name for path in status_dir.iterdir()) == [
        "count_n_chr1.flag", "count_n_chr2.flag", "count_n_chr3.flag",
        "sum_n.flag"]


@pytest.mark.usefixtures("process_pool_enabled")
def test_process_pool_ignores_the_task_log_options(
    genome_resource: GenomicResource, tmp_path: pathlib.Path,
) -> None:
    """A process pool creates the task log directory but writes no log.

    The page "Task executors" says that the process pool ignores
    ``task_log_dir``, ``verbose`` and ``fork_tasks``.
    """
    log_dir = tmp_path / "task-log"
    graph, _ = build_n_count_graph(genome_resource)

    with ProcessPoolTaskExecutor(
            max_workers=2, task_log_dir=str(log_dir), verbose=2) as executor:
        assert task_graph_run(graph, executor)

    assert list(log_dir.iterdir()) == []


def test_rerun_runs_a_task_whose_record_is_an_error(
    genome_resource: GenomicResource, tmp_path: pathlib.Path,
) -> None:
    """A task runs again unless its record type is ``COMPUTED``.

    ``TaskCache.get_record`` documents this rule, so an ``ERROR`` record
    does not skip the task.
    """
    status_dir = str(tmp_path / "task-status")
    run_with_file_task_cache(genome_resource, status_dir)
    graph, _ = build_n_count_graph(genome_resource)
    failed = next(t for t in graph.tasks if t.task_id == "count_n_chr2")
    FileTaskCache(cache_dir=status_dir).cache(
        failed, is_error=True, result=ValueError("failed"))

    completed, records = rerun_with_file_task_cache(
        genome_resource, status_dir)

    assert records["count_n_chr2"].type == CacheRecordType.ERROR
    assert sorted(task.task_id for task, _ in completed) == [
        "count_n_chr2", "sum_n"]
