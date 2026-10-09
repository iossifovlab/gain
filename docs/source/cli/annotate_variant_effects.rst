annotate_variant_effects
========================

``annotate_variant_effects`` annotates the effects of variants on genes. It
reads a delimited text file with one variant on each line, from a file or from
standard input. It writes the same file with extra columns for the worst
effect, the gene effects and the effect details. Use it when you need only the
variant effects of a variant table and you do not want to write an annotation
pipeline. The ``effect_annotator`` of an annotation pipeline, which
``annotate_tabular`` or ``annotate_vcf`` runs, computes the same effects. It
runs together with the other annotators of the pipeline and takes its
configuration from the pipeline file.

Examples
--------

.. code-block:: bash

    annotate_variant_effects -gmri hg38/gene_models/MANE/1.4 -rgri hg38/genomes/GRCh38.p14 variants.tsv effects.tsv

Selects the gene models and the reference genome by GRR resource id, and
annotates the variants of ``variants.tsv``.

.. code-block:: bash

    annotate_variant_effects -gmfn genes.gtf -gmff gtf -rgfn genome.fa -pl 100 variants.tsv effects.tsv

Selects the gene models and the reference genome by file name, and counts a
promoter of 100 base pairs.

.. code-block:: bash

    annotate_variant_effects -gmri hg38/gene_models/MANE/1.4 -rgri hg38/genomes/GRCh38.p14 \
        --input-sep , --annotation-attributes "WE:worst_effect,GE:gene_effects" variants.csv effects.tsv

Reads a comma-separated file and writes two columns with the names ``WE`` and
``GE``.

See also
--------

* :ref:`effect-annotators`: the effect annotator of an annotation pipeline.
* :ref:`grr-configuration`: how the tool finds the GRR for ``-gmri`` and
  ``-rgri``.

Option reference
----------------

.. argparse::
    :module: gain.effect_annotation.cli
    :func: _build_columns_argument_parser
    :prog: annotate_variant_effects
