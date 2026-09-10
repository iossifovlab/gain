Gene models
===========

A :class:`~gain.genomic_resources.gene_models.GeneModels` object holds a gene
annotation — genes, their transcripts, and the exons of each transcript —
parsed out of whatever format the resource stores it in (GTF, refFlat,
refSeq, CCDS and several others; :doc:`/grr` lists the formats and the
``format`` key that selects one).

Unlike the other resource types, gene models are loaded wholly into memory
rather than read from an open file. The consequence is that
:meth:`~gain.genomic_resources.gene_models.GeneModels.load` is expensive and
:meth:`~gain.genomic_resources.gene_models.GeneModels.close` does nothing:

.. code-block:: python

    from gain.genomic_resources.repository_factory import build_genomic_resource_repository
    from gain.genomic_resources.gene_models import build_gene_models_from_resource_id

    grr = build_genomic_resource_repository()
    genes = build_gene_models_from_resource_id("hg38/gene_models/MANE/1.5", grr).load()

:meth:`~gain.genomic_resources.gene_models.GeneModels.is_loaded` distinguishes
a built-but-empty object from a loaded one, which library code that accepts
either should check rather than calling ``load`` a second time.

Two lookups
-----------

:meth:`~gain.genomic_resources.gene_models.GeneModels.gene_models_by_gene_name`
goes from a gene symbol to its transcripts, and returns ``None`` when the
symbol is unknown — not an empty list, so a missing gene and a gene with no
transcripts stay distinguishable.

:meth:`~gain.genomic_resources.gene_models.GeneModels.gene_models_by_location`
goes the other way, from a position or an interval to the transcripts that
overlap it, and returns a list:

.. code-block:: python

    transcripts = genes.gene_models_by_gene_name("TP53")
    for tm in transcripts or []:
        print(tm.tr_id, tm.chrom, tm.tx, tm.is_coding())

    overlapping = genes.gene_models_by_location("chr17", 7_676_000, 7_690_000)

:meth:`~gain.genomic_resources.gene_models.GeneModels.join_gene_models`
merges several loaded objects into one — the way to query a primary
annotation and a supplementary one as a single set.

Transcripts and exons
---------------------

A :class:`~gain.genomic_resources.gene_models.TranscriptModel` is the unit
most analyses work with. Its coordinates are plain attributes (``chrom``,
``strand``, ``tx`` for the transcribed interval, ``cds`` for the coding one,
``exons``), and its methods derive the parts you usually want:

.. code-block:: python

    tm = transcripts[0]
    if tm.is_coding():
        for region in tm.cds_regions():
            print(region.chrom, region.start, region.stop)
        print("5' UTR:", tm.utr5_regions())
        print("coding length:", tm.cds_len())

:meth:`~gain.genomic_resources.gene_models.TranscriptModel.cds_regions`,
:meth:`~gain.genomic_resources.gene_models.TranscriptModel.utr5_regions`,
:meth:`~gain.genomic_resources.gene_models.TranscriptModel.utr3_regions` and
:meth:`~gain.genomic_resources.gene_models.TranscriptModel.all_regions`
each return a list of ``BedRegion``. Note that ``utr5``/``utr3`` are named
for the *transcript's* orientation, so which end of the interval they fall on
depends on ``strand``.

The exon list is :class:`~gain.genomic_resources.gene_models.Exon` objects.
An exon's ``frame`` is the reading frame the effect-annotation engine needs,
and it is not populated by parsing alone: ``None`` means "not computed yet",
*not* "non-coding" (a non-coding exon has ``-1``). A resource whose source
format carries no frames holds ``None`` until
:meth:`~gain.genomic_resources.gene_models.TranscriptModel.update_frames`
fills them in. ``Exon``'s three coordinates are described in its class
documentation below rather than as separate entries.

API
---

.. currentmodule:: gain.genomic_resources.gene_models

.. autofunction:: build_gene_models_from_resource_id

.. autoclass:: GeneModels
   :members:

.. autoclass:: TranscriptModel
   :members:

.. autoclass:: Exon
   :members:
