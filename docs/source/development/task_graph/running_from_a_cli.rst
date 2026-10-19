Running from a CLI
==================

The tools of GAIn run their task graphs from a command line through
:class:`~gain.task_graph.cli_tools.TaskGraphCli`. A script of your own can
do the same. ``TaskGraphCli`` adds the task graph options to an
``argparse`` parser, and it runs a task graph by the parsed arguments.
This page describes the Python side of the options. The user page
:doc:`/running_in_parallel` describes what each option does.

Creating the arguments
----------------------

:meth:`TaskGraphCli.add_arguments
<gain.task_graph.cli_tools.TaskGraphCli.add_arguments>` adds the options
to a parser:

.. code-block:: python

    import argparse

    from gain.task_graph import TaskGraphCli

    parser = argparse.ArgumentParser()
    TaskGraphCli.add_arguments(parser)
    args = parser.parse_args()

Each option adds a key to the parsed arguments. Several keys do not have
the name of their option: ``--process-pool`` adds ``use_process_pool``,
``-N`` adds ``dask_cluster_name``, ``-c`` adds
``dask_cluster_config_file`` and ``-d`` adds ``task_status_dir``. The
docstring of ``add_arguments`` lists every key. ``add_arguments`` adds no
``-v``. The commands ``list`` and ``status`` read ``verbose``, so add ``-v``
with :meth:`VerbosityConfiguration.set_arguments
<gain.utils.verbosity_configuration.VerbosityConfiguration.set_arguments>`.

Three keywords of ``add_arguments`` change the options:

``use_commands=False``
    Removes the optional command ``run``, ``list`` or ``status``. The
    script then always runs the graph.

``task_progress_mode=False``
    Removes ``--force`` and ``-d``. The parsed arguments then have no
    ``force`` and no ``task_status_dir``.

    .. warning::

       Pass the same ``task_progress_mode`` to ``add_arguments`` and to
       ``process_graph``. ``process_graph`` has its own keyword. If you
       pass ``task_progress_mode=False`` to ``add_arguments`` only,
       ``process_graph`` still builds a ``FileTaskCache``, and the task
       cache writes its records into the current directory.

``default_task_status_dir``
    Sets the default of ``-d``, the task status directory. The default is
    ``./.task-progress``. With ``None``, a run without ``-d`` writes the
    records of the task cache into the current directory.

The lifecycle
-------------

:meth:`TaskGraphCli.process_graph
<gain.task_graph.cli_tools.TaskGraphCli.process_graph>` takes the task
graph and the parsed arguments as keywords. It does these steps:

1. It prunes the graph to the task ids of ``-t``, and to the tasks that
   they depend on.
2. It builds the task cache. With ``task_progress_mode`` true, the
   default, the task cache is a ``FileTaskCache`` over the task status
   directory.
3. For the command ``run``, the default, it creates the task executor with
   :meth:`~gain.task_graph.cli_tools.TaskGraphCli.create_executor`. It runs
   the graph inside the context of the task executor, and it returns true
   when every task succeeds.
4. For the commands ``list`` and ``status``, it prints the record type of
   each task and runs nothing. It returns true.

``--force`` does not change the task cache. The task executor then runs
every task and writes every record again.

``create_executor`` picks the task executor by the parsed arguments:

- ``-j 1`` gives a
  :class:`~gain.task_graph.sequential_executor.SequentialExecutor`.
- ``--process-pool`` with ``-j N`` gives a
  :class:`~gain.task_graph.process_pool_executor.ProcessPoolTaskExecutor`
  with ``N`` processes. Without ``-j``, the pool has one process for each
  CPU.
- Every other case gives a
  :class:`~gain.task_graph.dask_executor.DaskExecutor` on a named
  cluster, also a command line without ``-j``.

``-j 1`` together with ``-N`` or ``-c`` fails an ``assert`` in
``create_executor``. A direct call of ``create_executor()`` without
keywords gives a ``SequentialExecutor``.

A running example, part 3
-------------------------

Part 1, on the page :doc:`graphs`, builds a graph that counts the ``N``
bases of a reference genome. Part 2, on the page :doc:`task_executors`,
runs it from Python. Part 3 runs it from a command line.

The script has a ``main(argv)`` function. It adds the genomic context
options with ``context_providers_add_argparser_arguments`` and the task
graph options with ``add_arguments``. Then it parses ``argv`` and builds
the genomic context. It takes the resource of the reference genome from
the genomic context, builds the graph of part 1, and calls
``process_graph``:

.. literalinclude:: ../../../../core/tests/small/task_graph/test_docs_running_from_a_cli.py
   :language: python
   :start-after: # The docs example of a command line script starts here.
   :end-before: # The docs example of a command line script ends here.

A command line names the GRR with ``--grr-directory <dir>`` or
``-g <grr file>``, and the reference genome with
``-R <genome resource id>``.

The return value of ``main`` is not an exit status: ``main`` returns
``True`` when every task succeeds. A console script that calls
``sys.exit(main())`` exits with status 1 after a successful run. Map the
return value to an exit status in the function that the console script
calls:

.. literalinclude:: ../../../../core/tests/small/task_graph/test_docs_running_from_a_cli.py
   :language: python
   :start-after: # The docs example of a console script starts here.
   :end-before: # The docs example of a console script ends here.

In the commands below, ``count_n`` is a console script that calls the
function ``count_n``. The first command runs the graph sequentially and
writes the records into the task status directory ``task-status``:

.. code-block:: bash

    count_n --grr-directory grr -R genome -j 1 -d task-status

After the run, the command ``status`` prints ``COMPUTED`` for each task:

.. code-block:: bash

    count_n --grr-directory grr -R genome status -j 1 -d task-status

API
---

``TaskGraphCli`` is defined in the module ``gain.task_graph.cli_tools``.
It is also exported by the package ``gain.task_graph``, as
``gain.task_graph.TaskGraphCli``.

.. currentmodule:: gain.task_graph.cli_tools

.. autosummary::

   TaskGraphCli
   TaskGraphCli.add_arguments
   TaskGraphCli.create_executor
   TaskGraphCli.process_graph

.. autoclass:: TaskGraphCli
   :members:
