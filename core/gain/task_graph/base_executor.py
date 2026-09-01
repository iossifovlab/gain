from __future__ import annotations

import contextlib
import multiprocessing as mp
import os
import pickle  # ruff: ignore[suspicious-pickle-import]
import time
from abc import abstractmethod
from collections.abc import Generator
from copy import copy
from typing import Any

import fsspec
import networkx
import psutil

from gain import logging
from gain.task_graph.cache import (
    CacheRecord,
    CacheRecordType,
    NoTaskCache,
    TaskCache,
)
from gain.task_graph.executor import TaskGraphExecutor
from gain.task_graph.graph import Task, TaskDesc, TaskGraph
from gain.task_graph.logging import (
    configure_task_logging,
    ensure_log_dir,
    safe_task_id,
)

logger = logging.getLogger(__name__)

NO_TASK_CACHE = NoTaskCache()


class TaskGraphExecutorBase(TaskGraphExecutor):
    """Executor that walks the graph in order that satisfies dependancies."""

    def __init__(
        self, task_cache: TaskCache = NO_TASK_CACHE,
        *,
        force: bool = False,
        **kwargs: Any,
    ):
        super().__init__()
        self._task_cache = task_cache
        self._executing = False
        self._force = force

        log_dir = ensure_log_dir(**kwargs)
        self._params = copy(kwargs)
        self._params["task_log_dir"] = log_dir

    @staticmethod
    def _exec_internal(
        task: TaskDesc,
        params: dict[str, Any],
    ) -> Any:
        verbose = params.get("verbose")
        if verbose is None:  # Dont use .get default in case of a Box
            verbose = 0

        log_dir = params.get("task_log_dir", ".")
        task_id = safe_task_id(task.task.task_id)
        task_func = task.func
        args = task.args
        kwargs = task.kwargs

        root_logger = logging.getLogger()
        handler = configure_task_logging(log_dir, task_id, verbose)
        root_logger.addHandler(handler)

        task_logger = logging.getLogger("task_executor")
        task_logger.info("task <%s> started", task_id)

        start = time.time()

        process = psutil.Process(os.getpid())
        start_memory_mb = process.memory_info().rss / (1024 * 1024)
        task_logger.info(
            "worker process memory usage: %.2f MB", start_memory_mb)

        try:
            result = task_func(*args, **kwargs)
        except Exception as exp:  # ruff: ignore[blind-except]
            # pylint: disable=broad-except
            result = exp

        elapsed = time.time() - start
        task_logger.info("task <%s> finished in %0.2fsec", task_id, elapsed)

        finish_memory_mb = process.memory_info().rss / (1024 * 1024)
        task_logger.info(
            "worker process memory usage: %.2f MB; change: %+0.2f MB",
            finish_memory_mb, finish_memory_mb - start_memory_mb)

        root_logger.removeHandler(handler)
        handler.close()
        return result

    @staticmethod
    def _exec_forked(
        task: TaskDesc,
        params: dict[str, Any],
    ) -> None:
        task_id = safe_task_id(task.task.task_id)
        result_fn = TaskGraphExecutorBase._result_fn(task_id, params)
        result = TaskGraphExecutorBase._exec_internal(task, params)

        try:
            with fsspec.open(result_fn, "wb") as out:
                pickle.dump(result, out)  # pyright: ignore
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                "cannot write result for task %s. Ignoring and continuing.",
                result_fn,
            )

    @staticmethod
    def _result_fn(task_id: str, params: dict[str, Any]) -> str:
        status_dir = params.get("task_status_dir", ".")
        return os.path.join(status_dir, f"{task_id}.result")

    @staticmethod
    def _exec(
        task: TaskDesc,
        params: dict[str, Any],
    ) -> Any:
        fork_tasks = params.get("fork_tasks", False)
        if not fork_tasks:
            return TaskGraphExecutorBase._exec_internal(task, params)
        mp.current_process()._config[  # type: ignore  # ruff: ignore[private-member-access]
            "daemon"] = False
        p = mp.Process(
            target=TaskGraphExecutorBase._exec_forked,
            args=(task, params),
        )
        p.start()
        p.join()

        task_id = safe_task_id(task.task.task_id)
        result_fn = TaskGraphExecutorBase._result_fn(task_id, params)
        try:
            with fsspec.open(result_fn, "rb") as infile:
                result = pickle.load(infile)  # pyright: ignore
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                "cannot write result for task %s. Ignoring and continuing.",
                result_fn,
            )
            result = None
        return result

    def get_completed_tasks(
        self, graph: TaskGraph,
    ) -> Generator[tuple[Task, Any], None, None]:
        """
        Return cached tasks and their results.

        All tasks that depend on uncomputed tasks are invalidated and
        will not be returned, even if they have a cached result.

        All the tasks that are returned will be preprocessed and removed
        by the graph internally, so that they are not executed again.

        Will not do anything is the executor is in force mode.
        """
        if self._force:
            return
        cached_tasks: dict[Task, CacheRecord] = {}
        uncomputed_tasks: set[Task] = set()

        with graph as tasks:
            di_graph = graph.as_directed_graph()
            for task in tasks:
                task_desc = graph.get_task_desc(task)
                record = self._task_cache.get_record(task_desc)
                if record.type != CacheRecordType.COMPUTED:
                    uncomputed_tasks.add(task)
                cached_tasks[task] = record

            intermediates_needing_recompute = set()
            for task in uncomputed_tasks:
                task_desc = graph.get_task_desc(task)
                for input_file in task_desc.input_files:
                    if os.path.exists(input_file):
                        continue
                    for ancestor_task in networkx.ancestors(di_graph, task):
                        ancestor_desc = graph.get_task_desc(ancestor_task)
                        if (
                            input_file in ancestor_desc.output_files or
                            input_file in
                            ancestor_desc.intermediate_output_files
                        ):
                            cached_tasks[ancestor_task] = \
                                cached_tasks[ancestor_task].invalidate()
                            intermediates_needing_recompute.add(ancestor_task)

            uncomputed_tasks.update(intermediates_needing_recompute)

            for task in uncomputed_tasks:
                task_desc = graph.get_task_desc(task)
                descendants = networkx.descendants(di_graph, task)
                for descendant_task in descendants:
                    cached_tasks[descendant_task] = \
                        cached_tasks[descendant_task].invalidate()

        completed_tasks = {
            task: record.result_or_error
            for task, record in cached_tasks.items()
            if record.type == CacheRecordType.COMPUTED
        }

        for task, result in completed_tasks.items():
            yield task, result

    def execute(
        self, graph: TaskGraph,
    ) -> Generator[tuple[Task, Any], None, None]:
        assert not self._executing, \
            "Cannot execute a new graph while an old one is still running."

        self._executing = True
        try:
            completed_tasks = list(self.get_completed_tasks(graph))
            graph.process_completed_tasks(completed_tasks)

            if len(graph) == 0:
                logger.warning(
                    "All tasks are already COMPUTED; nothing to compute")
                return

            # closing(), not a bare `for`: this is a generator, and a consumer
            # that abandons it part way -- `task_graph_run_with_results`
            # raises out of its `for` loop on the first error unless
            # --keep-going -- closes it, throwing GeneratorExit in at the
            # yield below. That unwinds this frame, but it does NOT close the
            # sub-generator: the iterator a `for` loop holds is released when
            # this generator object is deallocated, not when its close()
            # returns, and the consumer is still holding a reference to it. So
            # `_execute`'s own teardown would run only whenever this object
            # happened to be collected -- and for the dask executor that
            # teardown is what shuts the run state down and joins both worker
            # threads, so until then they spin against the shared client for
            # the life of the process (gain#480). Closing it here makes that
            # teardown run before close() returns, on every path.
            with contextlib.closing(self._execute(graph)) as task_results:
                for task_node, result in task_results:
                    is_error = isinstance(result, BaseException)
                    self._task_cache.cache(
                        task_node,
                        is_error=is_error,
                        result=result,
                    )
                    yield task_node, result
        finally:
            # In the finally for the same reason: an abandoned run that left
            # this flag set made the executor permanently unusable, since the
            # assert above then fires on its next graph. Every executor built
            # here today is built per CLI invocation, so that is latent -- but
            # it turns "the run failed" into "the executor is dead" for any
            # caller that reuses one, which is a strange thing to have to
            # know. This method sets the flag, so this method clears it.
            self._executing = False

    @abstractmethod
    def _execute(
        self, graph: TaskGraph,
    ) -> Generator[tuple[Task, Any], None, None]:
        """Execute the given task graph.

        Must be a generator: :meth:`execute` closes it to tear the run down
        when its own consumer abandons it part way (gain#480).

        Args:
            graph: Task graph to execute.

        Yields:
            Tuples of (task, result) as tasks complete.
        """
