Named clusters
==============

A dask task executor runs the tasks on a dask cluster. The module
``gain.dask.named_cluster`` builds the dask client of that cluster from a
named cluster or from one cluster definition. A *cluster definition* holds
the ``type``, the ``params`` and the worker count that build one dask
cluster. A *named cluster* is a cluster definition stored under a name.
This page describes the Python side of the named clusters.

Creating a dask client from a named cluster
-------------------------------------------

:func:`~gain.dask.named_cluster.setup_client` takes the name of a named
cluster and a worker count. It returns the dask client and the cluster
definition that built it:

.. code-block:: python

    from gain.dask.named_cluster import setup_client

    client, cluster_definition = setup_client("local", number_of_workers=4)

``None`` as the name selects the named cluster in
``gain_named_cluster.default``. ``None`` as the worker count keeps the
``number_of_workers`` of the cluster definition.

:func:`~gain.dask.named_cluster.setup_client_from_config` takes one cluster
definition mapping, with ``type``, ``params`` and a worker count. It needs
no named cluster file. The cluster definition below starts two workers
that are threads of the current process, with no dashboard:

.. literalinclude:: ../../../../core/tests/small/task_graph/test_docs_named_clusters.py
   :language: python
   :start-after: # The docs example of a low-cost cluster definition starts here.
   :end-before: # The docs example of a low-cost cluster definition ends here.

.. literalinclude:: ../../../../core/tests/small/task_graph/test_docs_named_clusters.py
   :language: python
   :dedent: 4
   :start-after: # The docs example of a client from a cluster definition starts here.
   :end-before: # The docs example of a client from a cluster definition ends here.

``setup_client_from_config`` changes the mapping that it gets, so the
example gives it a copy. The Notes of its docstring list the changes.

The lifecycle
-------------

Give the client to a :class:`~gain.task_graph.dask_executor.DaskExecutor`.
The task executor owns the client from then on. When the task executor
closes, it shuts down the client and its cluster. Do not use the client
after that.

Where the line falls
--------------------

The named cluster file, the four types ``local``, ``sge``, ``slurm`` and
``manual``, and the options ``-N`` and ``-c`` are the user side. The user
page :doc:`/named_clusters` describes them, and the user page
:doc:`/running_in_parallel` describes the task graph options. This page
describes only the two functions that build a dask client.

A running example, part 4
-------------------------

Part 1, on the page :doc:`graphs`, builds a graph that counts the ``N``
bases of a reference genome. Part 2, on the page :doc:`task_executors`,
runs it from Python, and part 3, on the page :doc:`running_from_a_cli`,
runs it from a command line. Part 4 runs it on a dask cluster.

The first run builds a dask client from the cluster definition above and
gives it to a ``DaskExecutor``. The ``with`` block closes the task
executor, so the client is closed after the run:

.. literalinclude:: ../../../../core/tests/small/task_graph/test_docs_named_clusters.py
   :language: python
   :start-after: # The docs example of a run on a dask client starts here.
   :end-before: # The docs example of a run on a dask client ends here.

The second run uses the script of part 3. ``-N local`` selects the named
cluster ``local``, and the script builds the client with ``setup_client``:

.. code-block:: bash

    count_n --grr-directory grr -R genome -N local -d task-status

A dask run prints logs that change from run to run, so the page shows no
output. After the run, the record of the summary task ``sum_n`` in the
task status directory ``task-status`` holds the sum.

API
---

``setup_client`` and ``setup_client_from_config`` are defined in the
module ``gain.dask.named_cluster``.

.. currentmodule:: gain.dask.named_cluster

.. autosummary::

   setup_client
   setup_client_from_config

.. autofunction:: setup_client

.. autofunction:: setup_client_from_config
