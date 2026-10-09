to_gpf_gene_models_format
=========================

``to_gpf_gene_models_format`` converts a gene models file to the default
GPF gene models format. It reads the input file, loads the gene models and
writes them to the output file. Use it to prepare a gene models resource
from a RefSeq, CCDS, UCSC knownGene or GTF file.

Examples
--------

.. code-block:: bash

    to_gpf_gene_models_format refGene.txt.gz gene_models.txt

Converts a gene models file. The tool infers the format of the input file.

.. code-block:: bash

    to_gpf_gene_models_format --gm-format refseq \
        --chrom-mapping chrom_mapping.txt refGene.txt.gz gene_models.txt

Converts a RefSeq file and renames the chromosomes with a mapping file.

Option reference
----------------

.. argparse::
    :module: gain.genomic_resources.gene_models.to_gpf_gene_models_format
    :func: _build_argument_parser
    :prog: to_gpf_gene_models_format
    :nodescription:
