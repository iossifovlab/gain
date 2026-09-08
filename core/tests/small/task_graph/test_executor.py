# pylint: disable=W0621,C0114,C0115,C0116,W0212,W0613
import operator
import time
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from gain.task_graph import base_executor
from gain.task_graph.cache import FileTaskCache
from gain.task_graph.cli_tools import (
    task_graph_run_with_results,
)
from gain.task_graph.executor import (
    TaskGraphExecutor,
)
from gain.task_graph.graph import TaskGraph
from gain.task_graph.sequential_executor import SequentialExecutor


def do_work(timeout: float) -> None:
    if timeout != 0:
        time.sleep(timeout)


def double(x: int) -> int:
    return x * 2


def noop(*args: Any, **kwargs: Any) -> None:
    pass


class _CloseRaisingExecutor(TaskGraphExecutor):
    """Minimal executor whose teardown fails, to exercise __exit__."""

    def __init__(self) -> None:
        self.closed = False

    def execute(self, graph: TaskGraph) -> Any:
        # A generator, not `iter(())`: closing the result generator is how a
        # run is told to tear down, so an executor that hands back a plain
        # iterator has no close() for `task_graph_run_with_results` to call
        # (gain#480). Nothing here routes through that, but a test executor
        # should not be a counterexample to the interface it implements.
        yield from ()

    def get_completed_tasks(self, graph: TaskGraph) -> Any:
        return iter(())

    def close(self) -> None:
        self.closed = True
        raise TimeoutError("workers did not terminate in time")


def test_exit_suppresses_close_failure() -> None:
    """A teardown failure during context-manager exit must not crash a run
    whose work already completed (iossifovlab/gain#127)."""
    executor = _CloseRaisingExecutor()
    with executor:
        pass  # clean body; work is "done"
    assert executor.closed  # close() was attempted, its error swallowed


def test_exit_does_not_mask_body_exception() -> None:
    """A teardown failure must not swallow or replace an error raised inside
    the ``with`` body (iossifovlab/gain#127)."""
    executor = _CloseRaisingExecutor()
    with pytest.raises(ValueError, match="real failure"), executor:
        raise ValueError("real failure")
    assert executor.closed


def test_dependency_chain(executor: TaskGraphExecutor) -> None:
    graph = TaskGraph()
    task_1 = graph.create_task("Task 1", do_work, args=[0.01], deps=[])
    graph.create_task("Task 2", do_work, args=[0], deps=[task_1])

    tasks_in_finish_order = [task for task, _ in executor.execute(graph)]
    ids_in_finish_order = [task.task_id for task in tasks_in_finish_order]
    assert ids_in_finish_order == ["Task 1", "Task 2"]


def test_dependent_task_recalculation(
    tmp_path: Path,
) -> None:
    graph = TaskGraph()
    dep = graph.create_task("dep", noop, args=[], deps=[])
    task = graph.create_task("task", noop, args=[], deps=[dep])
    cache = FileTaskCache(cache_dir=str(tmp_path))
    cache.cache(task, is_error=False, result="test")

    executor = SequentialExecutor(task_cache=cache)

    completed_tasks = list(executor.get_completed_tasks(graph))
    assert len(completed_tasks) == 0

    cache.cache(dep, is_error=False, result="test")

    completed_tasks = list(executor.get_completed_tasks(graph))
    assert len(completed_tasks) == 2


def test_multiple_dependancies(executor: TaskGraphExecutor) -> None:
    graph = TaskGraph()

    tasks = []
    for i in range(10):
        task = graph.create_task(f"Task {i}", do_work, args=[0], deps=[])
        tasks.append(task)

    for i in range(100, 105):
        graph.create_task(f"Task {i}", do_work, args=[0], deps=tasks)

    tasks_in_finish_order = [task for task, _ in executor.execute(graph)]
    ids_in_finish_order = [task.task_id for task in tasks_in_finish_order]

    assert len(ids_in_finish_order) == 15


def add_to_list(what: int, where: list[int]) -> list[int]:
    where.append(what)
    return where


def test_implicit_dependancies(executor: TaskGraphExecutor) -> None:
    graph = TaskGraph()

    last_task = graph.create_task("0", add_to_list, args=[0, []], deps=[])
    for i in range(1, 9):
        last_task = graph.create_task(
            f"{i}", add_to_list, args=[i, last_task], deps=[])

    last_task = graph.create_task(
        "9", add_to_list, args=[9, last_task], deps=[])

    full_list = []
    for task, result in executor.execute(graph):
        print(task, result)
        if task == last_task:
            full_list = result

    assert full_list == list(range(10))


def _create_graph_with_result_passing() -> TaskGraph:
    def add_to_list(what: int, where: list[int]) -> list[int]:
        return [*where, what]

    def concat_lists(*lists: list[int]) -> list[int]:
        res = []
        for next_list in lists:
            res.extend(next_list)
        return res

    # create the task graph
    graph = TaskGraph()
    first_task = graph.create_task("0", add_to_list, args=[0, []], deps=[])
    add_tasks = [
        graph.create_task(f"{i}", add_to_list, args=[i, first_task], deps=[])
        for i in range(1, 8)
    ]
    graph.create_task("final", concat_lists, args=add_tasks, deps=[])
    return graph


def raise_exception() -> None:
    raise ValueError("Task failed")


def test_error_handling(executor: TaskGraphExecutor) -> None:
    graph = TaskGraph()
    task_1 = graph.create_task("Task 1", do_work, args=[0], deps=[])
    task_2 = graph.create_task("Task 2", raise_exception, args=[], deps=[])
    graph.create_task("Task 3", do_work, args=[0], deps=[task_1, task_2])

    with pytest.raises(ValueError, match="Task failed"):
        list(task_graph_run_with_results(
            graph, executor, keep_going=False,
        ))


def test_error_handling_keep_going(
    executor: TaskGraphExecutor,
) -> None:
    graph = TaskGraph()
    task_1 = graph.create_task("Task 1", do_work, args=[0], deps=[])
    task_2 = graph.create_task("Task 2", raise_exception, args=[], deps=[])
    graph.create_task("Task 3", do_work, args=[0], deps=[task_1])
    graph.create_task("Task 4", do_work, args=[0], deps=[task_2])

    results = list(executor.execute(graph))
    assert len(results) == 3

    error_tasks = []
    ok_tasks = []
    for task, result in results:
        if isinstance(result, ValueError):
            error_tasks.append(task.task_id)
        else:
            ok_tasks.append(task.task_id)

    assert "Task 2" in error_tasks
    assert "Task 1" in ok_tasks
    assert "Task 3" in ok_tasks
    assert "Task 4" not in ok_tasks
    assert "Task 4" not in error_tasks


def test_diamond_graph(executor: TaskGraphExecutor) -> None:
    graph = TaskGraph()
    task_a = graph.create_task("A", do_work, args=[0.01], deps=[])
    task_b = graph.create_task("B", do_work, args=[0.01], deps=[task_a])
    task_c = graph.create_task("C", do_work, args=[0.01], deps=[task_a])
    graph.create_task("D", do_work, args=[0.01], deps=[task_b, task_c])

    tasks_in_finish_order = [task for task, _ in executor.execute(graph)]
    ids_in_finish_order = [task.task_id for task in tasks_in_finish_order]

    assert len(ids_in_finish_order) == 4
    assert ids_in_finish_order[0] == "A"
    assert set(ids_in_finish_order[1:3]) == {"B", "C"}
    assert ids_in_finish_order[3] == "D"


def return_5() -> int:
    return 5


def test_result_passing_chain(executor: TaskGraphExecutor) -> None:
    graph = TaskGraph()
    task_a = graph.create_task("A", return_5, args=[], deps=[])
    task_b = graph.create_task("B", operator.add, args=[task_a, 3], deps=[])
    graph.create_task("C", operator.mul, args=[task_b, 2], deps=[])

    results = {
        task.task_id: result for task, result in executor.execute(graph)
    }

    assert results["A"] == 5
    assert results["B"] == 8
    assert results["C"] == 16


def test_wide_parallel_execution(executor: TaskGraphExecutor) -> None:
    # Test many independent tasks that can run in parallel
    graph = TaskGraph()
    num_tasks = 50

    for i in range(num_tasks):
        graph.create_task(f"WideTask{i}", double, args=[i], deps=[])

    results = {
        task.task_id: result for task, result in executor.execute(graph)
    }

    assert len(results) == num_tasks
    for i in range(num_tasks):
        assert results[f"WideTask{i}"] == i * 2


def test_empty_graph(executor: TaskGraphExecutor) -> None:
    graph = TaskGraph()
    results = list(executor.execute(graph))
    assert len(results) == 0


def return_42() -> int:
    return 42


def test_single_task_graph(executor: TaskGraphExecutor) -> None:
    # Was xfailed as "flaky. Needs fixing." until gain#365: a one-task
    # graph is the shape most exposed to the run loop's termination race,
    # because it has exactly one submission to lose. Under the same
    # delayed-``map()`` probe that the gain#365 regression test uses, this
    # graph yielded 0 of 1 results before the fix and 1 of 1 after.
    graph = TaskGraph()
    graph.create_task("Only", return_42, args=[], deps=[])

    results = {
        task.task_id: result for task, result in executor.execute(graph)
    }

    assert len(results) == 1
    assert results["Only"] == 42


