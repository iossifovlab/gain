install_vep_cache
=================

``install_vep_cache`` downloads and unpacks the VEP cache for the VEP
annotator plugin. The ``gain-vep-annotator`` package installs the tool. It
downloads the Homo sapiens GRCh38 cache of VEP release 113 into the
destination directory and unpacks it there. The tool refuses to write into a
non-empty directory.

Examples
--------

.. code-block:: bash

    install_vep_cache /data/vep_cache

Downloads and unpacks the cache into ``/data/vep_cache``.

.. code-block:: bash

    install_vep_cache --continue /data/vep_cache

Resumes an interrupted download.

See also
--------

* :ref:`annotation-vep-full-annotator`: the annotator that reads the cache.

Option reference
----------------

.. argparse::
    :module: vep_annotator.install_vep_cache
    :func: _build_argument_parser
    :prog: install_vep_cache
    :nodescription:
