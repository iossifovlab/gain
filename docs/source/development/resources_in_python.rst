Working with resources in Python
================================

This chapter is the in-depth description of the *resource* half of GAIn's
declared API: the objects you get back when you open a repository and ask it
for something, and the objects those hand you in turn.

It is the zoom on the "Resource path" box of the
:doc:`architecture_overview` map. If you have not read that page, read it
first — it says how repositories, resources and the protocol layer fit
together, and this chapter assumes that shape.

Where the line falls
--------------------

Everything here is Python. The *same* resources have a curator-facing side —
the ``genomic_resource.yaml`` keys that define them and the ``grr_manage``
commands that build and publish them — and that side is documented on
:doc:`/grr`, not here. The two pages meet at two places, and each is
documented on exactly one side:

* A score's ``scores:`` block is a YAML question — see
  :ref:`grr-position-scores` and its siblings. What that block *becomes* at
  runtime is a :class:`~gain.genomic_resources.score_def.GenomicScoreDef`,
  and that is described in :doc:`resources/scores`.
* A score's ``histogram:`` block is likewise a YAML question — see
  :ref:`grr-histogram-configuration`, which also carries the one Python
  example that belongs on the user page, the custom ``plot_function``. The
  configuration *objects* it parses into, and the histogram objects a
  statistics build produces, are described in :doc:`resources/histograms`.

If you are new to the Python interface, :doc:`/python_interface` is the
getting-started guide: it connects to a repository and runs three short
end-to-end examples. This chapter picks up where it stops.

The shape of the API
--------------------

Four builder functions are the entry points. Each takes a resource id and a
repository, and returns a typed object that knows how to read that kind of
resource:

.. code-block:: python

    from gain.genomic_resources.repository_factory import build_genomic_resource_repository
    from gain.genomic_resources.reference_genome import build_reference_genome_from_resource_id
    from gain.genomic_resources.gene_models import build_gene_models_from_resource_id
    from gain.genomic_resources.genomic_scores import build_score_from_resource_id

    grr = build_genomic_resource_repository()

    genome = build_reference_genome_from_resource_id("hg38/genomes/GRCh38-hg38", grr)
    genes = build_gene_models_from_resource_id("hg38/gene_models/MANE/1.5", grr)
    score = build_score_from_resource_id("hg38/scores/phastCons100way", grr)

Two conventions run through all of them.

**Opening is explicit.** A reference genome and a genomic score hold file
handles, so they are built closed and must be opened before they will answer
a query — ``genome.open()`` and ``score.open()`` both return the object, so
the call chains. Both are context managers, which is the form to prefer.
Gene models are the exception that proves the rule: they are loaded wholly
into memory by :meth:`~gain.genomic_resources.gene_models.GeneModels.load`
and hold nothing to close.

**The repository is the only thing that knows about ids.** The typed objects
above never re-enter the repository; once built, they read their own files.
That is why a builder needs the repository handed to it, and why closing the
repository is separate from closing the objects built through it.

.. toctree::
   :maxdepth: 2

   resources/repositories
   resources/reference_genomes
   resources/gene_models
   resources/scores
   resources/histograms
   resources/adding_a_resource_type

Not covered here
----------------

The annotation side of the declared API — pipelines, annotators, and the
``gain.annotation.annotators`` entry-point group — is the subject of its own
chapter. ``gain.genomic_resources.testing``, the task graph, and the
effect-annotation engine are outside the declared API and are documented, to
the extent they are, by the generated
:doc:`module_index <module_index>`.