def return_1_2() -> list[int]:
    return [1, 2]


def return_3_4() -> list[int]:
    return [3, 4]


def return_5_6() -> list[int]:
    return [5, 6]


def merge_lists(*lists: list[int]) -> list[int]:
    # Merge results
    result = []
    for lst in lists:
        result.extend(lst)
    return result


def test_complex_result_aggregation(
    executor: TaskGraphExecutor,
) -> None:
    # Test complex data flow with multiple branches merging
    graph = TaskGraph()

    # Create initial tasks that produce lists
    task_a = graph.create_task("A", return_1_2, args=[], deps=[])
    task_b = graph.create_task("B", return_3_4, args=[], deps=[])
    task_c = graph.create_task("C", return_5_6, args=[], deps=[])

    graph.create_task(
        "Merge", merge_lists, args=[task_a, task_b, task_c], deps=[])

    results = {
        task.task_id: result for task, result in executor.execute(graph)
    }

    assert results["A"] == [1, 2]
    assert results["B"] == [3, 4]
    assert results["C"] == [5, 6]
    assert results["Merge"] == [1, 2, 3, 4, 5, 6]


def _touch(filename: str) -> None:
    Path(filename).touch()


def test_output_files_skip_recompute(tmp_path: Path) -> None:
    config_fn = str(tmp_path / "config.yaml")
    out_fn = str(tmp_path / "result.txt")
    call_count: list[int] = [0]

    def write_output() -> None:
        call_count[0] += 1
        Path(out_fn).write_text("done")

    _touch(config_fn)

    graph = TaskGraph()
    graph.input_files.append(config_fn)
    graph.create_task(
        "A", write_output, args=[],
        output_files=[out_fn],
    )

    cache_dir = str(tmp_path / "cache")
    Path(cache_dir).mkdir()

    executor = SequentialExecutor(FileTaskCache(cache_dir=cache_dir))
    list(executor.execute(graph))
    assert call_count[0] == 1
    assert Path(out_fn).exists()

    graph2 = TaskGraph()
    graph2.input_files.append(config_fn)
    graph2.create_task(
        "A", write_output, args=[],
        output_files=[out_fn],
    )
    executor2 = SequentialExecutor(FileTaskCache(cache_dir=cache_dir))
    list(executor2.execute(graph2))

    assert call_count[0] == 1  # not called again


def test_output_files_deleted_by_later_task(tmp_path: Path) -> None:
    config_fn = str(tmp_path / "config.yaml")
    intermediate_fn = str(tmp_path / "intermediate.txt")
    count_a: list[int] = [0]
    count_b: list[int] = [0]

    def task_a_func() -> None:
        count_a[0] += 1
        Path(intermediate_fn).write_text("intermediate")

    def task_b_func() -> None:
        count_b[0] += 1
        Path(intermediate_fn).unlink(missing_ok=True)

    _touch(config_fn)

    graph = TaskGraph()
    graph.input_files.append(config_fn)
    task_a = graph.create_task(
        "A", task_a_func, args=[],
        intermediate_output_files=[intermediate_fn],
    )
    graph.create_task("B", task_b_func, args=[], deps=[task_a])

    cache_dir = str(tmp_path / "cache")
    Path(cache_dir).mkdir()

    executor = SequentialExecutor(FileTaskCache(cache_dir=cache_dir))
    list(executor.execute(graph))
    assert count_a[0] == 1
    assert count_b[0] == 1
    assert not Path(intermediate_fn).exists()

    # Second run: intermediate file was deleted by B, but both tasks should
    # still be considered cached (A via flag file, B normally).
    graph2 = TaskGraph()
    graph2.input_files.append(config_fn)
    task_a2 = graph2.create_task(
        "A", task_a_func, args=[],
        intermediate_output_files=[intermediate_fn],
    )
    graph2.create_task("B", task_b_func, args=[], deps=[task_a2])

    executor2 = SequentialExecutor(FileTaskCache(cache_dir=cache_dir))
    list(executor2.execute(graph2))

    assert count_a[0] == 1  # not called again
    assert count_b[0] == 1  # not called again


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    result = tmp_path / "cache"
    result.mkdir()
    return result


def _run_with_file_cache(graph: TaskGraph, cache_dir: Path) -> None:
    executor = SequentialExecutor(FileTaskCache(cache_dir=str(cache_dir)))
    list(executor.execute(graph))


def _completed_task_ids(graph: TaskGraph, cache_dir: Path) -> set[str]:
    executor = SequentialExecutor(FileTaskCache(cache_dir=str(cache_dir)))
    return {
        task.task_id for task, _ in executor.get_completed_tasks(graph)
    }


def _chunked_writer_graph(
    tmp_path: Path, count: int, *, input_files: list[str] | None = None,
) -> tuple[TaskGraph, list[str]]:
    """Build ``count`` chunk producers feeding one writer.

    Each producer declares its chunk as an intermediate output; the writer
    depends on every producer and, unless ``input_files`` overrides it,
    declares every chunk file as an input.
    """
    chunk_dir = tmp_path / "chunks"
    chunk_dir.mkdir(exist_ok=True)
    chunk_files = [str(chunk_dir / f"chunk_{i}") for i in range(count)]
    graph = TaskGraph()
    producers = [
        graph.create_task(
            f"chunk_{i}", _touch, args=[chunk_files[i]],
            intermediate_output_files=[chunk_files[i]],
        )
        for i in range(count)
    ]
    out_fn = str(tmp_path / "out.txt")
    graph.create_task(
        "writer", _touch, args=[out_fn], deps=producers,
        input_files=chunk_files if input_files is None else input_files,
        output_files=[out_fn],
    )
    return graph, chunk_files


def _unlink_all(filenames: list[str]) -> None:
    for filename in filenames:
        Path(filename).unlink()


def test_missing_input_invalidates_every_producing_ancestor(
    tmp_path: Path, cache_dir: Path,
) -> None:
    graph, chunk_files = _chunked_writer_graph(tmp_path, 5)
    _run_with_file_cache(graph, cache_dir)
    _unlink_all(chunk_files)

    graph2, _ = _chunked_writer_graph(tmp_path, 5)
    completed = _completed_task_ids(graph2, cache_dir)

    assert completed == set()


@pytest.mark.parametrize("count", [20, 80])
def test_cache_reconciliation_walks_ancestors_once_per_consumer(
    tmp_path: Path, cache_dir: Path, count: int,
) -> None:
    """A consumer with T missing produced inputs costs one ancestor walk."""
    graph, chunk_files = _chunked_writer_graph(tmp_path, count)
    _run_with_file_cache(graph, cache_dir)
    _unlink_all(chunk_files)
    graph2, _ = _chunked_writer_graph(tmp_path, count)

    with (
        mock.patch.object(
            base_executor.networkx, "ancestors",
            wraps=base_executor.networkx.ancestors,
        ) as walks,
        mock.patch.object(
            graph2, "get_task_desc", wraps=graph2.get_task_desc,
        ) as descs,
    ):
        _completed_task_ids(graph2, cache_dir)

    assert walks.call_count == 1
    assert descs.call_count <= count + 1


def test_missing_input_nobody_produces_keeps_the_cache(
    tmp_path: Path, cache_dir: Path,
) -> None:
    external_fn = str(tmp_path / "external.txt")
    _touch(external_fn)
    graph, _ = _chunked_writer_graph(tmp_path, 3, input_files=[external_fn])
    _run_with_file_cache(graph, cache_dir)
    Path(external_fn).unlink()

    graph2, _ = _chunked_writer_graph(tmp_path, 3, input_files=[external_fn])
    completed = _completed_task_ids(graph2, cache_dir)

    assert completed == {"chunk_0", "chunk_1", "chunk_2"}


def _shared_file_graph(
    tmp_path: Path, producer_names: list[str], *, consumer_depends: bool,
) -> TaskGraph:
    """Every producer declares one file; a consumer names it as its input."""
    shared_fn = str(tmp_path / "shared.txt")
    graph = TaskGraph()
    producers = [
        graph.create_task(
            name, _touch, args=[shared_fn],
            intermediate_output_files=[shared_fn],
        )
        for name in producer_names
    ]
    out_fn = str(tmp_path / "out.txt")
    graph.create_task(
        "consumer", _touch, args=[out_fn],
        deps=producers if consumer_depends else None,
        input_files=[shared_fn], output_files=[out_fn],
    )
    return graph


def test_missing_input_does_not_invalidate_a_producer_it_does_not_depend_on(
    tmp_path: Path, cache_dir: Path,
) -> None:
    _run_with_file_cache(
        _shared_file_graph(tmp_path, ["producer"], consumer_depends=False),
        cache_dir)
    (tmp_path / "shared.txt").unlink()

    completed = _completed_task_ids(
        _shared_file_graph(tmp_path, ["producer"], consumer_depends=False),
        cache_dir)

    assert completed == {"producer"}


def test_missing_input_invalidates_every_ancestor_that_declares_it(
    tmp_path: Path, cache_dir: Path,
) -> None:
    names = ["first", "second"]
    _run_with_file_cache(
        _shared_file_graph(tmp_path, names, consumer_depends=True),
        cache_dir)
    (tmp_path / "shared.txt").unlink()

    completed = _completed_task_ids(
        _shared_file_graph(tmp_path, names, consumer_depends=True),
        cache_dir)

    assert completed == set()
