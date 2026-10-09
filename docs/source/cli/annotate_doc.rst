annotate_doc
============

``annotate_doc`` writes the documentation of an annotation pipeline. It
reads a pipeline, a YAML file or a GRR pipeline resource id, and the GRR
definition that resolves the resources of the pipeline. It writes an HTML
page that describes the annotators of the pipeline and the attributes that
they produce. Use it to review a pipeline before you annotate with it, or to
share a description of a pipeline. Without ``-o``, the tool prints the HTML
page to the standard output.

Examples
--------

.. code-block:: bash

    annotate_doc custom_pipeline.yaml > doc.html

Writes the documentation of a pipeline file to ``doc.html``.

.. code-block:: bash

    annotate_doc pipeline/hg38_clinical_annotation -o hg38_clinical_annotation.html

Writes the documentation of a pipeline resource of the GRR to
``hg38_clinical_annotation.html``.

See also
--------

* :ref:`getting-started-cli-custom-pipeline`: review the attributes of a
  custom pipeline.
* :ref:`annotation-cli-annotate-doc`: an example of the page and the common
  options.

Option reference
----------------

The tool also accepts the ``pipeline`` argument and the GRR context options
``-g``, ``--grr-directory``, ``-R``, ``-G`` and ``-ar``. The
``annotate_tabular`` :doc:`option reference <annotate_tabular>` describes
them.

.. argparse::
    :module: gain.annotation.annotate_doc
    :func: configure_argument_parser
    :prog: annotate_doc
    :nodescription:

    -o --output : @replace
        The name of the output HTML file. Without this option, the tool
        prints the HTML page to the standard output.
