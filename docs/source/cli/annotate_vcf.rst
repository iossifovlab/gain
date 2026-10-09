annotate_vcf
============

``annotate_vcf`` annotates the variants of a VCF file with an annotation
pipeline. It reads a VCF file, plain or gzip/bgzip-compressed, and a
pipeline, a YAML file or a GRR pipeline resource id. It writes a copy of the
VCF file with the attributes of the pipeline in the ``INFO`` column. Use it
when the variants are in VCF format and the result must stay a VCF file.
When the input is sorted, bgzip-compressed and tabix-indexed, the tool
splits the job by genomic region and runs the parts in parallel.

Examples
--------

.. code-block:: bash

    annotate_vcf small_input.vcf custom_pipeline.yaml -o vcf.annotated.vcf

Annotates the variants of ``small_input.vcf`` with a pipeline file and
writes the result to ``vcf.annotated.vcf``.

.. code-block:: bash

    annotate_vcf small_input.sorted.vcf.bgz custom_pipeline.yaml -r 30_000_000 -j 8

Splits a tabix-indexed VCF file into 30 Mbp regions and annotates them with 8
parallel jobs.

.. code-block:: bash

    annotate_vcf annotated.vcf.gz new_pipeline.yaml --reannotate old_pipeline.yaml -o reannotated.vcf.gz

Annotates a VCF file that ``old_pipeline.yaml`` annotated before, and
computes only the attributes that are new or changed in
``new_pipeline.yaml``.

See also
--------

* :ref:`getting-started-cli-vcf`: annotate a VCF file and prepare it for
  parallel annotation.
* :ref:`annotation-cli-annotate-vcf`: the common options.
* :ref:`annotation-cli-notes`: the options that ``annotate_vcf`` shares with
  ``annotate_tabular``.

Option reference
----------------

.. argparse::
    :module: gain.annotation.annotate_vcf
    :func: _build_argument_parser
    :prog: annotate_vcf
