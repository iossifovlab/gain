Reference genomes
=================

A :class:`~gain.genomic_resources.reference_genome.ReferenceGenome` wraps a
``genome`` resource — a FASTA, plain or bgzipped, plus its index — and
answers two kinds of question: what chromosomes are there and how long are
they, and what are the nucleotides in a given interval.

Opening and closing
-------------------

The genome holds an open FASTA handle, so it is built closed and must be
opened. :meth:`~gain.genomic_resources.reference_genome.ReferenceGenome.open`
returns the genome itself, so the call chains onto the builder:

.. code-block:: python

    from gain.genomic_resources.repository_factory import build_genomic_resource_repository
    from gain.genomic_resources.reference_genome import build_reference_genome_from_resource_id

    grr = build_genomic_resource_repository()
    genome = build_reference_genome_from_resource_id("hg38/genomes/GRCh38-hg38", grr).open()

In anything longer than a script, wrap it in a ``with`` block so the handle is
released on an exception too. Note that **entering the context manager does
not open the genome** — ``__enter__`` returns it unchanged, and what ``with``
adds is the guaranteed
:meth:`~gain.genomic_resources.reference_genome.ReferenceGenome.close`. Keep
the ``.open()``:

.. code-block:: python

    with build_reference_genome_from_resource_id("hg38/genomes/GRCh38-hg38", grr).open() as genome:
        print(genome.get_sequence("chr21", 5_030_000, 5_030_060))

Dropping the ``.open()`` fails less obviously here than it does for a score:
the chromosome accessors load the index lazily and so still answer, while
:meth:`~gain.genomic_resources.reference_genome.ReferenceGenome.get_sequence`
asserts.
:meth:`~gain.genomic_resources.reference_genome.ReferenceGenome.is_open`
reports the current state, which is worth checking in library code that may
be handed either a fresh or an already-opened genome.

Chromosomes
-----------

:attr:`~gain.genomic_resources.reference_genome.ReferenceGenome.chromosomes`
lists the contigs in the order the index gives them, and
:meth:`~gain.genomic_resources.reference_genome.ReferenceGenome.get_all_chrom_lengths`
returns the whole name-to-length mapping in one call. Both read the same
lazily-loaded index that
:meth:`~gain.genomic_resources.reference_genome.ReferenceGenome.get_chrom_length`
does, so neither is meaningfully faster than the other; the mapping is worth
knowing about because it is the live internal dictionary rather than a copy,
so treat it as read-only.

:attr:`~gain.genomic_resources.reference_genome.ReferenceGenome.chrom_prefix`
is the small piece of glue that makes a script portable between builds that
spell their contigs ``chr1`` and builds that spell them ``1``.

Sequence
--------

:meth:`~gain.genomic_resources.reference_genome.ReferenceGenome.get_sequence`
returns an interval as a string. Coordinates are 1-based and the interval is
closed at both ends, matching the convention the rest of GAIn and the
underlying formats use:

.. code-block:: python

    print(genome.get_sequence("chr21", 5_030_000, 5_030_060))

For intervals large enough that a single string is awkward,
:meth:`~gain.genomic_resources.reference_genome.ReferenceGenome.fetch`
yields the same nucleotides in buffered chunks instead.

:meth:`~gain.genomic_resources.reference_genome.ReferenceGenome.split_into_regions`
divides the genome into ``Region`` pieces of the requested size — the
standard way to fan a whole-genome computation out over a task graph or a
process pool. The last region of each chromosome is open-ended
(``Region(chrom, start, None)``) rather than padded to the full size, so do
not assume every yielded region has an end:

.. code-block:: python

    for region in genome.split_into_regions(10_000_000, chromosome="chr21"):
        print(region)

Curator-facing detail — how a ``genome`` resource's ``genomic_resource.yaml``
names its FASTA and its index, and what ``PARS`` configuration
:meth:`~gain.genomic_resources.reference_genome.ReferenceGenome.is_pseudoautosomal`
reads — is on :doc:`/grr`.

API
---

.. currentmodule:: gain.genomic_resources.reference_genome

.. autofunction:: build_reference_genome_from_resource_id

.. autoclass:: ReferenceGenome
   :members:
