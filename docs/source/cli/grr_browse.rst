grr_browse
==========

``grr_browse`` lists the resources of the GRRs that a GRR definition file
names. It reads the GRR definition from ``-g``, from the
``GRR_DEFINITION_FILE`` environment variable or from
``~/.grr_definition.yaml``, in that order, and uses the default IossifovLab
GRR when it finds none. It prints the definition that it uses, then one line
for each resource: the type, the version, the file count, the size, the GRR
and the resource id. It writes no files. Use it to check which GRRs GAIn
sees and to find a resource id for an annotation pipeline.

Examples
--------

.. code-block:: bash

    grr_browse -g my_grr_definition.yaml

Lists every resource of the GRRs in ``my_grr_definition.yaml``.

.. code-block:: bash

    grr_browse -t position_score -s phyloP

Lists the position scores that match the full-text search term ``phyloP``.

.. code-block:: bash

    grr_browse -q 'hg38/scores/*' --summary

Lists the resources whose ids match the wildcard query, each with its summary.

See also
--------

* :ref:`getting-started-cli-browse`: the output of ``grr_browse``, line by
  line.
* :ref:`grr-searching-resources`: the search index and the search terms.
* :doc:`../grr_configuration`: how the tools find the GRR definition.

Option reference
----------------

.. argparse::
    :module: gain.genomic_resources.cli
    :func: _build_browse_argument_parser
    :prog: grr_browse
