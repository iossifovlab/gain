Working with resources in Python
================================

This chapter is the in-depth description of the *resource* half of GAIn's
declared API: the objects you get back when you open a repository and ask it
for something, and the objects those hand you in turn.

.. rubric:: Where the line falls

Everything here is Python. The *same* resources have a curator-facing side —
the ``genomic_resource.yaml`` keys that define them and the ``grr_manage``
commands that build and publish them — and that side is documented on
:doc:`/grr`, not here. The two pages meet at two places, and each is
documented on exactly one side:

* A score's ``scores:`` block. How to write it — the keys it accepts and the
  values they take — is documented on the YAML side: see
  :ref:`grr-position-scores` and its siblings. What that block *becomes* at
  runtime is a :class:`~gain.genomic_resources.score_def.GenomicScoreDef`,
  and that is described in :doc:`resources/scores`.
* A score's ``histogram:`` block. How to write it is likewise documented on
  the YAML side: see :ref:`grr-histogram-configuration`, which also carries
  the one Python example that belongs on the user page, the custom
  ``plot_function``. The configuration *objects* it parses into, and the
  histogram objects a statistics build produces, are described in
  :doc:`resources/histograms`.

If you are new to the Python interface, :doc:`/development/overview` is the
getting-started guide: it connects to a repository and runs five short
end-to-end examples. This chapter picks up where it stops.

.. rubric:: The shape of the API

Builder functions are the entry points. Each takes a resource id and a
repository, and returns a typed object that knows how to read that kind of
resource. All of them, the repository constructors and the GAIn types they
return are imported from one package, ``gain.grr``. The data-frame and
AnnData loaders return plain pandas and anndata objects, and
``GenomicContext`` stays in ``gain.genomic_resources.genomic_context``; none
of these is re-exported:

.. code-block:: python

    from gain.grr import (
        build_gene_models_from_resource_id,
        build_genomic_resource_repository,
        build_reference_genome_from_resource_id,
        build_score_from_resource_id,
    )

    grr = build_genomic_resource_repository()

    genome = build_reference_genome_from_resource_id("hg38/genomes/GRCh38-hg38", grr)
    genes = build_gene_models_from_resource_id("hg38/gene_models/MANE/1.5", grr)
    score = build_score_from_resource_id("hg38/scores/phastCons100way", grr)

Every builder comes in two forms, ``*_from_resource_id(resource_id, grr)``
and ``*_from_resource(resource)`` for a
:class:`~gain.genomic_resources.repository.GenomicResource` already in hand.
The :ref:`factories` section below lists them all, with the resource kind
each one builds. Each name is the same object as in the module that defines
it, and the chapters below refer to those defining modules; importing from
either place gives the same thing.

.. _factories:

Factories
---------

Every function in this section lives in the package ``gain.grr``. The
package also exports the types ``GenomicResourceRepo`` and
``GenomicResource``. The outline lists each factory under the kind of
resource it builds, and each row links to the entry below it. The data-frame
and AnnData loaders return a pandas DataFrame and an AnnData object. The
other builders return the typed object that the name of the resource kind
suggests. For example, ``build_score_from_resource_id`` returns a
``GenomicScore``.

.. currentmodule:: gain.grr

.. rubric:: Repositories (see :doc:`repositories <resources/repositories>`)

.. autosummary::

   build_genomic_resource_repository
   build_genomic_resource_group_repository
   get_default_grr_definition
   get_default_grr_definition_path

.. rubric:: Genomic context

.. autosummary::

   get_genomic_context
   get_grr_from_context

.. rubric:: Reference genomes

.. autosummary::

   build_reference_genome_from_resource_id
   build_reference_genome_from_resource

.. rubric:: Gene models

.. autosummary::

   build_gene_models_from_resource_id
   build_gene_models_from_resource

.. rubric:: Liftover chains (see :doc:`liftover chains <resources/liftover_chains>`)

.. autosummary::

   build_liftover_chain_from_resource_id
   build_liftover_chain_from_resource

.. rubric:: Genomic scores

