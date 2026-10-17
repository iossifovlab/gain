Task graph
==========

This chapter is the in-depth description of the *task graph* part of GAIn's
declared API. A task graph is a directed acyclic graph of tasks. A task
executor runs it, and a task cache decides which tasks must run again. The
statistics build of ``grr_manage``, the parallel runs of ``annotate_tabular``
and ``annotate_vcf``, and the binning tool all build a task graph and run it
this way.

.. rubric:: Where the line falls

Everything here is Python. The task graph has a user side too: the
command-line options that choose a task executor and the number of workers,
the task status directory, and the named cluster YAML. The user page
"Running in parallel" describes that side, not this chapter.

This chapter is for code that builds its own task graph, or that runs one
from a script of its own. It describes the objects that such code touches.

.. rubric:: The shape of the API

A script does two things. First it builds a
:class:`~gain.task_graph.graph.TaskGraph` and adds its tasks. Then it runs
the graph with a task executor:

.. code-block:: python

    from gain.task_graph.graph import TaskGraph
    from gain.task_graph.cli_tools import task_graph_run
    from gain.task_graph.sequential_executor import SequentialExecutor

    def part(name: str) -> str:
        return name.upper()

    def summary(*parts: str) -> str:
        return ",".join(parts)

    graph = TaskGraph()
    parts = [graph.create_task(f"part_{n}", part, args=[n], deps=[])
             for n in ("a", "b")]
    graph.create_task("summary", summary, args=parts, deps=parts)

    with SequentialExecutor() as executor:
        assert task_graph_run(graph, executor)

The drivers of GAIn do both steps through
:class:`~gain.task_graph.cli_tools.TaskGraphCli`. Its ``add_arguments``
adds the task graph options to a command-line parser. Its ``process_graph``
creates a task cache from those options. For the ``run`` command, which is
the default, it also creates a task executor and runs the graph. For the
``list`` and ``status`` commands, it only reports the state of the task
cache, and it creates no task executor.

With ``-j 1``, the task executor runs the tasks sequentially. Otherwise,
``--process-pool`` selects
:class:`~gain.task_graph.process_pool_executor.ProcessPoolTaskExecutor`,
which runs the tasks in a pool of ``-j`` processes. Without ``-j``, the pool
has one process for each CPU. Without ``--process-pool``, a dask task
executor runs the tasks on a cluster with ``-j`` workers. The cluster is the
default named cluster, the named cluster that ``-N`` names, or the cluster
that the ``-c`` cluster config file describes.

.. rubric:: Not covered here

Some names of the task graph packages are internal. A task executor uses
them, or a driver uses them, but code of your own does not need them:

- ``gain.task_graph.dask_run_state``, ``gain.task_graph.logging``,
  ``gain.task_graph.work_dir`` and ``gain.task_graph.demo_graphs_cli``;
- ``gain.dask.config``;
- ``sync_tasks``, ``task_graph_status`` and ``task_graph_all_done``;
- the four ``set_up_*`` cluster functions of ``gain.dask.named_cluster``;
- the ``TaskGraph`` members that only a task executor calls:
  ``ready_tasks``, ``extract_tasks``, ``process_completed_tasks``,
  ``get_task_deps`` and ``as_directed_graph``.

The generated :doc:`module index <module_index>` documents these names, to
the extent they are documented.

.. toctree::
   :maxdepth: 2

   task_graph/graphs
   task_graph/task_executors
   task_graph/running_from_a_cli
