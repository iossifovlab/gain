Task executors
==============

A task executor runs a :class:`~gain.task_graph.graph.TaskGraph`. It runs
each task only after the tasks that it depends on, and it asks a task cache
which tasks must run. This page describes the three task executors of GAIn,
the functions that run a graph with one call, and the task caches.

Creating a task executor
------------------------

GAIn has three task executors. Each one takes the keyword argument
``task_cache``, the task cache that decides which tasks must run. Without
it, the task executor runs every task.

``SequentialExecutor()``
    Runs the tasks one at a time, in the current process. It is in
    ``gain.task_graph.sequential_executor``.

``ProcessPoolTaskExecutor(max_workers=...)``
    Runs the tasks in a pool of processes. ``max_workers`` is the number of
    processes. Without it, the pool has one process for each CPU. It is in
    ``gain.task_graph.process_pool_executor``.

``DaskExecutor(client)``
    Runs the tasks on the dask cluster of ``client``. It is in
    ``gain.task_graph.dask_executor``.

The three task executors derive from
:class:`~gain.task_graph.base_executor.TaskGraphExecutorBase`, which also
takes these keyword arguments:

``force``
    When true, the task executor runs every task and does not read the task
    cache. It still records the outcome of each task in the task cache.

``task_log_dir``
    The directory of the task logs. Without it, the task executor writes
    them into ``.task-log`` in the current directory.

``verbose``
    The verbosity of the task logs.

``fork_tasks``
    When true, each task runs in a new process.

``task_status_dir``
    The directory of the result files of the forked tasks. Without it, the
    task executor writes them into the current directory.

Only ``SequentialExecutor`` and ``DaskExecutor`` use ``task_log_dir``,
``verbose``, ``fork_tasks`` and ``task_status_dir``.
``ProcessPoolTaskExecutor`` ignores them: it
creates the directory of the task logs, but it writes no task log and it
does not fork the tasks.

A process pool or a dask cluster pickles the function and the arguments of
each task. Use module-level functions for the tasks, and pass arguments that
pickle, for example a resource and not an open genome.

The lifecycle
-------------

A task executor is a context manager. The ``with`` block calls
:meth:`~gain.task_graph.executor.TaskGraphExecutor.close` when it ends, and
``close`` releases the workers:

.. code-block:: python

    from gain.task_graph.sequential_executor import SequentialExecutor

    with SequentialExecutor() as executor:
        for task, result in executor.execute(graph):
            print(task.task_id, result)

:meth:`~gain.task_graph.base_executor.TaskGraphExecutorBase.execute`
consumes the graph and yields each task with its result when the task
completes. The order is the order of completion, not the order of the
graph. The result of a failed task is its exception: ``execute`` does not
raise it. A task executor runs one graph at a time, so exhaust the
generator of a run before you start the next run.

``DaskExecutor`` takes ownership of its client. Its ``close`` shuts down the
client and the cluster of the client, so do not use the client after the
``with`` block.

Running a graph with one call
-----------------------------

:func:`~gain.task_graph.cli_tools.task_graph_run` and
:func:`~gain.task_graph.cli_tools.task_graph_run_with_results`, in
``gain.task_graph.cli_tools``, run a graph with one call. Without a task
executor, they create a ``SequentialExecutor``.

``task_graph_run`` returns true when no task failed.
``task_graph_run_with_results`` yields the result of each task, without the
task. Without ``keep_going``, the first failed task raises its error and
ends the run. With ``keep_going=True``, the run prints the error of each
failed task and continues.

.. code-block:: python

    from gain.task_graph.cli_tools import task_graph_run

    with SequentialExecutor() as executor:
        assert task_graph_run(graph, executor)

The task cache
--------------

A task cache decides which tasks must run and records the outcome of each
task that runs. The classes are in ``gain.task_graph.cache``.

:class:`~gain.task_graph.cache.NoTaskCache`
    Runs every task and records nothing. It is the default of each task
    executor.

:class:`~gain.task_graph.cache.FileTaskCache`
    Writes one record file for each task into the task status directory,
    ``cache_dir``. The record holds the result of the task, or its
    exception. The modification times of the files of a task decide whether
    a computed record is still valid: an input file that is newer than the
    output files of the task makes the task run again.

:meth:`TaskCache.create <gain.task_graph.cache.TaskCache.create>` picks the
task cache for a set of options. It picks ``NoTaskCache`` when ``force`` is
true or the status mode, ``task_progress_mode``, is off. Otherwise it picks
a ``FileTaskCache``. Without ``cache_dir``, the ``FileTaskCache`` uses the
current directory.

:meth:`~gain.task_graph.cache.TaskCache.get_record` returns the
:class:`~gain.task_graph.cache.CacheRecord` of a task. The ``type`` of the
record is a :class:`~gain.task_graph.cache.CacheRecordType`:
``NEEDS_COMPUTE``, ``COMPUTED`` or ``ERROR``. ``result`` holds the result of
a computed task.

Before a run, ``execute`` asks the task cache for the record of each task.
It removes each task that the task cache reports as computed from the
graph, and it does not yield that task. A task that depends on a task that
must run runs again, even when its own record is computed.

A running example, part 2
-------------------------

Part 1, on the page :doc:`graphs`, builds a graph that counts the ``N``
bases of a reference genome. The examples here run that graph. Each run
builds a new graph, because a run consumes the graph.

The first run is sequential. ``task_graph_run_with_results`` yields the
results without their tasks. The summary task depends on all the other
tasks, so it completes last, and the last result is the sum:

.. literalinclude:: ../../../../core/tests/small/task_graph/test_docs_task_executors.py
   :language: python
   :start-after: # The docs example of a sequential run starts here.
   :end-before: # The docs example of a sequential run ends here.

The second run uses a pool of two processes. The task functions are
module-level, so the pool can pickle them:

.. literalinclude:: ../../../../core/tests/small/task_graph/test_docs_task_executors.py
   :language: python
   :start-after: # The docs example of a process pool run starts here.
   :end-before: # The docs example of a process pool run ends here.

The third run gives the task executor a ``FileTaskCache`` over a task status
directory. The task cache writes a record file for each task. Then the
example builds a fresh graph and runs it with a new ``FileTaskCache`` over
the same directory. The record type of each task is ``COMPUTED``, the record
of the summary task holds the sum, and ``execute`` yields nothing:

.. literalinclude:: ../../../../core/tests/small/task_graph/test_docs_task_executors.py
   :language: python
   :start-after: # The docs example of a run with a task cache starts here.
   :end-before: # The docs example of a run with a task cache ends here.

API
---

Each class below is defined in the module that the section names.
``task_graph_run`` and ``task_graph_run_with_results`` are defined in the
module ``gain.task_graph.cli_tools``.

.. currentmodule:: gain.task_graph.executor

.. autosummary::

   TaskGraphExecutor
   TaskGraphExecutor.close
   TaskGraphExecutor.execute

.. currentmodule:: gain.task_graph.base_executor

.. autosummary::

   TaskGraphExecutorBase
   TaskGraphExecutorBase.execute

.. currentmodule:: gain.task_graph.sequential_executor

.. autosummary::

   SequentialExecutor
   SequentialExecutor.close

.. currentmodule:: gain.task_graph.process_pool_executor

.. autosummary::

   ProcessPoolTaskExecutor
   ProcessPoolTaskExecutor.close

.. currentmodule:: gain.task_graph.dask_executor

.. autosummary::

   DaskExecutor
   DaskExecutor.close

.. currentmodule:: gain.task_graph.base_executor

Inherited from ``TaskGraphExecutorBase`` by the three task executors:

.. autosummary::

   TaskGraphExecutorBase.execute

.. currentmodule:: gain.task_graph.cache

.. autosummary::

   TaskCache
   TaskCache.cache
   TaskCache.create
   TaskCache.get_record
   FileTaskCache
   FileTaskCache.cache
   FileTaskCache.get_record
   NoTaskCache
   NoTaskCache.cache
   NoTaskCache.get_record
   CacheRecord
   CacheRecord.error
   CacheRecord.result
   CacheRecordType

Inherited from ``TaskCache`` by ``FileTaskCache`` and ``NoTaskCache``:

.. autosummary::

   TaskCache.create

.. currentmodule:: gain.task_graph.cli_tools

.. autosummary::

   task_graph_run
   task_graph_run_with_results

Task executors
^^^^^^^^^^^^^^

.. currentmodule:: gain.task_graph.executor

.. autoclass:: TaskGraphExecutor
   :members:

.. currentmodule:: gain.task_graph.base_executor

.. autoclass:: TaskGraphExecutorBase
   :members:

.. currentmodule:: gain.task_graph.sequential_executor

.. autoclass:: SequentialExecutor
   :members:

.. currentmodule:: gain.task_graph.process_pool_executor

.. autoclass:: ProcessPoolTaskExecutor
   :members:

.. currentmodule:: gain.task_graph.dask_executor

.. autoclass:: DaskExecutor
   :members:

Running a graph
^^^^^^^^^^^^^^^

.. currentmodule:: gain.task_graph.cli_tools

.. autofunction:: task_graph_run

.. autofunction:: task_graph_run_with_results

Task caches
^^^^^^^^^^^

.. currentmodule:: gain.task_graph.cache

.. autoclass:: TaskCache
   :members:

.. autoclass:: FileTaskCache
   :members:

.. autoclass:: NoTaskCache
   :members:

.. autoclass:: CacheRecord
   :members:

.. autoclass:: CacheRecordType
   :members:
