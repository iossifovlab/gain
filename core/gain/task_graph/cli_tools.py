import argparse
import contextlib
import sys
import textwrap
import traceback
from collections.abc import Generator
from typing import Any

import yaml

from gain import logging
from gain.task_graph.cache import CacheRecordType, NoTaskCache, TaskCache
from gain.task_graph.executor import (
    TaskGraphExecutor,
)
from gain.task_graph.graph import TaskGraph
from gain.task_graph.process_pool_executor import ProcessPoolTaskExecutor
from gain.task_graph.sequential_executor import SequentialExecutor

logger = logging.getLogger(__name__)


class TaskGraphCli:
    """Add the task graph options to a command line and run a task graph.

    A tool calls :meth:`add_arguments` on its parser, and then calls
    :meth:`process_graph` with its task graph and the parsed arguments as
    keywords. The page :doc:`/running_in_parallel` describes the options
    for the user.
    """

    @staticmethod
    def add_arguments(
        parser: argparse.ArgumentParser, *,
        task_progress_mode: bool = True,
        default_task_status_dir: str | None = "./.task-progress",
        use_commands: bool = True,
    ) -> None:
        """Add the task graph options to a command line parser.

        The options choose the task executor, the number of workers, the
        task status directory and the command. Pass the parsed arguments
        to :meth:`process_graph` with ``**vars(args)``. Several keys do not
        have the name of their option. The options add these keys:

        - ``-j`` adds ``jobs``.
        - ``--process-pool`` adds ``use_process_pool``.
        - ``-N`` adds ``dask_cluster_name``.
        - ``-c`` adds ``dask_cluster_config_file``.
        - ``--task-log-dir`` adds ``task_log_dir``.
        - The optional command adds ``command``.
        - ``-t`` adds ``task_ids``.
        - ``--keep-going`` adds ``keep_going``.
        - ``--fork-tasks`` adds ``fork_tasks``.
        - ``--force`` adds ``force``.
        - ``-d`` adds ``task_status_dir``.

        The function adds no ``-v``. The commands ``list`` and ``status``
        read ``verbose``, so add it with
        :meth:`~gain.utils.verbosity_configuration.VerbosityConfiguration.set_arguments`.

        Parameters
        ----------
        parser : argparse.ArgumentParser
            The parser that gets the options.
        task_progress_mode : bool
            When false, the parser gets no ``--force`` and no ``-d``, and
            the parsed arguments have no ``force`` and no
            ``task_status_dir``. Pass the same value to
            :meth:`process_graph`: it builds a ``FileTaskCache`` by default
            whatever the parser holds, and without ``task_status_dir`` the
            task cache writes its record files into the current directory.
        default_task_status_dir : str | None
            The default of ``-d``, the task status directory. ``None``
            means that a run without ``-d`` writes the record files of the
            task cache into the current directory.
        use_commands : bool
            When false, the parser gets no optional command ``run``,
            ``list`` or ``status``, and :meth:`process_graph` always runs
            the graph.
        """
        executor_group = parser.add_argument_group(title="Task Graph Executor")
        # cluster_name
        # cluster_config_file
        executor_group.add_argument(
            "-j", "--jobs", type=int, default=None,
            help="Number of jobs to run in parallel. Defaults to the number "
            "of processors on the machine")

        executor_group.add_argument(
            "--process-pool", "--pp",
            dest="use_process_pool", action="store_true",
            help="Use a process pool executor with the specified number of "
            "processes instead of a dask distributed executor.",
        )
        executor_group.add_argument(
            "-N", "--dask-cluster-name", "--dcn",
            dest="dask_cluster_name",
            type=str, default=None,
            help="The name of a named cluster in the dask config, under "
            "'gain_named_cluster.clusters'. The tasks run on it.",
        )
        executor_group.add_argument(
            "-c", "--dccf", "--dask-cluster-config-file",
            dest="dask_cluster_config_file",
            type=str, default=None,
            help="dask cluster config file",
        )
        executor_group.add_argument(
            "--task-log-dir", dest="task_log_dir", type=str,
            default=None,
            help="Path to directory where to store tasks' logs",
        )
        # task_cache
        execution_mode_group = parser.add_argument_group(
            title="Execution Mode")
        if use_commands:
            execution_mode_group.add_argument(
                "command",
                choices=["run", "list", "status"],
                default="run", nargs="?",
                help=textwrap.dedent("""\
                    Command to execute on the import configuration.
                    run - runs the import process
                    list - lists the tasks to be executed but doesn't run them
                    status - synonym for list
                """),
            )
        execution_mode_group.add_argument(
            "-t", "--task-ids", dest="task_ids", type=str, nargs="+")
        execution_mode_group.add_argument(
            "--keep-going", default=False, action="store_true",
            help="Whether or not to keep executing in case of an error",
        )
        execution_mode_group.add_argument(
            "--fork-tasks", "--fork-task", "--fork",
            dest="fork_tasks", action="store_true",
            help="Whether to fork a new worker process for each task",
        )

        if task_progress_mode:
            execution_mode_group.add_argument(
                "--force", "-f", default=False, action="store_true",
                help="Ignore precomputed state and always rerun all tasks.",
            )
            execution_mode_group.add_argument(
                "-d", "--task-status-dir", "--tsd",
                default=default_task_status_dir,
                type=str, help="Directory to store the task progress.",
            )

        else:
            assert not task_progress_mode, \
                "task_progress_mode must be False if no cache is used"

    @staticmethod
    def _create_dask_executor(
        task_cache: TaskCache,
        **kwargs: Any,
    ) -> TaskGraphExecutor:
        """Create a task graph executor according to the args specified."""
        # pylint: disable=import-outside-toplevel
        from gain.dask.named_cluster import (
            setup_client,
            setup_client_from_config,
        )
        from gain.task_graph.dask_executor import DaskExecutor

        assert kwargs.get("dask_cluster_name") is None or \
            kwargs.get("dask_cluster_config_file") is None
        if kwargs.get("dask_cluster_config_file") is not None:
            dask_cluster_config_file = kwargs.get("dask_cluster_config_file")
            assert dask_cluster_config_file is not None
            with open(dask_cluster_config_file) as conf_file:
                dask_cluster_config = yaml.safe_load(conf_file)
            logger.info(
                "THE CLUSTER CONFIG IS: %s; loaded from: %s",
                dask_cluster_config,
                kwargs.get("dask_cluster_config_file"))
            client, _ = setup_client_from_config(
                dask_cluster_config,
                number_of_workers=kwargs.get("jobs"),
            )
        else:
            client, _ = setup_client(
                kwargs.get("dask_cluster_name"),
                number_of_workers=kwargs.get("jobs"))

        logger.info("Working with client: %s", client)
        return DaskExecutor(client, task_cache=task_cache, **kwargs)

    @staticmethod
    def create_executor(
        task_cache: TaskCache | None = None,
        **kwargs: Any,
    ) -> TaskGraphExecutor:
        """Create the task executor that the parsed arguments choose.

        With ``jobs`` equal to 1, the function creates a
        :class:`~gain.task_graph.sequential_executor.SequentialExecutor`.
        Otherwise, with ``use_process_pool`` true, it creates a
        :class:`~gain.task_graph.process_pool_executor.ProcessPoolTaskExecutor`
        with ``jobs`` processes. In every other case, it creates a
        :class:`~gain.task_graph.dask_executor.DaskExecutor` on a named
        cluster.

        Parameters
        ----------
        task_cache : TaskCache | None
            The task cache of the task executor. ``None`` means a
            :class:`~gain.task_graph.cache.NoTaskCache`, which runs every
            task and records nothing.
        **kwargs
            The parsed arguments of :meth:`add_arguments`. The function
            reads these keys, and it passes all of them to the task
            executor too:

            ``jobs``
                The number of workers. A missing key means 1, so a call
                without keywords creates a ``SequentialExecutor``. A
                parsed command line always holds ``jobs``, with the value
                ``None`` when ``-j`` is absent, so a command line without
                ``-j`` creates a ``DaskExecutor``, or a
                ``ProcessPoolTaskExecutor`` with ``--process-pool``.
                ``None`` means one process for each CPU for the process
                pool, and the default of the named cluster for dask.
            ``use_process_pool``
                When true, and ``jobs`` is not 1, the function creates a
                ``ProcessPoolTaskExecutor``.
            ``dask_cluster_name``
                The named cluster of the ``DaskExecutor``. ``None`` means
                the default named cluster.
            ``dask_cluster_config_file``
                A cluster config file that describes the cluster of the
                ``DaskExecutor``. ``None`` means that the function uses
                ``dask_cluster_name``.

        Returns
        -------
        TaskGraphExecutor
            The new task executor. Use it as a context manager, so that
            it releases its workers.

        Raises
        ------
        AssertionError
            When ``jobs`` is 1, or ``use_process_pool`` is true, and
            ``dask_cluster_name`` or ``dask_cluster_config_file`` is set.
            Also when both ``dask_cluster_name`` and
            ``dask_cluster_config_file`` are set.
        """
        if task_cache is None:
            task_cache = NoTaskCache()

        if kwargs.get("jobs", 1) == 1:
            assert kwargs.get("dask_cluster_name") is None
            assert kwargs.get("dask_cluster_config_file") is None
            return SequentialExecutor(task_cache=task_cache, **kwargs)

        if kwargs.get("use_process_pool"):
            assert kwargs.get("dask_cluster_name") is None
            assert kwargs.get("dask_cluster_config_file") is None
            return ProcessPoolTaskExecutor(
                max_workers=kwargs.get("jobs"),
                task_cache=task_cache,
                **kwargs)

        return TaskGraphCli._create_dask_executor(
            task_cache=task_cache, **kwargs)

    @staticmethod
    def process_graph(
        task_graph: TaskGraph, *,
        task_progress_mode: bool = True,
        **kwargs: Any,
    ) -> bool:
        """Run a task graph, or report its tasks, by the parsed arguments.

        The function prunes the graph to the tasks of ``task_ids`` and
        builds the task cache with
        :meth:`TaskCache.create <gain.task_graph.cache.TaskCache.create>`.
        For the command ``run``, it creates the task executor with
        :meth:`create_executor` and runs the graph inside the context of
        the task executor. For the commands ``list`` and ``status``, it
        prints a table of the task ids and the record type of each task
        to the standard output, and it runs nothing.

        Parameters
        ----------
        task_graph : TaskGraph
            The task graph. A run consumes it.
        task_progress_mode : bool
            When true, the task cache is a ``FileTaskCache`` over
            ``task_status_dir``. When false, it is a ``NoTaskCache``. This
            keyword is separate from the keyword of :meth:`add_arguments`,
            so pass the same value to both.
        **kwargs
            The parsed arguments of :meth:`add_arguments`. The function
            reads these keys, and it passes all of them to
            :meth:`create_executor`:

            ``command``
                ``run``, ``list`` or ``status``. ``None`` means ``run``.
            ``task_ids``
                The ids of the tasks to keep. The graph keeps the tasks
                that they depend on too. ``None`` means every task.
            ``task_status_dir``
                The task status directory of the ``FileTaskCache``.
                ``None``, or a missing key, means the current directory.
            ``force``
                When true, the task executor runs every task and writes
                every record again. The task cache does not change.
            ``keep_going``
                When true, the run continues after a failed task.
            ``verbose``
                When set and not 0, the commands ``list`` and ``status``
                print the exception of each task with an ``ERROR`` record.
                :meth:`add_arguments` does not add this key. The ``-v``
                option of ``VerbosityConfiguration.set_arguments`` adds
                it.

        Returns
        -------
        bool
            For ``run``, true when every task succeeds. For ``list`` and
            ``status``, always true.

        Raises
        ------
        Exception
            The error of the first failed task, for ``run`` without
            ``keep_going``.
        ValueError
            When ``command`` is not ``run``, ``list`` or ``status``, or
            when the graph has no task with one of the ``task_ids``.
        """
        if kwargs.get("task_ids") is not None:
            task_graph.prune(tasks_to_keep=kwargs["task_ids"])

        task_cache = TaskCache.create(
            task_progress_mode=task_progress_mode,
            cache_dir=kwargs.get("task_status_dir"),
        )

        if kwargs.get("command") is None or kwargs.get("command") == "run":
            with TaskGraphCli.create_executor(task_cache, **kwargs) as xtor:
                return task_graph_run(
                    task_graph, xtor,
                    keep_going=kwargs.get("keep_going", False))

        if kwargs.get("command") in {"list", "status"}:
            return task_graph_status(
                task_graph, task_cache, kwargs.get("verbose"))

        raise ValueError(f"Unknown command {kwargs.get('command')}")


def task_graph_run(
    task_graph: TaskGraph,
    executor: TaskGraphExecutor | None = None,
    *,
    keep_going: bool = False,
) -> bool:
    """Run a task graph with a task executor.

    Parameters
    ----------
    task_graph : TaskGraph
        The task graph to run. The run consumes it.
    executor : TaskGraphExecutor | None
        The task executor that runs the graph. ``None`` means a new
        :class:`~gain.task_graph.sequential_executor.SequentialExecutor`.
    keep_going : bool
        When false, the first failed task raises its error and ends the
        run. When true, the run prints the error of each failed task to
        the standard error and continues.

    Returns
    -------
    bool
        True when no task failed.

    Raises
    ------
    Exception
        The error of the first failed task, when ``keep_going`` is false.
    """
    no_errors = True
    for result_or_error in task_graph_run_with_results(
            task_graph, executor, keep_going=keep_going):
        if isinstance(result_or_error, Exception):
            no_errors = False
    return no_errors


def task_graph_run_with_results(
    task_graph: TaskGraph, executor: TaskGraphExecutor | None = None,
    *,
    keep_going: bool = False,
) -> Generator[Any, None, None]:
    """Run a task graph and yield the result of each task.

    The function yields the results without their tasks, in the order in
    which the tasks complete. A task that the task cache reports as
    computed does not run, and the function yields no result for it.

    Parameters
    ----------
    task_graph : TaskGraph
        The task graph to run. The run consumes it.
    executor : TaskGraphExecutor | None
        The task executor that runs the graph. ``None`` means a new
        :class:`~gain.task_graph.sequential_executor.SequentialExecutor`.
    keep_going : bool
        When false, the first failed task raises its error and ends the
        run. When true, the function prints the error of each failed task
        to the standard error and yields the error as the result.

    Yields
    ------
    Any
        The result of each task, or the exception of a failed task.

    Raises
    ------
    Exception
        The error of the first failed task, when ``keep_going`` is false.
    """
    if executor is None:
        executor = SequentialExecutor()

    # closing(), because the `raise` below is the normal way a run without
    # --keep-going ends, and it leaves this generator's frame without
    # exhausting `tasks_iter`. An exception propagating out of a generator
    # does not close the iterator that generator was reading: `tasks_iter` is
    # released only when this abandoned generator object is collected, and
    # the raised error's traceback keeps it referenced for as long as anyone
    # holds the error -- which the caller reporting the failure necessarily
    # does. So the run's teardown was deferred to a collection that need
    # never come, and for the dask executor that teardown is what joins the
    # two run-loop worker threads: every failed run left a pair of them
    # spinning against a client that outlives it (gain#480). Unlike frame
    # release, a `with` block unwinds synchronously, on the raise and on the
    # GeneratorExit of a consumer that abandons us in turn.
    with contextlib.closing(executor.execute(task_graph)) as tasks_iter:
        for task, result_or_error in tasks_iter:
            if isinstance(result_or_error, Exception):
                if keep_going:
                    print(f"Task {task.task_id} failed with:",
                          file=sys.stderr)
                    traceback.print_exception(
                        None, value=result_or_error,
                        tb=result_or_error.__traceback__,
                        file=sys.stderr,
                    )
                else:
                    raise result_or_error
            yield result_or_error


def task_graph_all_done(task_graph: TaskGraph, task_cache: TaskCache) -> bool:
    """Check if the task graph is fully executed.

    When all tasks are already computed, the function returns True.
    If there are tasks, that need to run, the function returns False.
    """
    with task_graph as tasks:
        for task in tasks:
            record = task_cache.get_record(task_graph.get_task_desc(task))
            if record.type != CacheRecordType.COMPUTED:
                return False
    return True


def task_graph_status(
        task_graph: TaskGraph, task_cache: TaskCache,
        verbose: int | None) -> bool:
    """Show the status of each task from the task graph."""
    id_col_len = max(len(t.task_id) for t in task_graph.tasks)
    id_col_len = min(120, max(50, id_col_len))
    columns = ["TaskID", "Status"]
    print(f"{columns[0]:{id_col_len}s} {columns[1]}")
    for task in task_graph.tasks:
        record = task_cache.get_record(task_graph.get_task_desc(task))
        status = record.type.name
        msg = f"{task.task_id:{id_col_len}s} {status}"
        is_error = record.type == CacheRecordType.ERROR
        if is_error and not verbose:
            msg += " (-v to see exception)"
        print(msg)
        if is_error and verbose:
            traceback.print_exception(
                None, value=record.error,
                tb=record.error.__traceback__,
                file=sys.stdout,
            )
    return True
