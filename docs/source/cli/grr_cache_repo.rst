grr_cache_repo
==============

``grr_cache_repo`` downloads the resources that an annotation pipeline uses
into the GRR cache. It reads the pipeline, a file or a GRR resource id, and
the GRR definition. It writes a copy of each resource of the pipeline into
the ``cache_dir`` of the GRR definition. Use it before a large annotation
job, so that the job reads local copies and does not stop to download
resources.

Examples
--------

.. code-block:: bash

    grr_cache_repo pipeline/hg38_clinical_annotation

Caches the resources of the ``pipeline/hg38_clinical_annotation`` pipeline
resource.

.. code-block:: bash

    grr_cache_repo -g my_grr_definition.yaml -j 8 custom_pipeline.yaml

Caches the resources of a pipeline file with 8 parallel downloads, into the
cache that ``my_grr_definition.yaml`` configures.

See also
--------

* :ref:`getting-started-cli-caching`: set up a cache and annotate from it.
* :ref:`grr-configuration-caching`: the ``cache_dir`` entry of a GRR
  definition.

Option reference
----------------

.. argparse::
    :module: gain.genomic_resources.cli_cache_repo
    :func: _build_argument_parser
    :prog: grr_cache_repo
