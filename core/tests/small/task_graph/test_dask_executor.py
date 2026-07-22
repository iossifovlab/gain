# pylint: disable=W0621,C0114,C0115,C0116,W0212,W0613
import asyncio
import gc
import pathlib
import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest
from dask.distributed import Client
from gain.task_graph.dask_executor import DaskExecutor
from gain.task_graph.executor import (
    TaskGraphExecutor,
)
from gain.task_graph.graph import Task, TaskGraph


def noop() -> None:
    pass


def test_close_tears_down_cluster_gracefully(
    tmp_path: pathlib.Path,
) -> None:
    """``close()`` must shut the cluster down with a single graceful
    ``shutdown()``, not by retiring/closing workers first.

    Regression for iossifovlab/gain#125: ``close()`` called
    ``retire_workers(close_workers=True)`` *before* ``shutdown()``, which
    races the scheduler teardown -- workers find the scheduler gone and emit a
    storm of heartbeat failures and "Connection ... closed" INFO lines (one per
    worker). ``shutdown()`` alone tears down workers and scheduler gracefully
    and silently.
    """
    client = MagicMock()
    executor = DaskExecutor(client, work_dir=str(tmp_path))

    executor.close()

    client.shutdown.assert_called_once()
    client.retire_workers.assert_not_called()


@pytest.mark.parametrize(
    "tasks,expected_order", [
        (  # 0: simple chain
            [
                ("A", []),
                ("B", ["A"]),
                ("C", ["B"]),
            ],
            [["A"], ["B"], ["C"]],
        ),
        (  # 1: diamond
            [
                ("A", []),
                ("B", ["A"]),
                ("C", ["A"]),
                ("D", ["B", "C"]),
            ],
            [["A"], ["B", "C"], ["D"]],
        ),
        (  # 2: wide graph
            [
                ("A", []),
                ("B", []),
                ("C", []),
                ("D", []),
                ("E", ["A", "B", "C", "D"]),
            ],
            [["A", "B", "C", "D"], ["E"]],
        ),
        (  # 3: complex graph
            [
                ("A", []),
                ("B", ["A"]),
                ("C", ["A"]),
                ("D", ["B"]),
                ("E", ["B", "C"]),
                ("F", ["D", "E"]),
            ],
            [["A"], ["B", "C"], ["D", "E"], ["F"]],
        ),
        (
            [  # 4: multiple roots
                ("A", []),
                ("B", []),
                ("C", ["A"]),
                ("D", ["B"]),
                ("E", ["C", "D"]),
            ],
            [["A", "B", "C", "D"], ["E"]],
        ),
        (
            [  # 5: multiple independent chains
                ("A1", []),
                ("B1", ["A1"]),
                ("C1", ["B1"]),
                ("A2", []),
                ("B2", ["A2"]),
                ("C2", ["B2"]),
            ],
            [["A1", "A2", "B1", "B2", "C1", "C2"]],
        ),
        (
            [  # 6: simple graph with numeric ids
                ("3", []),
                ("2", []),
                ("1", ["2", "3"]),
            ],
            [["2", "3"], ["1"]],
        ),
    ],
)
def test_dask_executor(
    executor: TaskGraphExecutor,
    tasks: list[tuple[str, list[str]]],
    expected_order: list[list[str]],
) -> None:
    graph = TaskGraph()
    for task_id, dep_ids in tasks:
        deps = [Task(dep_id) for dep_id in dep_ids]
        graph.create_task(task_id, noop, args=[], deps=deps)

    executed_tasks = list(executor.execute(graph))
    executed_task_ids: list[str] = [
        task.task_id for task, _ in executed_tasks]
    index = 0
    for expected_group in expected_order:
        executed_group = set()
        for _ in expected_group:
            executed_group.add(executed_task_ids[index])
            index += 1
        assert set(expected_group) == executed_group


def slow_task() -> int:
    time.sleep(1.5)
    return 1


def _live_asyncio_tasks() -> int:
    return sum(1 for obj in gc.get_objects() if isinstance(obj, asyncio.Task))


def test_execute_does_not_accumulate_asyncio_waiters(
    dask_client: Client,
) -> None:
    """The run loop must not mint a waiter per pending future per poll.

    Regression for iossifovlab/gain#355. The loop used to call
    ``wait(running, return_when="FIRST_COMPLETED", timeout=0.05)`` on every
    iteration. ``distributed`` implements that as
    ``Any({f._state.wait() for f in fs})``, which wraps EVERY still-pending
    future in ``asyncio.ensure_future`` and then never cancels the ones it
    loses the race to. At ~20 polls/s that abandoned one asyncio Task --
    plus a coroutine, a Timeout and a weakref -- per pending future per
    poll, and the driver grew ~220MB/min for the length of the run.

    The waiters do drain once their future resolves, so the leak is only
    visible WHILE tasks are in flight -- hence sampling during execute()
    rather than after it.
    """
    graph = TaskGraph()
    for i in range(8):
        graph.create_task(f"S{i}", slow_task, args=[], deps=[])

    executor = DaskExecutor(dask_client)

    samples: list[int] = []
    stop = threading.Event()

    def sampler() -> None:
        while not stop.is_set():
            samples.append(_live_asyncio_tasks())
            stop.wait(0.25)

    gc.collect()
    baseline = _live_asyncio_tasks()

    sampler_thread = threading.Thread(target=sampler, daemon=True)
    sampler_thread.start()
    try:
        executed = list(executor.execute(graph))
    finally:
        stop.set()
        sampler_thread.join()

    assert len(executed) == 8

    peak = max(samples, default=baseline)
    # With the poll, this graph abandoned ~8 waiters x ~20 polls/s for the
    # ~1.5s the tasks were in flight -- hundreds of live asyncio Tasks. With
    # one callback registered per future at submit time, the count tracks
    # in-flight futures instead of elapsed poll iterations.
    assert peak - baseline < 100, (
        f"live asyncio Task count went {baseline} -> {peak} while running "
        f"8 tasks: waiters are accumulating (gain#355)"
    )


def double(x: int) -> int:
    return x * 2


class _SlowSubmitClient:
    """A client whose ``map()`` takes longer than the run loop's wait.

    Stands in for a loaded machine, where handing a batch of tasks to the
    scheduler is not instant. Everything else is the real client.
    """

    def __init__(self, client: Client, delay: float) -> None:
        self._client = client
        self._delay = delay

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    def map(self, *args: Any, **kwargs: Any) -> Any:
        time.sleep(self._delay)
        return self._client.map(*args, **kwargs)


def test_slow_submission_does_not_end_the_run_early(
    dask_client: Client,
) -> None:
    """A submission slower than the run loop's wait must not end the run.

    Regression for iossifovlab/gain#365. The run loop calls a graph done
    when the graph is empty and nothing sits in ``submit_queue``,
    ``running``, ``completed_queue`` or ``results_queue``. The submit
    worker used to take its tasks off ``submit_queue`` and clear it
    *before* calling ``Client.map()``, and only recorded the futures in
    ``running`` once ``map()`` returned -- so for the whole width of that
    call a submitted task was in none of the four. A run loop that
    evaluated the check inside that window declared itself finished and
    returned having yielded NOTHING: not a wrong answer, an empty one,
    which the caller cannot tell from a graph with no work in it.

    The window is as wide as ``map()`` and the check is reached every
    0.05s, so on an unloaded machine this almost never happens -- CI hit
    it once in 23 builds. Delaying ``map()`` past that 0.05s makes it
    certain.
    """
    graph = TaskGraph()
    num_tasks = 50
    for i in range(num_tasks):
        graph.create_task(f"WideTask{i}", double, args=[i], deps=[])

    executor = DaskExecutor(_SlowSubmitClient(dask_client, delay=0.2))

    results = {
        task.task_id: result for task, result in executor.execute(graph)
    }

    assert len(results) == num_tasks, (
        f"executor yielded {len(results)} of {num_tasks} results; a run "
        f"whose submission outlasts the loop's wait ended early (gain#365)"
    )
    for i in range(num_tasks):
        assert results[f"WideTask{i}"] == i * 2
