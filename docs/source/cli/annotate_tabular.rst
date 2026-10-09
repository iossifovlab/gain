annotate_tabular
================

``annotate_tabular`` annotates the variants, positions or regions of a
tabular file with an annotation pipeline. It reads a TSV or CSV file with a
header, plain or gzip/bgzip-compressed, and a pipeline, a YAML file or a GRR
pipeline resource id. It writes a copy of the table with one new column for
each attribute of the pipeline. Use it for most annotation jobs. When the
input is sorted, bgzip-compressed and tabix-indexed, the tool splits the job
by genomic region and runs the parts in parallel.

Examples
--------

.. code-block:: bash

    annotate_tabular small_input.csv pipeline/hg38_clinical_annotation

Annotates the variants of ``small_input.csv`` with the
``pipeline/hg38_clinical_annotation`` pipeline resource and writes
``small_input.annotated.csv``.

.. code-block:: bash

    annotate_tabular input.tsv annotation.yaml --col-chrom CHROMOSOME -o my_output.tsv

Reads the chromosome from the ``CHROMOSOME`` column instead of ``chrom`` and
writes the result to ``my_output.tsv``.

.. code-block:: bash

    annotate_tabular variants.sorted.tsv.bgz pipeline/hg38_clinical_annotation -r 30_000_000 -j 10

Splits a tabix-indexed input into 30 Mbp regions and annotates them with 10
parallel jobs.

See also
--------

* :ref:`getting-started-cli-quick-test`: run a first annotation.
* :ref:`getting-started-cli-custom-pipeline`: annotate with a pipeline file.
* :ref:`getting-started-cli-parallel`: prepare a large input and annotate it
  in parallel.
* :ref:`getting-started-cli-positions-regions`: annotate positions and
  regions.
* :ref:`getting-started-cli-reannotation`: reuse the attributes of a previous
  annotation.
* :ref:`annotation-cli-annotate-tabular`: the column options and the common
  options.

Option reference
----------------

.. argparse::
    :module: gain.annotation.annotate_tabular
    :func: _build_argument_parser
    :prog: annotate_tabular