.. autosummary::

   build_score_from_resource_id
   build_score_from_resource
   build_position_score_from_resource_id
   build_position_score_from_resource
   build_allele_score_from_resource_id
   build_allele_score_from_resource
   build_fragment_score_from_resource_id
   build_fragment_score_from_resource

.. rubric:: Gene scores (see :doc:`gene scores <resources/gene_scores>`)

.. autosummary::

   build_gene_score_from_resource_id
   build_gene_score_from_resource

.. rubric:: Gene set collections (see :doc:`gene sets <resources/gene_sets>`)

.. autosummary::

   build_gene_set_collection_from_resource_id
   build_gene_set_collection_from_resource

.. rubric:: Data frames and AnnData (see :doc:`data frames and AnnData <resources/data_frames_and_anndata>`)

.. autosummary::

   load_data_frame_from_resource_id
   load_data_frame_from_resource
   load_ann_data_from_resource_id
   load_ann_data_from_resource

.. rubric:: Entries

.. autofunction:: build_genomic_resource_repository

.. autofunction:: build_genomic_resource_group_repository

.. autofunction:: get_default_grr_definition

.. autofunction:: get_default_grr_definition_path

.. autofunction:: get_genomic_context

.. autofunction:: get_grr_from_context

.. autofunction:: build_reference_genome_from_resource_id

.. autofunction:: build_reference_genome_from_resource

.. autofunction:: build_gene_models_from_resource_id

   .. code-block:: python

       from gain.grr import build_gene_models_from_resource_id

.. autofunction:: build_gene_models_from_resource

   .. code-block:: python

       from gain.grr import build_gene_models_from_resource

.. autofunction:: build_liftover_chain_from_resource_id

.. autofunction:: build_liftover_chain_from_resource

.. autofunction:: build_score_from_resource_id

.. autofunction:: build_score_from_resource

.. autofunction:: build_position_score_from_resource_id

.. autofunction:: build_position_score_from_resource

.. autofunction:: build_allele_score_from_resource_id

.. autofunction:: build_allele_score_from_resource

.. autofunction:: build_fragment_score_from_resource_id

.. autofunction:: build_fragment_score_from_resource

.. autofunction:: build_gene_score_from_resource_id

.. autofunction:: build_gene_score_from_resource

.. autofunction:: build_gene_set_collection_from_resource_id

.. autofunction:: build_gene_set_collection_from_resource

.. autofunction:: load_data_frame_from_resource_id

.. autofunction:: load_data_frame_from_resource

.. autofunction:: load_ann_data_from_resource_id

.. autofunction:: load_ann_data_from_resource

Two conventions run through all of them.

**Opening is explicit.** A reference genome and a genomic score hold file
handles, so they are built closed and must be opened before they will answer
a query — ``genome.open()`` and ``score.open()`` both open the object and
return it, so the call chains.

Both are also context managers, but **entering one does not open it**:
``__enter__`` returns the object unchanged, and the ``with`` block's
contribution is the guaranteed ``close()`` on the way out. The spelling that
does both is therefore ``with builder(...).open() as obj:`` — keep the
``.open()``.

Gene models are the exception that proves the rule: they are loaded wholly
into memory by :meth:`~gain.genomic_resources.gene_models.GeneModels.load`
and hold nothing to close.

**The repository is the only thing that resolves ids.** Once built, a typed
object never looks a resource id up again — it reads its own resource's
files, through that resource's protocol. That is why a builder needs the
repository handed to it, and why closing the repository is separate from
closing the objects built through it.

.. rubric:: Not covered here

The annotation side of the declared API — pipelines, annotators, and the
``gain.annotation.annotators`` entry-point group — is the subject of its own
chapter, :doc:`annotators`. ``gain.genomic_resources.testing``, the task graph, and the
effect-annotation engine are outside the declared API and are documented, to
the extent they are, by the generated
:doc:`module_index <module_index>`.

.. toctree::
   :maxdepth: 2

   resources/repositories
   resources/reference_genomes
   resources/gene_models
   resources/scores
   resources/gene_scores
   resources/histograms
   resources/liftover_chains
   resources/gene_sets
   resources/data_frames_and_anndata
   resources/adding_a_resource_type
