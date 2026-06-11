from __future__ import annotations

import logging
from abc import abstractmethod
from collections.abc import Generator, Iterator
from types import TracebackType
from typing import Any

from gain.task_graph.graph import Task, TaskGraph

logger = logging.getLogger(__name__)


class TaskGraphExecutor:
    """Class that executes a task graph."""

    @abstractmethod
    def execute(self, graph: TaskGraph) -> Iterator[tuple[Task, Any]]:
        """Start executing the graph.

        Return an iterator that yields the task in the graph
        after they are executed.

        This is not necessarily in DFS or BFS order.
        This is not even the order in which these tasks are executed.

        The only guarantee is that when a task is returned its execution
        is already finished.
        """

    @abstractmethod
    def get_completed_tasks(
        self, graph: TaskGraph,
    ) -> Generator[tuple[Task, Any], None, None]:
        """Return an iterator that yields already completed tasks in the graph.

        This is not necessarily in DFS or BFS order.
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
        except Exception:  # noqa: BLE001 pylint: disable=broad-except
            logger.warning("error while closing task graph executor",
                           exc_info=True)
        return exc_type is None

    @abstractmethod
    def close(self) -> None:
        """Clean-up any resources used by the executor."""
