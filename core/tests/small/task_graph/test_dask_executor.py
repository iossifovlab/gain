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
from gain.task_graph.cache import CacheRecordType, FileTaskCache
from gain.task_graph.dask_executor import (
    RESULTS_WORKER_THREAD_NAME,
    SUBMIT_WORKER_THREAD_NAME,
    DaskExecutor,
)
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


class _WrappedClient:
    """A real client with one of its calls overridden by a subclass.

    Everything not overridden is the real client, so the run still goes
    through a real scheduler and real workers.
    """

    def __init__(self, client: Client) -> None:
        self._client = client

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


class _SlowClient(_WrappedClient):
    """A real client with one of its calls artificially slowed down.

    Stands in for a loaded machine, where talking to the scheduler is not
    instant.
    """

    def __init__(self, client: Client, delay: float) -> None:
        super().__init__(client)
        self._delay = delay


class _SlowSubmitClient(_SlowClient):
    """A client whose ``map()`` takes longer than the run loop's wait."""

    def map(self, *args: Any, **kwargs: Any) -> Any:
        time.sleep(self._delay)
        return self._client.map(*args, **kwargs)


class _SlowGatherClient(_SlowClient):
    """A client whose ``gather()`` takes longer than the run loop's wait."""

    def gather(self, *args: Any, **kwargs: Any) -> Any:
        time.sleep(self._delay)
        return self._client.gather(*args, **kwargs)


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


def test_slow_gather_does_not_end_the_run_early(
    dask_client: Client,
) -> None:
    """A gather slower than the run loop's wait must not end the run.

    Regression for iossifovlab/gain#367, and the mirror image of
    ``test_slow_submission_does_not_end_the_run_early``. A finished task
    stops being *running* the moment its callback fires and only becomes a
    *result* once ``Client.gather()`` has returned; for the whole width of
    that call it is in neither. Termination must still see it -- the
    results worker holds it in an explicit in-flight gather state, so the
    run loop counts it as outstanding the entire time.

    The window is as wide as ``gather()`` and the run loop reaches its
    termination check every 0.05s, so this only bites when gathering
    outlasts that wait; delaying ``gather()`` past it makes it certain.
    The test is only meaningful because no lock is held across the gather:
    an implementation that pulls the batch out of the completed collection
    and then releases its lock for the round trip yields 1 of 50 here.
    """
    graph = TaskGraph()
    num_tasks = 50
    for i in range(num_tasks):
        graph.create_task(f"WideTask{i}", double, args=[i], deps=[])

    executor = DaskExecutor(_SlowGatherClient(dask_client, delay=0.2))

    results = {
        task.task_id: result for task, result in executor.execute(graph)
    }

    assert len(results) == num_tasks, (
        f"executor yielded {len(results)} of {num_tasks} results; a run "
        f"whose gather outlasts the loop's wait ended early (gain#367)"
    )
    for i in range(num_tasks):
        assert results[f"WideTask{i}"] == i * 2


def die_on_the_worker(*args: Any, **kwargs: Any) -> Any:
    """Fail where nothing in the executor can catch it."""
    raise RuntimeError("the worker died")


class _DyingWorkerClient(_WrappedClient):
    """A client whose submitted work dies on the worker.

    ``_exec`` catches every ``Exception`` the task function raises and
    returns it as the task's *result*, so a task that merely raises never
    leaves a future in the ``error`` state. The failures that do -- an
    OOM-killed worker, a lost worker, a cancelled key -- cannot be
    provoked from inside the task function, so what dask is asked to run
    is replaced instead.
    """

    def map(self, _func: Any, tasks: Any, **kwargs: Any) -> Any:
        return self._client.map(die_on_the_worker, tasks, **kwargs)


def test_a_task_that_dies_on_the_worker_is_delivered_as_an_error(
    dask_client: Client,
    tmp_path: pathlib.Path,
) -> None:
    """A future that ends in ``error`` must be yielded as an exception.

    Regression for the silent-wrong-result mode this whole line of work
    exists to prevent. The results worker passed a *tuple* of futures to
    ``Client.gather(..., errors="skip")``; ``skip`` only drops bad futures
    when the container is a ``list`` -- for a tuple ``distributed`` packs
    them back as ``None`` and returns the same length. The length check
    therefore always matched, the "look for exceptions in the futures"
    fallback was unreachable, and an OOM-killed task was delivered with
    the value ``None``.

    Nothing raised, so ``is_error`` downstream was ``False`` and the task
    was written to the cache as a COMPUTED result of ``None`` -- a later
    cached run then skips it entirely. Hence the assertion on the cache
    record: the delivered value and what it makes the run believe are the
    same bug.
    """
    def a_graph() -> TaskGraph:
        graph = TaskGraph()
        graph.create_task("DoomedTask", double, args=[1], deps=[])
        return graph

    task_cache = FileTaskCache(cache_dir=str(tmp_path))
    executor = DaskExecutor(
        _DyingWorkerClient(dask_client),
        task_cache=task_cache,
        task_log_dir=str(tmp_path / "logs"),
    )

    results = dict(executor.execute(a_graph()))

    assert len(results) == 1, "the failed task was not delivered at all"
    result = results[Task("DoomedTask")]
    assert isinstance(result, BaseException), (
        f"a task whose future ended in error was delivered as {result!r}; "
        f"it must be delivered as an exception"
    )

    record = task_cache.get_record(
        a_graph().get_task_desc(Task("DoomedTask")))
    assert record.type == CacheRecordType.ERROR, (
        f"the failed task was cached as {record.type}; a crashed task "
        f"cached as COMPUTED is skipped by every later cached run"
    )


def _live_run_worker_threads() -> list[threading.Thread]:
    return [
        thread for thread in threading.enumerate()
        if thread.name in (
            SUBMIT_WORKER_THREAD_NAME, RESULTS_WORKER_THREAD_NAME)
    ]


def test_abandoning_the_result_iterator_shuts_the_run_down(
    dask_client: Client,
    tmp_path: pathlib.Path,
) -> None:
    """Closing the iterator early must tear the run down, not leak it.

    ``task_graph_run_with_results(..., keep_going=False)`` does
    ``raise result_or_error`` from inside its ``for ... in tasks_iter``
    loop, so the first failing task of any run without ``--keep-going``
    abandons this generator part way. The teardown -- shutdown, both
    joins, clearing ``_executing`` -- used to sit after the ``while`` loop
    unprotected, and ``GeneratorExit`` skipped all of it: both worker
    threads survived for the life of the process and the executor stayed
    permanently "executing", so the long-lived module-level executors in
    ``web_api`` could never run another graph.
    """
    def a_graph(prefix: str, count: int) -> TaskGraph:
        graph = TaskGraph()
        for i in range(count):
            graph.create_task(f"{prefix}{i}", double, args=[i], deps=[])
        return graph

    executor = DaskExecutor(
        dask_client, task_log_dir=str(tmp_path / "logs"))

    assert not _live_run_worker_threads(), "leaked from an earlier test"

    tasks_iter = executor.execute(a_graph("Abandoned", 50))
    next(tasks_iter)
    tasks_iter.close()

    assert not _live_run_worker_threads(), (
        "abandoning the result iterator left the run's worker threads "
        "alive; every non-keep-going run that hits a failing task leaks "
        "two threads"
    )

    results = dict(executor.execute(a_graph("Reused", 20)))
    assert len(results) == 20, (
        "the executor could not run a second graph after an abandoned run"
    )
