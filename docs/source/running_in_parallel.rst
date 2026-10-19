.. _running-in-parallel:

Running in parallel
===================

Some GAIn tools split their work into tasks and run the tasks in parallel:
:doc:`annotate_tabular <cli/annotate_tabular>`,
:doc:`annotate_vcf <cli/annotate_vcf>`,
:doc:`binning_tool <cli/binning_tool>` and
:doc:`grr_manage <cli/grr_manage>`. Each of these tools builds a *task
graph*: a set of *tasks* and the order in which they must run. A *task
executor* runs the task graph. The options on this page choose the task
executor, the number of workers, and which tasks run again.

The table in :ref:`running-in-parallel-tools` shows which options each tool
takes.


.. _running-in-parallel-workers:

How many workers
----------------

``-j N`` (also ``--jobs``) sets the number of workers.

- ``-j 1`` runs the tasks one after another, in the tool's own process. With
  ``--fork-tasks``, each task runs in a new process instead.
- A run without ``-j`` starts the default named cluster. The bundled default
  is ``local``, a dask cluster on this machine. dask sizes its workers to the
  CPU count of the machine.
- ``-j N`` with ``N`` above 1 starts the default named cluster with ``N``
  workers. When ``-N`` or ``-c`` names a different cluster, the tool starts
  that cluster with ``N`` workers.
- ``--process-pool`` (also ``--pp``) together with ``-j N`` runs a pool of
  ``N`` processes on this machine instead of a dask cluster. Without ``-j``,
  the pool has one process for each CPU of the machine.
- ``--fork-tasks`` (also ``--fork-task`` and ``--fork``) starts a new
  process for each task, with ``-j 1`` or on a dask cluster. With
  ``--process-pool`` it has no effect.

.. code-block:: bash

    # Run the tasks one after another.
    annotate_tabular input.tsv.bgz pipeline.yaml -j 1

    # Run the tasks on the default named cluster with 8 workers.
    annotate_tabular input.tsv.bgz pipeline.yaml -j 8

    # Run the tasks in a pool of 8 processes.
    annotate_tabular input.tsv.bgz pipeline.yaml -j 8 --process-pool

Do not give ``-N`` or ``-c`` together with ``-j 1`` or ``--process-pool``.
These two task executors do not use a dask cluster.

The annotate tools can split the input into regions only when the input has
a tabix index and ``--region-size`` is above 0. Otherwise they run the tasks
one after another, as with ``-j 1``, whatever ``-j`` says. On such an input,
``-N`` or ``-c`` makes the tool stop with an error.


.. _running-in-parallel-clusters:

Named clusters
--------------

A named cluster is a cluster definition that is stored under a name in the
named cluster file.

- ``-N <name>`` (also ``--dask-cluster-name`` and ``--dcn``) runs the tasks on
  the named cluster ``<name>``.
- ``-c <file>`` (also ``--dccf`` and ``--dask-cluster-config-file``) runs the
  tasks on the cluster that ``<file>`` defines. The file holds one cluster
  definition without a name.

.. code-block:: bash

    annotate_tabular input.tsv.bgz pipeline.yaml -N slurm_large -j 100

:doc:`named_clusters` describes the named cluster file, its shape and how
your own file merges with the bundled file.


.. _running-in-parallel-reruns:

Reruns
------

The annotate tools and ``binning_tool`` record each finished task in the
*task status directory*. When you run the same command again, the tool skips
the tasks that finished and runs only the rest. Thus a run that stopped
halfway continues where it stopped.

A run that succeeds removes the work directory that it created, and the
default task status directory with it. So after a successful run, the same
command runs all the tasks again. The task status directory stays after a
failed run, with ``--keep-work-dir`` or ``--keep-parts``, when ``-w`` names a
work directory that already exists, or when ``-d`` names a directory outside
the work directory.

- ``-d <dir>`` (also ``--task-status-dir`` and ``--tsd``) names the task
  status directory. By default it is ``.task-status`` in the work directory
  of the tool.
- ``--force`` (also ``-f``) ignores the task status directory and runs all
  the tasks again.
- ``-t <id> ...`` (also ``--task-ids``) runs only the tasks with the given
  task ids and all the tasks that they depend on. The tool still skips a
  task that the task status directory records as finished. With
  ``--force``, or with no task status directory, the tool runs all these
  dependencies again, and for a late task that can be most of the task
  graph.
- ``--keep-going`` continues with the other tasks after a task fails. The
  ``grr_manage`` subcommands always continue, so for them the option changes
  nothing.
- ``--task-log-dir <dir>`` names a directory for the logs of the tasks. By
  default it is ``.task-log`` in the work directory of the tool. For
  ``grr_manage``, the default is ``.task-log`` in the repository.

.. code-block:: bash

    # Run again only the tasks that did not finish.
    annotate_vcf input.vcf.gz pipeline.yaml -j 8

    # Run all the tasks again.
    annotate_vcf input.vcf.gz pipeline.yaml -j 8 --force

The annotate tools also take an optional command after the positional
arguments:

- ``run`` runs the task graph. This is the default.
- ``list`` lists the tasks and does not run them.
- ``status`` is the same as ``list``.


.. _running-in-parallel-tools:

Which tool takes what
---------------------

.. list-table::
    :header-rows: 1
    :widths: 20 80

    * - Tool
      - Options
    * - ``annotate_tabular``
      - All the options on this page, and the optional command ``run``,
        ``list`` or ``status``.
    * - ``annotate_vcf``
      - All the options on this page, and the optional command ``run``,
        ``list`` or ``status``.
    * - ``binning_tool``
      - All the options on this page. It takes no command.
    * - ``grr_manage``
      - The subcommands ``repo-stats``, ``resource-stats``, ``repo-info``,
        ``resource-info``, ``repo-repair`` and ``resource-repair`` take the
        options on this page without the command and without ``-d``. The
        other subcommands take none of them. These subcommands keep no task
        status directory. Instead, each run adds tasks only for the
        resources whose statistics are out of date or failed in an earlier
        run. They always continue past a failed task. They write the logs
        of the tasks to ``.task-log`` in the repository, unless
        ``--task-log-dir`` names a different directory. The ``-f``
        (``--force``) of these subcommands is an option of ``grr_manage``:
        it ignores the state of the resource. See
        :ref:`grr-manage-force-dry-run`.

``grr_cache_repo`` has a ``-j`` of its own, with the default 4, and none of
the other options on this page.
