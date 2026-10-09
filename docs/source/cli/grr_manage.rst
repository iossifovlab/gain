grr_manage
==========

``grr_manage`` builds and maintains a Genomic Resource Repository (GRR). It
reads the resource directories of a GRR, each with its
``genomic_resource.yaml`` and data files. It writes the manifests, the
statistics and the info pages of the resources, and the repository-global
files: the ``.CONTENTS`` files, the search index and the repository index
pages. Use it when you create a GRR, after you add or change a resource, and
when you publish a GRR. Each subcommand has a ``repo-*`` form for the whole
GRR and a ``resource-*`` form for one resource.

Examples
--------

.. code-block:: bash

    grr_manage repo-init

Turns the current directory into an empty GRR.

.. code-block:: bash

    grr_manage resource-repair -r hg38/scores/phyloP7way

Builds the manifest, the statistics and the info page of one resource of the
GRR that holds the current directory.

.. code-block:: bash

    grr_manage repo-repair -R /data/my_GRR -j 8

Repairs every resource of the GRR in ``/data/my_GRR`` with 8 parallel jobs,
then publishes the repository-global files.

See also
--------

* :ref:`grr-manage-usage`: the subcommands, and the files that the
  ``repo-*`` and ``resource-*`` forms write.
* :ref:`grr-manage-force-dry-run`: when to force a rebuild of the statistics.
* :ref:`getting-started-grr-create`: create a GRR and add resources to it.

Option reference
----------------

.. argparse::
    :module: gain.genomic_resources.cli
    :func: _build_manage_argument_parser
    :prog: grr_manage
