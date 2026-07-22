# pylint: disable=W0621,C0114,C0115,C0116,W0212,W0613
import asyncio
import gc
import pathlib
import threading
import time
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
