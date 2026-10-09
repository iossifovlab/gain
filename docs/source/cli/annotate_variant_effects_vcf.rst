annotate_variant_effects_vcf
============================

``annotate_variant_effects_vcf`` annotates the effects of variants on genes
in a VCF file. It reads a VCF file and writes a VCF file that has the worst
effect, the gene effects and the effect details of each alternative allele in
new ``INFO`` fields. Use it when you need only the variant effects of a VCF
file and you do not want to write an annotation pipeline. The
``effect_annotator`` of an annotation pipeline, which ``annotate_tabular`` or
``annotate_vcf`` runs, computes the same effects. It runs together with the
other annotators of the pipeline and takes its configuration from the pipeline
file.

Examples
--------

.. code-block:: bash

    annotate_variant_effects_vcf -gmri hg38/gene_models/MANE/1.4 -rgri hg38/genomes/GRCh38.p14 variants.vcf.gz effects.vcf

Selects the gene models and the reference genome by GRR resource id, and
annotates the variants of ``variants.vcf.gz``.

.. code-block:: bash

    annotate_variant_effects_vcf -gmfn genes.gtf -gmff gtf -rgfn genome.fa -pl 100 variants.vcf.gz effects.vcf

Selects the gene models and the reference genome by file name, and counts a
promoter of 100 base pairs.

.. code-block:: bash

    annotate_variant_effects_vcf -gmri hg38/gene_models/MANE/1.4 -rgri hg38/genomes/GRCh38.p14 \
        --annotation-attributes "WE:worst_effect" variants.vcf.gz > effects.vcf

Writes only the ``WE`` field, and sends the result to standard output.

See also
--------

* :ref:`effect-annotators`: the effect annotator of an annotation pipeline.
* :ref:`grr-configuration`: how the tool finds the GRR for ``-gmri`` and
  ``-rgri``.

Option reference
----------------

.. argparse::
    :module: gain.effect_annotation.cli
    :func: _build_vcf_argument_parser
    :prog: annotate_variant_effects_vcf
