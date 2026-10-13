.. _factories:

Factories
=========

Every function on this page lives in the package ``gain.grr``. The
package also exports the types ``GenomicResourceRepo`` and
``GenomicResource``. The page lists each factory under the kind of
resource it builds. The data-frame and AnnData loaders return a pandas
DataFrame and an AnnData object. The other builders return the typed object
that the name of the resource kind suggests. For example,
``build_score_from_resource_id`` returns a ``GenomicScore``.

The examples in this chapter import the package under the name ``grr`` and
call the functions through it: ``grr.build_genomic_resource_repository()``.
A repository in an example is named ``repo``. The factories name that
parameter ``grr``, so pass the repository as the second positional argument.

.. currentmodule:: gain.grr

Repositories
------------

See :doc:`repositories <repositories>`.

.. autofunction:: build_genomic_resource_repository

.. autofunction:: build_genomic_resource_group_repository

.. autofunction:: get_default_grr_definition

.. autofunction:: get_default_grr_definition_path

Genomic context
---------------

.. autofunction:: get_genomic_context

.. autofunction:: get_grr_from_context

Reference genomes
-----------------

See :doc:`reference genomes <reference_genomes>`.

.. autofunction:: build_reference_genome_from_resource_id

.. autofunction:: build_reference_genome_from_resource

Gene models
-----------

See :doc:`gene models <gene_models>`.

.. autofunction:: build_gene_models_from_resource_id

.. autofunction:: build_gene_models_from_resource

Liftover chains
---------------

See :doc:`liftover chains <liftover_chains>`.

.. autofunction:: build_liftover_chain_from_resource_id

.. autofunction:: build_liftover_chain_from_resource

Genomic scores
--------------

See :doc:`genomic scores <scores>`, :doc:`position scores <position_scores>`,
:doc:`allele scores <allele_scores>` and :doc:`fragment scores <fragment_scores>`.

.. autofunction:: build_score_from_resource_id

.. autofunction:: build_score_from_resource

.. autofunction:: build_position_score_from_resource_id

.. autofunction:: build_position_score_from_resource

.. autofunction:: build_allele_score_from_resource_id

.. autofunction:: build_allele_score_from_resource

.. autofunction:: build_fragment_score_from_resource_id

.. autofunction:: build_fragment_score_from_resource

Gene scores
-----------

See :doc:`gene scores <gene_scores>`.

.. autofunction:: build_gene_score_from_resource_id

.. autofunction:: build_gene_score_from_resource

Gene set collections
--------------------

See :doc:`gene sets <gene_sets>`.

.. autofunction:: build_gene_set_collection_from_resource_id

.. autofunction:: build_gene_set_collection_from_resource

Data frames and AnnData
-----------------------

See :doc:`data frames and AnnData <data_frames_and_anndata>`.

.. autofunction:: load_data_frame_from_resource_id

.. autofunction:: load_data_frame_from_resource

.. autofunction:: load_ann_data_from_resource_id

.. autofunction:: load_ann_data_from_resource
