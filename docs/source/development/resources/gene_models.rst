
Gene models
===========

Gene models resources describe genes, their transcripts, and the exons that make up each transcript. GAIn provides the :class:`~gain.genomic_resources.gene_models.GeneModels` Python object for accessing these annotations. It reads the resource's data and represents individual transcripts as :class:`~gain.genomic_resources.gene_models.TranscriptModel` objects, making it possible to find transcripts by gene name or genomic location and examine their coding regions, untranslated regions, and exons.

Gene models can be stored in several formats, including GTF, refFlat, refSeq, and CCDS. GAIn interprets these formats using the ``format`` setting in the resource definition, as described in :doc:`Genomic resources and repositories </grr>`.

Accessing gene models
---------------------

A ``GeneModels`` object can be built from a resource ID and a repository using :func:`~gain.genomic_resources.gene_models.build_gene_models_from_resource_id`:

.. code-block:: python

    import gain.grr as grr

    repo = grr.build_genomic_resource_repository()
    genes = grr.build_gene_models_from_resource_id(
        "hg38/gene_models/MANE/1.5", repo
    )

If you already have a ``GenomicResource`` object, you can use :func:`~gain.genomic_resources.gene_models.build_gene_models_from_resource` instead:

.. code-block:: python

    resource = repo.get_resource("hg38/gene_models/MANE/1.5")
    genes = grr.build_gene_models_from_resource(resource)

Both builder functions are available through ``gain.grr``. Calls that resolve to the same resource return the same ``GeneModels`` object, which may already have been loaded by another part of the program.

Loading gene models
-------------------

Unlike a reference genome, which retrieves sequences from an open FASTA file, a ``GeneModels`` object loads its annotation data into memory. Call :meth:`~gain.genomic_resources.gene_models.GeneModels.load` before querying genes or transcripts:

.. code-block:: python

    genes = grr.build_gene_models_from_resource_id(
        "hg38/gene_models/MANE/1.5", repo
    ).load()

The ``load()`` method returns the ``GeneModels`` object itself, allowing it to be chained to the builder as shown above. Use :meth:`~gain.genomic_resources.gene_models.GeneModels.is_loaded` to check whether an object has already been loaded, particularly in code that may receive either a newly built or an already-loaded object.

Because the annotation is kept in memory rather than accessed through an open file handle, :meth:`~gain.genomic_resources.gene_models.GeneModels.close` does nothing. Gene models do not require the ``open()`` and ``close()`` operations used for some other resource types.

Finding transcripts
-------------------

Once gene models are loaded, you can look up transcripts by gene name or genomic location.

:meth:`~gain.genomic_resources.gene_models.GeneModels.gene_models_by_gene_name` returns the transcripts associated with a gene symbol. If the gene name is not found, it returns ``None`` rather than an empty list. This distinguishes an unknown gene from one that has no transcripts.

:meth:`~gain.genomic_resources.gene_models.GeneModels.gene_models_by_location` returns a list of transcripts overlapping a specified genomic position or interval.

.. code-block:: python

    transcripts = genes.gene_models_by_gene_name("TP53")
    for tm in transcripts or []:
        print(tm.tr_id, tm.chrom, tm.tx, tm.is_coding())

    overlapping = genes.gene_models_by_location(
        "chr17", 7_676_000, 7_690_000
    )

The first lookup finds transcripts of ``TP53`` and prints each transcript's identifier, chromosome, transcribed interval, and coding status. The expression ``transcripts or []`` allows the loop to run safely even if the gene name is not found. The second lookup finds transcripts overlapping the specified interval on chromosome 17.

Working with transcripts and exons
----------------------------------

A :class:`~gain.genomic_resources.gene_models.TranscriptModel` represents an individual transcript. Its attributes include ``chrom`` (chromosome), ``strand`` (transcription orientation), ``tx`` (transcribed interval), ``cds`` (coding interval), and ``exons`` (the transcript's exons). Its methods can identify the coding and untranslated portions of a transcript and calculate their lengths.

For example, after finding the transcripts of a gene, you can inspect the coding regions of one transcript:

.. code-block:: python

    tm = transcripts[0]
    if tm.is_coding():
        for region in tm.cds_regions():
            print(region.chrom, region.start, region.stop)
        print("5' UTR:", tm.utr5_regions())
        print("coding length:", tm.cds_len())

The methods :meth:`~gain.genomic_resources.gene_models.TranscriptModel.cds_regions`, :meth:`~gain.genomic_resources.gene_models.TranscriptModel.utr5_regions`, :meth:`~gain.genomic_resources.gene_models.TranscriptModel.utr3_regions`, and :meth:`~gain.genomic_resources.gene_models.TranscriptModel.all_regions` each return a list of ``BedRegion`` objects. The 5' and 3' untranslated regions are defined relative to the direction of transcription, so their positions along the chromosome depend on the transcript's ``strand``.

The ``exons`` attribute contains :class:`~gain.genomic_resources.gene_models.Exon` objects. Each exon has a ``frame`` value used by the effect-annotation machinery to determine its coding reading frame. An exon with ``frame = None`` has not had its frame computed, while ``frame = -1`` identifies a non-coding exon. These values should not be confused. If the source annotation does not provide reading frames, they remain ``None`` until :meth:`~gain.genomic_resources.gene_models.TranscriptModel.update_frames` calculates them. The ``Exon`` class documentation in the API reference describes its three attributes, ``start``, ``stop`` and ``frame``.

Combining and constructing gene models
--------------------------------------

:meth:`~gain.genomic_resources.gene_models.GeneModels.join_gene_models` combines several loaded ``GeneModels`` objects into one. For example, a primary gene annotation and a supplementary annotation can be merged so that their transcripts can be queried together. The resulting object is already loaded and can be used wherever a loaded ``GeneModels`` object is expected, including by effect annotators. Each input must be loaded before it can be joined. An unloaded object is rejected rather than treated as an empty annotation.

:meth:`~gain.genomic_resources.gene_models.GeneModels.from_transcript_models` constructs a loaded, indexed ``GeneModels`` object from a resource and a dictionary of transcript models (``dict[str, TranscriptModel]``). This is useful when transcript annotations have been assembled or modified programmatically and need to be used through the same interface as gene models loaded from a GRR.


API
---

The classes below are defined in the module
``gain.genomic_resources.gene_models``, and so are the builders
``build_gene_models_from_resource_id`` and
``build_gene_models_from_resource``. ``GeneModels`` is also exported by
``gain.grr``, as ``grr.GeneModels``. The builders are also exported by
``gain.grr``, as ``grr.build_gene_models_from_resource_id`` and
``grr.build_gene_models_from_resource``.

.. currentmodule:: gain.genomic_resources.gene_models

.. autosummary::

   build_gene_models_from_resource
   build_gene_models_from_resource_id
   GeneModels
   GeneModels.close
   GeneModels.from_transcript_models
   GeneModels.gene_models_by_gene_name
   GeneModels.gene_models_by_location
   GeneModels.gene_names
   GeneModels.get_schema
   GeneModels.has_chromosome
   GeneModels.is_loaded
   GeneModels.join_gene_models
   GeneModels.load
   GeneModels.resource_id
   TranscriptModel
   TranscriptModel.all_regions
   TranscriptModel.calc_frames
   TranscriptModel.cds_len
   TranscriptModel.cds_regions
   TranscriptModel.is_coding
   TranscriptModel.test_frames
   TranscriptModel.total_len
   TranscriptModel.update_frames
   TranscriptModel.utr3_len
   TranscriptModel.utr3_regions
   TranscriptModel.utr5_len
   TranscriptModel.utr5_regions
   Exon
   Exon.contains

.. autofunction:: build_gene_models_from_resource

.. autofunction:: build_gene_models_from_resource_id

.. autoclass:: GeneModels
   :members:

.. autoclass:: TranscriptModel
   :members:

.. autoclass:: Exon
   :members:
