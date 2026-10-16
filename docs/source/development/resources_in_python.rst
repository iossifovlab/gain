Genomic resources
=================

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

Builder functions are the entry points. The examples import the package as
``import gain.grr as grr`` and call them through it,
``grr.build_score_from_resource_id(...)``. A repository in an example is
named ``repo``. Each function takes a resource id and a
repository, and returns a typed object that knows how to read that kind of
resource. All of them, the repository constructors and the GAIn types they
return are imported from one package, ``gain.grr``. The data-frame and
AnnData loaders return plain pandas and anndata objects, and
``GenomicContext`` stays in ``gain.genomic_resources.genomic_context``; none
of these is re-exported:

.. code-block:: python

    import gain.grr as grr

    repo = grr.build_genomic_resource_repository()

    genome = grr.build_reference_genome_from_resource_id("hg38/genomes/GRCh38-hg38", repo)
    genes = grr.build_gene_models_from_resource_id("hg38/gene_models/MANE/1.5", repo)
    score = grr.build_score_from_resource_id("hg38/scores/phastCons100way", repo)

Every builder comes in two forms, ``*_from_resource_id(resource_id, grr)``
and ``*_from_resource(resource)`` for a
:class:`~gain.genomic_resources.repository.GenomicResource` already in hand.
Every page below opens with a "Creating a …" section that names its builder
pair, and the pair's reference entries sit in the page's API section. Each
name is the same object as in the module that defines
it, and the chapters below refer to those defining modules; importing from
either place gives the same thing.

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
   resources/position_scores
   resources/allele_scores
   resources/fragment_scores
   resources/gene_scores
   resources/histograms
   resources/liftover_chains
   resources/gene_sets
   resources/data_frames_and_anndata
   resources/adding_a_resource_type
