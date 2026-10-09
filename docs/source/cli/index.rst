Command line tools
==================

GAIn installs the command line tools below. Each tool prints its options
with ``--help``.

.. toctree::
    :hidden:

    grr_browse
    grr_manage
    grr_cache_repo
    annotate_tabular
    annotate_vcf
    prepare_tabular
    annotate_doc
    annotate_variant_effects
    annotate_variant_effects_vcf
    binning_tool
    draw_score_histograms
    to_gpf_gene_models_format
    install_vep_cache

GRR management
--------------

* :doc:`grr_browse`: list the resources of the configured GRRs.
* :doc:`grr_manage`: build and repair the index and manifest files of a GRR.
* :doc:`grr_cache_repo`: download the resources that an annotation pipeline uses into the GRR cache.

Annotation
----------

* :doc:`annotate_tabular`: annotate the variants or regions of a tabular file.
* :doc:`annotate_vcf`: annotate the variants of a VCF file.
* :doc:`prepare_tabular`: sort and tabix-index a tabular file, so that ``annotate_tabular`` can annotate it in parallel.
* :doc:`annotate_doc`: write the documentation of an annotation pipeline.

Effect annotation
-----------------

* :doc:`annotate_variant_effects`: annotate the effects of the variants of a tabular file on genes.
* :doc:`annotate_variant_effects_vcf`: annotate the effects of the variants of a
  VCF file on genes.

Resource and score utilities
----------------------------

* :doc:`binning_tool`: bin genomic scores into a fixed genome grid.
* :doc:`draw_score_histograms`: draw the histograms of the scores of a resource.
* :doc:`to_gpf_gene_models_format`: convert gene models to the GPF format.
* :doc:`install_vep_cache`: download and unpack a VEP cache for the VEP annotator plugin.
