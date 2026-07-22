import threading
import time
from collections.abc import Iterator
from copy import copy
from typing import Any

from dask.distributed import Client

from gain import logging
from gain.task_graph.base_executor import TaskGraphExecutorBase
from gain.task_graph.cache import NoTaskCache, TaskCache
from gain.task_graph.dask_run_state import RunState
from gain.task_graph.graph import Task, TaskGraph
from gain.task_graph.logging import (
    ensure_log_dir,
    safe_task_id,
)

NO_TASK_CACHE = NoTaskCache()
logger = logging.getLogger(__name__)


class DaskExecutor(TaskGraphExecutorBase):
    """Dask-based task graph executor."""

    def __init__(
        self, dask_client: Client,
        task_cache: TaskCache = NO_TASK_CACHE, **kwargs: Any,
    ) -> None:
        """Initialize the Dask executor.

        Args:
            dask_client: Dask client to use for task execution.
        """
        super().__init__(task_cache=task_cache, **kwargs)
        self._executing = False
        self._dask_client = dask_client

        log_dir = ensure_log_dir(**kwargs)
        self._params = copy(kwargs)
        self._params["task_log_dir"] = log_dir

    def _submit_worker_func(self, state: RunState) -> None:
        """Hand queued tasks to the cluster until the run shuts down."""
        start = time.time()
        submit_count = 0

        while True:
            batch = state.claim_for_submit()
            if batch is None:
                logger.debug("submit worker stopping")
                return

            tasks = list(batch.tasks)
            task_ids = [safe_task_id(task.task.task_id) for task in tasks]

            # No lock is held across map(). The batch does not need one: it
            # sits in the in-flight submit state for the whole width of the
            # call, so termination counts it the entire time (gain#365).
            futures = self._dask_client.map(
                self._exec, tasks,
                key=task_ids,
                pure=False,
                params=self._params,
            )

            state.submitted(batch, futures)

            # Registered only once `running` already knows every future, so
            # a callback firing immediately (a future that is already done)
            # can never reach the run loop before its task mapping exists
            # (gain#355).
            for future in futures:
                future.add_done_callback(state.task_finished)

            submit_count += len(tasks)
            elapsed = time.time() - start
            logger.debug(
                "submitted %s tasks in %.2f seconds; %.2f tasks/s",
                submit_count, elapsed, submit_count / elapsed)
            logger.debug(
                "total unfinished tasks: %s", state.unfinished_count())

    def _results_worker_func(self, state: RunState) -> None:
        """Gather finished futures until the run shuts down."""
        processed_results = 0

        while True:
            batch = state.claim_for_gather()
            if batch is None:
                break

            logger.debug(
                "results worker processing %s completed tasks",
                len(batch.entries))

            # No lock is held across gather() either -- the mirror image of
            # the submit worker. The batch is in the in-flight gather state
            # for the whole round trip, so a run whose gather outlasts the
            # loop's wait cannot be called finished under it (gain#367).
            results = self._dask_client.gather(batch.futures, errors="skip")

            if len(results) == len(batch.tasks):
                gathered = list(zip(batch.tasks, results, strict=True))
            else:
                logger.error(
                    "failed to gather results for all %s tasks; "
                    "looking for exceptions in futures...",
                    len(batch.tasks))
                gathered = []
                for future, task in zip(
                        batch.futures, batch.tasks, strict=True):
                    try:
                        result = future.result()
                    except Exception as ex:  # noqa: BLE001
                        # pylint: disable=broad-except
                        result = ex
                    gathered.append((task, result))

            state.gathered(batch, gathered)
            for future in batch.futures:
                future.release()
            processed_results += len(gathered)

        logger.info("results worker processed %s results", processed_results)

    MAX_RUNNING_TASKS = 700

    def _execute(
        self, graph: TaskGraph,
    ) -> Iterator[tuple[Task, Any]]:
        self._executing = True

        state = RunState()

        submit_worker = threading.Thread(
            target=self._submit_worker_func, args=(state,), daemon=True)
        submit_worker.start()

        results_worker = threading.Thread(
            target=self._results_worker_func, args=(state,), daemon=True)
        results_worker.start()

        finished_tasks = 0
        initial_task_count = len(graph)

        # The run is over when the graph has nothing left to hand out and
        # the state has nothing outstanding. One query, one lock, one
        # owner: a task is outstanding from the moment it leaves the graph
        # until the moment its result is taken below, with no gap for it to
        # be invisible in -- so there is nothing for a re-read to catch up
        # with. Only this thread takes tasks out of the graph or puts
        # results back, so the two reads cannot race each other.
        while not graph.empty() or state.has_outstanding():
            unfinished = state.unfinished_count()
            if unfinished < self.MAX_RUNNING_TASKS:
                limit = max(self.MAX_RUNNING_TASKS - unfinished, 1)
                state.enqueue(
                    graph.extract_tasks(graph.ready_tasks(limit=limit)))

            # Block until a result is ready. The timeout only bounds how
            # long we go without re-checking the graph for newly ready
            # tasks; unlike the wait() poll it replaced, waking up costs
            # nothing per pending future (gain#355).
            state.wait_for_results()

            for task, result in state.take_results():
                graph.process_completed_tasks([(task, result)])
                finished_tasks += 1
                logger.info(
                    "finished %s/%s", finished_tasks, initial_task_count)
                yield task, result

        state.shutdown()

        results_worker.join()
        submit_worker.join()
        self._executing = False

    def close(self) -> None:
        """Close the Dask executor."""
        logger.info("closing Dask executor")
        # shutdown() tears down workers and scheduler gracefully. Retiring /
        # closing workers first races the scheduler teardown and floods the
        # log with heartbeat failures and "Connection ... closed" lines (#125).
        self._dask_client.shutdown()
        self._dask_client.close()
        logger.info("Dask executor closed")
