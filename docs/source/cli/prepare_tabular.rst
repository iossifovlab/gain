prepare_tabular
===============

``prepare_tabular`` prepares a tabular file for parallel annotation. It reads
a TSV or CSV file with a header, plain or gzip/bgzip-compressed. It sorts the
rows by chromosome and position, and writes a tab-separated,
bgzip-compressed file and its tabix index. Use it before you annotate a large
tabular file, so that :doc:`annotate_tabular` can split the job by genomic
region and run the parts in parallel.

Examples
--------

.. code-block:: bash

    prepare_tabular SSC_WES_variants_select.tsv.gz

Writes ``SSC_WES_variants_select.sorted.tsv.bgz`` and its tabix index
``SSC_WES_variants_select.sorted.tsv.bgz.tbi`` next to the input.

.. code-block:: bash

    prepare_tabular variants.csv -R hg38/genomes/GRCh38-hg38 -o variants.sorted.tsv.bgz

Sorts the chromosomes in the order of the ``hg38/genomes/GRCh38-hg38``
reference genome, not in lexicographic order.

.. code-block:: bash

    prepare_tabular variants.tsv.gz --sort-threads 8 --sort-buffer 4G -w /scratch/sort_tmp

Runs the sort with 8 threads and a 4 GB memory buffer, and keeps the
temporary files in ``/scratch/sort_tmp``.

See also
--------

* :ref:`getting-started-cli-parallel`: prepare a large input and annotate it
  in parallel.

Option reference
----------------

.. argparse::
    :module: gain.annotation.prepare_tabular
    :func: _build_argument_parser
    :prog: prepare_tabular
