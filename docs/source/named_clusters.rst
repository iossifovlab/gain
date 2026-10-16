.. _named-clusters:

Named clusters
==============

The GAIn command-line tools that run a task graph, for example
:doc:`annotate_tabular <cli/annotate_tabular>`,
:doc:`annotate_vcf <cli/annotate_vcf>` and :doc:`grr_manage <cli/grr_manage>`,
run their tasks on a dask cluster. A *cluster definition* holds the ``type``,
the ``params`` and the worker count that build one dask cluster. A *named
cluster* is a cluster definition stored under a name. The ``-N`` flag selects
a named cluster by its name.


The named cluster file
----------------------

GAIn reads the named clusters from the dask configuration, under the key
``gain_named_cluster``. GAIn bundles a set of named clusters, and dask merges
the YAML files in the dask config directory over them. The dask config
directory is ``~/.config/dask/`` by default. The ``DASK_CONFIG`` environment
variable names a different directory. See the
`dask configuration documentation <https://docs.dask.org/en/stable/configuration.html>`_
for all the locations that dask reads.

When GAIn starts for the first time, dask writes a copy of the bundled file to
``named_cluster.yaml`` in the dask config directory. dask writes the copy only
when the file does not exist, and never updates it. GAIn does not change this
file. Edit it to add or change named clusters, or delete it to use only the
bundled named clusters.


The shape of the file
---------------------

``gain_named_cluster`` holds three keys:

- ``default``: the name of the named cluster that GAIn uses when you do not
  give ``-N``.
- ``clusters``: a mapping from each name to its cluster definition.
- ``distributed``: dask ``distributed`` settings that GAIn applies when it
  builds a cluster.

The key under ``clusters`` is the name of the named cluster. A cluster
definition has no ``name`` key, and GAIn refuses a cluster definition that
holds one. To give the dask jobs a name, put it under ``params``.

.. code-block:: yaml

    gain_named_cluster:
      default: local
      clusters:
        slurm_large:
          type: slurm
          number_of_workers: 10
          params:
            walltime: "48:00:00"
            cores: 1
            memory: 8GB
            log_directory: ./slurm_worker_logs

The keys of a cluster definition are these:

- ``type``: ``local``, ``sge``, ``slurm`` or ``manual``.
- ``params``: the arguments that GAIn gives to the dask cluster class, for
  example ``LocalCluster``, ``SGECluster`` or ``SLURMCluster``. For ``manual``,
  ``params`` holds the ``address`` of a running dask scheduler.
- ``number_of_workers``: a fixed number of workers.
- ``adapt_params``: the ``minimum`` and ``maximum`` number of workers of an
  adaptive cluster.


How a user file merges with the bundled file
--------------------------------------------

dask merges mappings key by key. An entry under ``clusters`` in a user file
does one of two things:

- When the name is new, the entry adds a named cluster.
- When the name is bundled, the entry overrides only the keys that it sets.
  The bundled named cluster keeps all the other keys.

For example, this user file changes only the memory of the bundled ``sge``
named cluster:

.. code-block:: yaml

    gain_named_cluster:
      clusters:
        sge:
          params:
            memory: 6GB

.. warning::

    When a cluster definition holds both ``number_of_workers`` and
    ``adapt_params``, GAIn uses ``number_of_workers``. If an override adds
    ``adapt_params`` to a bundled named cluster that sets
    ``number_of_workers``, set ``number_of_workers: null`` in the same
    override:

    .. code-block:: yaml

        gain_named_cluster:
          clusters:
            sge:
              number_of_workers: null
              adapt_params:
                minimum: 3
                maximum: 100


Select a cluster on the command line
------------------------------------

``-N <name>`` (also ``--dask-cluster-name``) selects the named cluster
``<name>``. GAIn refuses a name that is not under
``gain_named_cluster.clusters``, and the message lists the defined names.

.. code-block:: bash

    annotate_tabular input.tsv.bgz pipeline.yaml -N slurm_large -j 100

``-j`` overrides the worker count of the cluster definition.

``-c <file>`` (also ``--dccf`` and ``--dask-cluster-config-file``) reads a
YAML file that holds one cluster definition without a name, and builds the
cluster from it. The file does not use ``gain_named_cluster``:

.. code-block:: yaml

    type: slurm
    number_of_workers: 10
    params:
      cores: 1
      memory: 8GB

``-N`` and ``-c`` cannot be given together.


The old key ``dae_named_cluster``
---------------------------------

Earlier GAIn releases read the named clusters from the key
``dae_named_cluster``, where ``clusters`` is a list of cluster definitions and
each cluster definition has a ``name`` key. The ``named_cluster.yaml`` that
dask wrote for an earlier release has this shape.

When the dask configuration holds ``dae_named_cluster``, GAIn converts it to
the new shape for the run and logs a warning. The values of the old key win
over the bundled values, and a bundled key that the old key lacks stays.
An old cluster definition replaces the bundled cluster definition of the same
name whole, as the old list replaced the bundled list in earlier releases.
GAIn stops reading ``dae_named_cluster`` in release 2026.11.0. Before that
release, delete the old ``named_cluster.yaml`` in the dask config directory,
or move its entries to ``gain_named_cluster`` in the new shape.
