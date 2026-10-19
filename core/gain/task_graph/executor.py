from __future__ import annotations

from abc import abstractmethod
from collections.abc import Generator
from types import TracebackType
from typing import Any

from gain import logging
from gain.task_graph.graph import Task, TaskGraph

logger = logging.getLogger(__name__)


class TaskGraphExecutor:
    """The interface of a task executor.

    A task executor runs a task graph. It is a context manager: the
    ``with`` block closes the task executor when it ends, and so releases
    its workers. A failure in :meth:`close` is logged and does not hide an
    error of the ``with`` block.

    The task executors of GAIn are
    :class:`~gain.task_graph.sequential_executor.SequentialExecutor`,
    :class:`~gain.task_graph.process_pool_executor.ProcessPoolTaskExecutor`
    and :class:`~gain.task_graph.dask_executor.DaskExecutor`.
    """

    # execute returns a generator, not merely an iterator, because
    # abandoning a run is a supported way to end it --
    # ``task_graph_run_with_results`` does it on the first failing task
    # unless ``--keep-going`` -- and closing the generator is how the run
    # is told to tear itself down. An implementation that holds resources
    # for the run's lifetime must release them from that path too, or an
    # abandoned run leaks them for the life of the process (gain#480).
    @abstractmethod
    def execute(
        self, graph: TaskGraph,
    ) -> Generator[tuple[Task, Any], None, None]:
        """Run the tasks of a task graph.

        The task executor consumes the graph: the graph has no tasks left
        when the run ends. Build a new graph for each run.

        Parameters
        ----------
        graph : TaskGraph
            The task graph to run.

        Yields
        ------
        tuple[Task, Any]
            Each task and its result, when the task completes. The result
            of a failed task is its exception. The order is not the order
            of the graph, and not the order in which the tasks run. A task
            is yielded only after it has finished.
        """

    @abstractmethod
    def get_completed_tasks(
        self, graph: TaskGraph,
    ) -> Generator[tuple[Task, Any], None, None]:
        """Yield the tasks of the graph that are already completed.

        The order is not the order of the graph.

        :meta private:
        """

    def __enter__(self) -> TaskGraphExecutor:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        # Teardown is best-effort: the task graph's results are produced and
        # consumed inside the ``with`` body, so a failure while releasing
        # executor resources (e.g. a dask worker shutdown timeout) must not
        # crash a completed run, nor mask an exception raised in the body.
        try:
            self.close()
        except Exception:
            logger.warning("error while closing task graph executor",
                           exc_info=True)
        return exc_type is None

    @abstractmethod
    def close(self) -> None:
        """Release the workers and the other resources of the task executor.

        The ``with`` block of a task executor calls this method when it
        ends.
        """
