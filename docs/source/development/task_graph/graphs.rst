Graphs
======

A :class:`~gain.task_graph.graph.TaskGraph` holds the tasks of one run and
the dependencies between them. This page describes how to build a task
graph and how a task executor consumes it. The examples import the classes
from ``gain.task_graph.graph``.

Creating a task graph
---------------------

Create an empty graph with ``TaskGraph()``. Then add each task with
:meth:`~gain.task_graph.graph.TaskGraph.create_task`. The call takes the id
of the task, the function that the task calls, and these keyword arguments:

``args`` and ``kwargs``
    The positional and keyword arguments of the function.

``deps``
    The tasks that must finish before this task runs.

``input_files``, ``output_files`` and ``intermediate_output_files``
    The files that the task reads and writes. A file task cache uses them
    to decide whether the task must run again.

``create_task`` returns a :class:`~gain.task_graph.graph.Task`, the handle of
the new task. When a task runs, each ``Task`` in its ``args`` or ``kwargs``
is replaced by the result of that task. The graph infers that dependency, so
you do not have to repeat the task in ``deps``. A task that is only in
``deps`` passes no value: the task waits for it and gets nothing from it.

.. code-block:: python

    from gain.task_graph.graph import TaskGraph

    graph = TaskGraph()
    prepare = graph.create_task("prepare", prepare_dir, args=[out_dir])
    part = graph.create_task(
        "part", compute, args=[region], deps=[prepare],
        output_files=[f"{out_dir}/part.txt"])
    graph.create_task("merge", merge, args=[part])

Here ``merge`` gets the result of ``part`` as its argument. ``part`` waits
for ``prepare`` but does not get its result.

:func:`~gain.task_graph.graph.chain_tasks` combines several task
descriptions into one task that runs their functions in order, in one
process. Each task after the first depends on no task, or only on the task
before it, and gets the result of that task.

The lifecycle
-------------

You build a graph and give it to a task executor. The task executor runs the
tasks and consumes the graph: it removes each task from the graph when it
schedules the task. Build a new graph for each run.

Before you give the graph to a task executor, you can reduce it.
:meth:`~gain.task_graph.graph.TaskGraph.prune` keeps the named task ids and
the tasks that they depend on, and removes every other task:

.. code-block:: python

    graph.prune(["merge"])

:meth:`~gain.task_graph.graph.TaskGraph.topological_order` lists the tasks
so that each task comes after its deps.
:meth:`~gain.task_graph.graph.TaskGraph.empty` tells whether the graph has
tasks left.

Task descriptions
-----------------

The second form builds a task without a graph.
:meth:`~gain.task_graph.graph.TaskGraph.make_task` takes the same arguments
as ``create_task`` and returns a :class:`~gain.task_graph.graph.TaskDesc`.
:meth:`~gain.task_graph.graph.TaskGraph.add_task` adds one description to a
graph, and :meth:`~gain.task_graph.graph.TaskGraph.add_tasks` adds a list
of them.

This form suits code that returns tasks to a caller that owns the graph.
The ``create_statistics_build_tasks`` method of a resource implementation
returns a list of ``TaskDesc``, and ``grr_manage`` adds it to its graph with
``add_tasks``. :doc:`../resources/adding_a_resource_type` describes that
method.

A running example
-----------------

The example counts the ``N`` bases of a reference genome. A module-level
function takes a ``GenomicResource`` and a region. It opens the reference
genome inside the function and counts the ``N`` bases. A second function
sums the counts:

.. literalinclude:: ../../../../core/tests/small/task_graph/test_docs_graphs.py
   :language: python
   :start-after: # The docs example of the task functions starts here.
   :end-before: # The docs example of the task functions ends here.

The graph builder adds one task for each chromosome. The summary task takes
the chromosome tasks as ``args``, so it depends on each of them and gets
their counts:

.. literalinclude:: ../../../../core/tests/small/task_graph/test_docs_graphs.py
   :language: python
   :start-after: # The docs example of the graph builder starts here.
   :end-before: # The docs example of the graph builder ends here.

The tasks pass the resource, not an open genome. A task executor can run a
task in another process or on another host, so the function opens the
genome where it runs. The statistics tasks of the reference genome
implementation use the same pattern: they pass the resource in ``args``.

API
---

The classes below are defined in the module ``gain.task_graph.graph``, and
so is ``chain_tasks``. ``Task`` and ``TaskGraph`` are also exported by
``gain.task_graph``.

.. currentmodule:: gain.task_graph.graph

.. autosummary::

   chain_tasks
   TaskGraph
   TaskGraph.add_task
   TaskGraph.add_tasks
   TaskGraph.create_task
   TaskGraph.empty
   TaskGraph.get_task_desc
   TaskGraph.has_task
   TaskGraph.make_task
   TaskGraph.prune
   TaskGraph.tasks
   TaskGraph.topological_order
   TaskDesc
   Task

.. autofunction:: chain_tasks

.. autoclass:: TaskGraph
   :members:

.. autoclass:: TaskDesc
   :members:

.. autoclass:: Task
   :members:
