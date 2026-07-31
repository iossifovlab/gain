# pylint: disable=W0621,C0114,C0115,C0116,W0212,W0613
import asyncio
import gc
import pathlib
import threading
import time
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from dask.distributed import Client
from gain.genomic_resources.histogram import HistogramError
from gain.task_graph import dask_executor
from gain.task_graph.cache import CacheRecordType, FileTaskCache
from gain.task_graph.cli_tools import task_graph_run_with_results
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
    permanently "executing", so it could never run another graph.
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


def test_abandoned_runs_do_not_accumulate_across_a_shared_client(
    dask_client: Client,
    tmp_path: pathlib.Path,
) -> None:
    """Abandoning run after run must not poison a later run (gain#480).

    The ordering pin the single-run test above cannot be. The threads a
    leaked run leaves behind are parked in ``claim_for_submit`` /
    ``claim_for_gather`` on a state whose ``shutdown()`` was never called, so
    they never stop: they wake every ``WAIT_TIMEOUT`` for the life of the
    process. That cost is per-abandoned-run and cumulative, which is why the
    defect was first seen not as a failure but as a whole pytest session that
    stopped exiting and, one test later, a deadlock in every subsequent dask
    run -- with the innocent next test blamed.

    So the assertion that matters here is not just "no threads survive one
    run" but "N abandoned runs later, a normal run on the same session
    client still completes". A test that exercises a single run in a fresh
    process cannot see this.
    """
    def a_graph(prefix: str, count: int) -> TaskGraph:
        graph = TaskGraph()
        for i in range(count):
            graph.create_task(f"{prefix}{i}", double, args=[i], deps=[])
        return graph

    assert not _live_run_worker_threads(), "leaked from an earlier test"

    abandoned_runs = 5
    for run in range(abandoned_runs):
        executor = DaskExecutor(
            dask_client, task_log_dir=str(tmp_path / "logs"))
        tasks_iter = executor.execute(a_graph(f"Abandoned{run}_", 50))
        next(tasks_iter)
        tasks_iter.close()

        assert not _live_run_worker_threads(), (
            f"abandoned run {run + 1} of {abandoned_runs} left worker "
            f"threads alive; each one leaks a pair that spins for the life "
            f"of the process"
        )

    # The victim: a plain, complete run on the same shared client, bounded so
    # a poisoned client fails the test instead of hanging the session.
    executor = DaskExecutor(dask_client, task_log_dir=str(tmp_path / "logs"))
    results = _run_in_thread_with_timeout(
        executor, a_graph("Later_", 20), timeout=60.0)

    assert len(results) == 20, (
        f"a run following {abandoned_runs} abandoned runs delivered "
        f"{len(results)} of 20 results"
    )


class _KeyRecordingClient(_WrappedClient):
    """A real client that records the dask keys every ``map()`` is given.

    Everything else is the real client, so the run really executes; the
    keys are simply observed on their way past.
    """

    def __init__(self, client: Client) -> None:
        super().__init__(client)
        self.submitted_keys: list[str] = []

    def map(self, *args: Any, **kwargs: Any) -> Any:
        self.submitted_keys.extend(kwargs["key"])
        return self._client.map(*args, **kwargs)


def _a_graph_of_doubles(prefix: str, count: int) -> TaskGraph:
    graph = TaskGraph()
    for i in range(count):
        graph.create_task(f"{prefix}{i}", double, args=[i], deps=[])
    return graph


def test_two_runs_of_the_same_graph_submit_disjoint_dask_keys(
    dask_client: Client,
    tmp_path: pathlib.Path,
) -> None:
    """No two runs may ever name the same dask key (gain#531).

    The direct, deterministic statement of the property the end-to-end
    stale-results test can only observe by winning a race. The executor used
    to derive a task's dask key from its task id alone, so every run of the
    same graph against the same client submitted the SAME keys by
    construction -- and whether that returned another run's results was then
    a matter of how quickly the scheduler got round to forgetting them.
    Releasing a future does not do that synchronously, so no teardown can
    close the window; only naming the keys apart can.

    Disjointness is asserted for two runs that both complete normally,
    because it must hold regardless of how the earlier run ended -- an
    abandoned run is the dangerous case, not the only one.
    """
    client = _KeyRecordingClient(dask_client)
    executor = DaskExecutor(
        cast("Client", client), task_log_dir=str(tmp_path / "logs"))

    assert len(dict(executor.execute(_a_graph_of_doubles("Keyed", 10)))) == 10
    first_keys = list(client.submitted_keys)

    client.submitted_keys.clear()
    assert len(dict(executor.execute(_a_graph_of_doubles("Keyed", 10)))) == 10
    second_keys = list(client.submitted_keys)

    assert len(first_keys) == 10
    assert len(second_keys) == 10
    assert set(first_keys).isdisjoint(second_keys), (
        f"the two runs share the dask keys "
        f"{sorted(set(first_keys) & set(second_keys))}; a run submitting a "
        f"key another run has used can be deduplicated against it and handed "
        f"that run's results without executing (gain#531)"
    )


def test_a_submitted_dask_key_still_names_its_task_first(
    dask_client: Client,
    tmp_path: pathlib.Path,
) -> None:
    """A key must stay readable as "this task, this run" (gain#531).

    Naming the keys apart is only free if they remain identifiable: the dask
    dashboard and the scheduler's own log lines quote the key, and a key
    that is a bare nonce turns every one of them into a dead end. The
    sanitized task id therefore leads, and what makes the key unique
    follows it -- which is also dask's own ``f"{key}-{token}"`` shape.
    """
    client = _KeyRecordingClient(dask_client)
    executor = DaskExecutor(
        cast("Client", client), task_log_dir=str(tmp_path / "logs"))

    assert len(dict(executor.execute(_a_graph_of_doubles("Traced", 5)))) == 5

    by_task_id = {
        key.split("-")[0]: key for key in client.submitted_keys
    }
    assert set(by_task_id) == {f"Traced{i}" for i in range(5)}, (
        f"the submitted keys {sorted(client.submitted_keys)} do not each "
        f"begin with their task id; a key must stay traceable to its task"
    )


def test_tasks_whose_ids_differ_only_in_punctuation_all_run(
    dask_client: Client,
    tmp_path: pathlib.Path,
) -> None:
    """Sanitizing a task id must not merge two tasks into one (gain#557).

    ``TaskGraph`` enforces uniqueness on the RAW task id, while
    ``safe_task_id`` -- written for log FILE NAMES, long before dask -- maps
    all nine of ``. /,()-:;`` to ``_``. The four ids below are four distinct
    tasks that sanitize to one string, so keying the submission on the
    sanitized id alone handed ``Client.map`` a duplicate key per task; it
    builds its layer as a dict, so the four collapsed into a single
    computation and the run reported success having executed one of them.

    No error is raised anywhere on that path, which is what makes it worth
    a test of its own: the caller cannot tell the difference between a task
    that ran and a task whose key was someone else's.
    """
    ids = ["annotate chr1", "annotate-chr1", "annotate.chr1", "annotate:chr1"]

    graph = TaskGraph()
    for i, task_id in enumerate(ids):
        graph.create_task(task_id, double, args=[i], deps=[])

    executor = DaskExecutor(dask_client, task_log_dir=str(tmp_path / "logs"))

    results = _run_in_thread_with_timeout(executor, graph, timeout=20.0)

    assert len(results) == len(ids), (
        f"the run delivered {len(results)} results for {len(ids)} tasks "
        f"whose ids differ only in punctuation (gain#557)"
    )
    by_task = dict(results)
    assert len(by_task) == len(ids), "a task was delivered more than once"
    for i, task_id in enumerate(ids):
        assert by_task[Task(task_id)] == i * 2, (
            f"task {task_id!r} was delivered {by_task[Task(task_id)]!r} "
            f"instead of its own result {i * 2}; its dask key was shared "
            f"with a sibling task (gain#557)"
        )


def slowly_tagged(stamp: str, x: int) -> str:
    # Slow enough that the rest of the run is still in flight when the first
    # result is taken and the run abandoned -- see
    # ``test_an_abandoned_run_does_not_feed_stale_results_to_the_next_run``.
    time.sleep(0.1)
    return f"{stamp}:{x * 2}"


def test_an_abandoned_run_does_not_feed_stale_results_to_the_next_run(
    dask_client: Client,
    tmp_path: pathlib.Path,
) -> None:
    """A retry must never be handed an abandoned run's results (gain#531).

    A run that ends abnormally leaves its submitted futures in the run state
    and its keys known to the scheduler. If a later run of the same graph
    submits the SAME keys -- which it did as long as a key was derived from
    the task id alone -- it is deduplicated against them and handed the
    ABANDONED run's results without executing anything. ``execute`` writes
    every result it yields into the task cache, so with a ``FileTaskCache``
    the stale answers persist.

    That is a silent wrong answer, not a hang, which makes it the more
    dangerous half of gain#480. Retrying a failed run in the same process is
    exactly when it bites, and it is exactly what a caller does after a run
    without ``--keep-going`` reports a task error.

    Releasing the abandoned run's futures -- which its teardown does, and
    must keep doing -- cannot be what protects the next run.
    ``Future.release()`` decrements a client-side refcount and queues
    ``client-releases-keys``; it does not wait for the scheduler to forget
    the key, and the key may still be ``processing`` on a worker. So between
    the release and the scheduler applying it there is a window, a few tens
    of milliseconds wide, in which the keys are still the abandoned run's.
    A retry landing inside it got that run's answers -- which is why this
    test was ~8% flaky rather than red: teardown usually, but not always,
    outlasted the window.

    Suppressing the release is how that window is held open deterministically
    here: the futures are kept instead of released, which is exactly what the
    scheduler sees for as long as it has not applied the release. The next
    run must be safe on its own, without anything having been forgotten.

    The stamp is a task *argument*, baked in when the graph is built rather
    than read when the task runs, so a stale key returns the first run's
    stamp no matter when the cluster gets round to executing it.
    """
    def a_graph(stamp: str) -> TaskGraph:
        graph = TaskGraph()
        for i in range(12):
            graph.create_task(
                f"Stamped{i}", slowly_tagged, args=[stamp, i], deps=[])
        return graph

    executor = DaskExecutor(dask_client, task_log_dir=str(tmp_path / "logs"))

    # Held, not released -- and held for real: dropping the list would let
    # ``Future.__del__`` release them after all.
    unreleased: list[Any] = []
    try:
        with patch.object(
            DaskExecutor, "_release_futures", staticmethod(unreleased.extend),
        ):
            tasks_iter = executor.execute(a_graph("first"))
            next(tasks_iter)
            tasks_iter.close()

        assert unreleased, (
            "the abandoned run had nothing outstanding when it was torn "
            "down, so the second run never faced its keys at all; this test "
            "would pass without exercising anything"
        )

        results = _run_in_thread_with_timeout(
            executor, a_graph("second"), timeout=60.0)
    finally:
        DaskExecutor._release_futures(unreleased)

    assert len(results) == 12
    stamps = {str(result).split(":")[0] for _, result in results}
    assert stamps == {"second"}, (
        f"the second run returned results stamped {sorted(stamps)}; it "
        f"submitted the keys the abandoned run had already used, so it was "
        f"deduplicated against them and never ran (gain#531)"
    )


def boom() -> None:
    raise ValueError("boom")


def test_a_failing_task_without_keep_going_tears_the_run_down(
    dask_client: Client,
    tmp_path: pathlib.Path,
) -> None:
    """The real non-keep-going path must tear the run down (gain#480).

    The tests above abandon the run by calling ``close()`` on the result
    iterator. Production never does that: ``task_graph_run_with_results``
    ends a run without ``--keep-going`` by ``raise``-ing the task's error
    from inside its own ``for`` loop, and an exception propagating out of a
    generator does *not* close the sub-iterator that generator was reading
    -- the sub-iterator is released only when the abandoned generator object
    is collected, and the raised exception's traceback keeps it referenced
    for as long as anything holds the exception. So the run's teardown is
    deferred to a garbage collection that may never come, which for the dask
    executor means both worker threads spin against the shared client
    meanwhile.

    Every task fails, so the very first result the run yields is the error
    that ends it, with the rest of the graph still outstanding -- the
    worst-case abandonment, and a deterministic one.
    """
    graph = TaskGraph()
    for i in range(30):
        graph.create_task(f"Boom{i}", boom, args=[], deps=[])

    executor = DaskExecutor(dask_client, task_log_dir=str(tmp_path / "logs"))

    assert not _live_run_worker_threads(), "leaked from an earlier test"

    with pytest.raises(ValueError, match="boom"):
        list(task_graph_run_with_results(graph, executor, keep_going=False))

    assert not _live_run_worker_threads(), (
        "the run that ended by raising a task error left its worker threads "
        "alive; the non-keep-going path is how production ends a failing "
        "run, so this leaks a spinning pair on every failed annotation"
    )


def _force_stop_run(state: Any) -> None:
    """Best-effort: empty a hung run's state so its loop can terminate.

    Test-only. ``shutdown()`` alone will not do it -- it deliberately leaves
    running/completed batches in place -- so a run stranded by gain#372 stays
    outstanding after it. Emptying every collection under the state's lock
    drops ``has_outstanding()`` to false, the one thing the spinning run loop
    is waiting to see.
    """
    with state._condition:
        state._shutdown = True
        state._queued.clear()
        state._submitting.clear()
        state._running.clear()
        state._completed.clear()
        state._gathering.clear()
        state._gathered.clear()
        state._condition.notify_all()


def _run_in_thread_with_timeout(
    executor: DaskExecutor, graph: TaskGraph, timeout: float,
) -> list[tuple[Task, Any]]:
    """Drain ``execute`` to completion in a thread, bounded by ``timeout``.

    Without a fix for gain#372 a worker whose dask call raises dies with its
    batch still in the in-flight state, so ``has_outstanding()`` answers
    "yes" forever and the run loop never terminates. Draining the generator
    in a daemon thread and joining it with a timeout turns that permanent
    hang into an assertion failure here instead of a hung test process: the
    test fails by timing out without the fix, and returns in well under a
    second with it.

    Results are collected as a LIST of ``(task, result)`` pairs, not a dict:
    a dict silently dedupes, so a task delivered twice -- over-delivery, the
    opposite failure -- would be invisible. The list length is exactly one
    per task or the assertion the caller makes on it catches the discrepancy.

    On timeout the run is stopped best-effort. A hung run loop is parked
    inside the generator with no further yields coming, so it cannot be
    closed cooperatively from here; instead the run's ``RunState`` -- caught
    as it is constructed, via the executor module's namespace -- is emptied
    under its own lock, which drops ``has_outstanding()`` to false so the
    loop terminates and its daemon workers stop. Otherwise a timed-out red
    run leaves a daemon spinning the run loop against the shared session
    cluster, cascading into later tests.
    """
    results: list[tuple[Task, Any]] = []
    errors: list[BaseException] = []
    done = threading.Event()
    captured: list[Any] = []

    real_run_state = dask_executor.RunState

    def capturing_run_state() -> Any:
        state = real_run_state()
        captured.append(state)
        return state

    def drain() -> None:
        try:
            for task, result in executor.execute(graph):
                results.append((task, result))
        except BaseException as ex:  # noqa: BLE001
            errors.append(ex)
        finally:
            done.set()

    timed_out = False
    with patch.object(dask_executor, "RunState", capturing_run_state):
        thread = threading.Thread(target=drain, daemon=True)
        thread.start()
        thread.join(timeout)

        if not done.is_set():
            # A genuine hang: force the run's state empty so the spinning loop
            # terminates and its daemon workers stop, instead of being left to
            # hammer the shared session cluster (best-effort, test-only).
            timed_out = True
            for state in captured:
                _force_stop_run(state)
            thread.join(timeout)

    assert done.is_set(), "the run did not stop even after force-stop"
    assert not timed_out, (
        f"the run did not terminate within {timeout}s; a worker whose dask "
        f"call raised left its batch counted as outstanding forever and the "
        f"run loop spun without end (gain#372)"
    )
    if errors:
        raise errors[0]
    return results


class _FailingSubmitClient(_WrappedClient):
    """A client whose ``map()`` raises, as a dead scheduler connection would.

    The submit worker calls ``map()`` with the batch held in the in-flight
    submit state; the raise stands in for a transport error there, which
    nothing in the worker used to catch.
    """

    def map(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("map failed: the scheduler connection is gone")


class _FailingGatherClient(_WrappedClient):
    """A client whose ``gather()`` raises, with a real ``map()`` underneath.

    The tasks are really submitted and really finish on the workers, so the
    results worker reaches ``gather()`` with the batch in the in-flight
    gather state; the raise stands in for a lost comm there, which
    ``errors="skip"`` does not suppress.
    """

    def gather(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("gather failed: the worker comm is gone")


def _run_a_graph_of_doubles(
    client: Any, tmp_path: pathlib.Path, num_tasks: int = 5,
    timeout: float = 20.0,
) -> list[tuple[Task, Any]]:
    """Run a wide graph of trivial tasks on ``client`` and drain the results.

    The scaffolding the mid-run-failure tests share: a graph of independent
    ``double`` tasks, an executor over the (usually wrapped) client, and the
    bounded drain that turns a hang into a failure. Results come back as a
    LIST, so over-delivery stays visible -- see
    :func:`_run_in_thread_with_timeout`. What each caller asserts about them
    differs, so only the setup lives here.
    """
    graph = TaskGraph()
    for i in range(num_tasks):
        graph.create_task(f"Task{i}", double, args=[i], deps=[])

    executor = DaskExecutor(client, task_log_dir=str(tmp_path / "logs"))

    return _run_in_thread_with_timeout(executor, graph, timeout=timeout)


def _assert_failing_client_delivers_every_task_as_error(
    client: Any, tmp_path: pathlib.Path, what: str, num_tasks: int = 5,
) -> None:
    """Run a graph on a client that fails mid-run; assert one error per task.

    Shared by the submit / gather / callback-registration regression tests:
    each wraps ``dask_client`` in a client that raises at one point, and the
    fix must turn every such failure into the run terminating with exactly
    one delivered per-task error -- never hanging, never silently fewer, and
    (since results is a list) never silently more (gain#372).
    """
    results = _run_a_graph_of_doubles(client, tmp_path, num_tasks)

    assert len(results) == num_tasks, (
        f"the run delivered {len(results)} of {num_tasks} results; a failed "
        f"{what} must deliver every task exactly once, never silently "
        f"fewer -- nor, since results is a list, silently more (gain#372)"
    )
    by_task = dict(results)
    assert len(by_task) == num_tasks, "a task was delivered more than once"
    for i in range(num_tasks):
        result = by_task[Task(f"Task{i}")]
        assert isinstance(result, BaseException), (
            f"Task{i} was delivered as {result!r}; a task in a batch whose "
            f"{what} failed must be delivered as an error"
        )

    # Terminating is only half the contract: a run that ends abnormally must
    # also release what it owns, because the client outlives it (gain#480).
    # Delivering every task as an error and then leaving a worker thread
    # spinning against the shared client would satisfy every assertion above
    # and still poison every later run in the process.
    assert not _live_run_worker_threads(), (
        f"the run terminated after the failed {what} but left its worker "
        f"threads alive; a run must join both on every exit path"
    )


def test_a_submission_that_fails_ends_the_run_instead_of_hanging(
    dask_client: Client,
    tmp_path: pathlib.Path,
) -> None:
    """A ``map()`` that raises must end the run, not hang it (gain#372).

    The submit worker holds the batch in the in-flight submit state for the
    whole width of ``map()``. If ``map()`` raises and nothing catches it the
    daemon thread dies with the batch still in that state, so
    ``has_outstanding()`` answers "yes" forever and the run loop spins at
    its wait timeout without end -- no exception surfaces and the loop logs
    nothing. The fix delivers the failure as the result of every task in the
    batch, so the run terminates and the caller learns every task failed.
    """
    _assert_failing_client_delivers_every_task_as_error(
        _FailingSubmitClient(dask_client), tmp_path, "submission")


def test_a_gather_that_fails_ends_the_run_instead_of_hanging(
    dask_client: Client,
    tmp_path: pathlib.Path,
) -> None:
    """A ``gather()`` that raises must end the run, not hang it (gain#372).

    Mirror of the submit case. A finished task is held in the in-flight
    gather state for the whole width of ``gather()``. If ``gather()`` raises
    and nothing catches it the results worker dies with the batch still in
    that state -- and its futures never released -- so the run loop spins
    without end. ``errors="skip"`` suppresses task errors, not the transport
    error modelled here. The fix delivers the failure as the result of every
    task in the batch and releases its futures, so the run terminates.
    """
    _assert_failing_client_delivers_every_task_as_error(
        _FailingGatherClient(dask_client), tmp_path, "gather")


class _CallbackRaisingFuture:
    """A future proxy whose ``add_done_callback`` raises on the Nth call.

    Everything else delegates to the real future. The callback is forwarded
    to the real future unchanged, so it fires with the *real* future -- which
    the run state, keyed by this proxy, does not recognise. The proxy is what
    stays in ``running``, exactly the future the recovery must clear; the
    forwarded callback finding nothing to pop is what keeps this batch's
    delivery a clean one-error-per-task rather than a race with the callback.

    (Landing a completed future in ``completed`` before the raise needs a
    proxy the run state and dask agree on, which this one is not -- see
    ``_RaisingAfterCompletionFuture`` below, which wraps only the raising
    future for exactly that reason. That broader over-delivery window, which
    the scoped gain#372 fix left to the gather path, is closed by gain#381
    and covered by
    ``test_an_abort_does_not_redeliver_a_task_the_results_worker_took`` here
    and window by window in ``test_dask_run_state.py``.)
    """

    def __init__(
        self, future: Any, counter: list[int], fail_at: int,
    ) -> None:
        self._future = future
        self._counter = counter
        self._fail_at = fail_at

    def add_done_callback(self, fn: Any) -> None:
        self._counter[0] += 1
        if self._counter[0] >= self._fail_at:
            raise TypeError(
                "add_done_callback failed: the client IOLoop is gone")
        self._future.add_done_callback(fn)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._future, name)


class _CallbackRaisingClient(_WrappedClient):
    """A real client whose futures raise from ``add_done_callback`` partway.

    ``map()`` and the workers are real; only registering completion callbacks
    fails, as it would under a client IOLoop torn down mid-run. By then the
    batch is already in ``running``, so -- unlike the ``map()``/``gather()``
    failures -- its futures exist and must be cleared from there.
    """

    def __init__(self, client: Client, fail_at: int) -> None:
        super().__init__(client)
        self._fail_at = fail_at

    def map(self, *args: Any, **kwargs: Any) -> Any:
        futures = self._client.map(*args, **kwargs)
        counter = [0]
        return [
            _CallbackRaisingFuture(future, counter, self._fail_at)
            for future in futures
        ]


def test_a_callback_registration_that_fails_ends_the_run_instead_of_hanging(
    dask_client: Client,
    tmp_path: pathlib.Path,
) -> None:
    """``add_done_callback`` raising mid-batch must end the run (gain#372).

    ``map()`` succeeds and ``submitted()`` moves the whole batch into
    ``running``; then registering completion callbacks raises part way --
    ``Future.add_done_callback`` does not swallow it. The futures past the
    raise got no callback and, before this fix, stranded in ``running``
    forever: ``has_outstanding()`` answered "yes" without end and the run
    loop spun, delivering fewer results than tasks and never terminating.
    The guard now covers ``submitted()`` and the callback loop, so on the
    raise the whole batch is delivered as per-task errors and cleared from
    ``running`` -- the run terminates with exactly one result per task.
    """
    _assert_failing_client_delivers_every_task_as_error(
        _CallbackRaisingClient(dask_client, fail_at=3),
        tmp_path, "callback registration")


class _RaisingAfterCompletionFuture:
    """A future proxy that raises from ``add_done_callback`` -- but not yet.

    It first waits for the results worker to carry an earlier future of the
    same batch out of ``completed`` and into the in-flight gather state, so
    that by the time the abort runs, that task is somewhere the abort's
    eviction cannot reach it. Unlike :class:`_CallbackRaisingFuture`, only
    the raising future is wrapped: the earlier ones are handed back exactly
    as ``map()`` returned them, so the run state keys them by the same object
    dask reports completion for and the completion really does travel down
    the results path (gain#381).

    Blocking here is safe: the submit worker registers callbacks outside the
    run state's lock, and the earlier futures already have theirs, so the
    completion this waits on does not wait on it in turn.
    """

    def __init__(
        self, future: Any, client: "_CompletionRacingCallbackClient",
    ) -> None:
        self._future = future
        self._client = client

    def add_done_callback(self, fn: Any) -> None:
        self._client.wait_for_gather()
        raise TypeError("add_done_callback failed: the client IOLoop is gone")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._future, name)


class _CompletionRacingCallbackClient(_WrappedClient):
    """A real client whose callback registration raises mid-gather.

    The whole batch is really submitted and really runs; registering the
    completion callbacks raises only once the results worker has entered
    ``gather()`` for an earlier task of the batch -- which proves that task
    left ``completed`` for the in-flight gather state, the first of the
    windows ``submit_aborted``'s eviction cannot reach (gain#381).

    Waiting on that event rather than sleeping a fixed span is what keeps the
    test honest: a duration wide enough on one machine is not on another, and
    a window that failed to open would leave the test passing as a mere
    duplicate of the gain#372 eviction case, having exercised nothing of
    gain#381. :attr:`raced_gather` records whether it really opened, for the
    test to assert on -- raising here could not say so, since the executor
    turns any exception from callback registration into the abort under test.
    """

    def __init__(self, client: Client, raise_at_index: int) -> None:
        super().__init__(client)
        self._raise_at_index = raise_at_index
        self._gather_entered = threading.Event()
        self.raced_gather = False

    def wait_for_gather(self, timeout: float = 10.0) -> None:
        """Block until the results worker enters ``gather()``."""
        self.raced_gather = self._gather_entered.wait(timeout)

    def map(self, *args: Any, **kwargs: Any) -> Any:
        futures = list(self._client.map(*args, **kwargs))
        futures[self._raise_at_index] = _RaisingAfterCompletionFuture(
            futures[self._raise_at_index], self)
        return futures

    def gather(self, *args: Any, **kwargs: Any) -> Any:
        self._gather_entered.set()
        return self._client.gather(*args, **kwargs)


def test_an_abort_does_not_redeliver_a_task_the_results_worker_took(
    dask_client: Client,
    tmp_path: pathlib.Path,
) -> None:
    """The abort races a real gather, end to end, and still delivers once.

    The run-state tests pin each window of this race down deterministically;
    this one runs it for real -- real scheduler, real workers, a task that
    genuinely completes and is genuinely collected while the same batch is
    being aborted mid-wiring. Whichever of the two paths wins the race, the
    run must yield exactly one result per task (gain#381). Before the fix the
    abort delivered its error on top of the collected result and the run
    yielded more results than the graph had tasks.
    """
    num_tasks = 5
    client = _CompletionRacingCallbackClient(dask_client, raise_at_index=3)

    results = _run_a_graph_of_doubles(
        client, tmp_path, num_tasks, timeout=30.0)

    assert client.raced_gather, (
        "the results worker never reached gather(), so the abort never raced "
        "it; this test passed without exercising gain#381 at all"
    )
    assert len(results) == num_tasks, (
        f"the run yielded {len(results)} results for {num_tasks} tasks; a "
        f"task the results worker had already taken was delivered again by "
        f"the abort (gain#381)"
    )
    assert len(dict(results)) == num_tasks, (
        "a task was delivered more than once"
    )
    assert any(isinstance(result, BaseException) for _, result in results), (
        "the wiring failure never surfaced; the caller must still learn the "
        "run failed (gain#372)"
    )


def test_releasing_futures_after_a_gather_failure_survives_a_raising_release(
) -> None:
    """A ``future.release()`` that raises must not kill the results worker.

    On the gather-failure path the release loop runs on the very dead-client
    condition that caused the failure. ``distributed``'s ``release()`` is
    effectively non-throwing, so this is robustness, not a live bug -- but a
    raising ``release()`` there would kill the results worker and re-strand
    any batch that completes after this one, the hang this whole line of work
    exists to prevent (an integration reproduction needs a second batch to
    finish after the worker dies, which is inherently racy -- hence this
    targeted unit check). Each release stands alone: one raising must neither
    propagate nor skip the rest.
    """
    good = MagicMock()
    bad = MagicMock()
    bad.release.side_effect = RuntimeError(
        "release failed: the client is gone")

    DaskExecutor._release_futures([bad, good])

    bad.release.assert_called_once()
    good.release.assert_called_once()


def raise_histogram_error() -> None:
    raise HistogramError(
        "Can not merge categorical histograms; too many unique values 127")


def test_a_histogram_error_ends_the_run_instead_of_hanging(
    dask_client: Client,
    tmp_path: pathlib.Path,
) -> None:
    """A task raising ``HistogramError`` must end the run, not hang it.

    Regression for iossifovlab/gain#465. ``HistogramError`` used to derive
    from ``BaseException``, so it slipped past the ``except Exception`` in
    ``_exec_internal`` that turns a task's failure into its *result*, and
    escaped into the dask worker as a genuine future failure. Every layer
    between worker and caller then filters on ``Exception``: tornado's
    coroutine runner never throws it back into the suspended coroutine,
    ``distributed``'s ``sync()`` is left parked at its ``yield`` with the
    completion event never set, and the results worker's gather-failure
    fallback blocks in ``Future.result()`` forever -- ``grr_manage
    repo-repair`` logged the error and then never exited.

    With the error re-based on ``Exception`` it is delivered as the task's
    result like any other task error, which is also what the CLI's run
    helpers branch on to report a failed run.
    """
    graph = TaskGraph()
    graph.create_task("HistogramTask", raise_histogram_error, args=[], deps=[])

    executor = DaskExecutor(dask_client, task_log_dir=str(tmp_path / "logs"))

    results = _run_in_thread_with_timeout(executor, graph, timeout=20.0)

    assert len(results) == 1, (
        f"the histogram task was delivered {len(results)} times; it must be "
        f"delivered exactly once, as an error"
    )
    _task, result = results[0]
    assert isinstance(result, HistogramError), (
        f"the histogram failure was delivered as {result!r}; it must be "
        f"delivered as the task's result"
    )
    assert isinstance(result, Exception), (
        "the delivered error is not an Exception; task_graph_run's error "
        "accounting tests isinstance(result, Exception), so a non-Exception "
        "failure is reported as a successful run"
    )
