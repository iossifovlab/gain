
Reference genomes
=================

.. note::

   Draft for review: this page is a revision of :doc:`reference_genomes`. Both are
   published while the revision is discussed; one replaces the other.

Genome resources provide the DNA sequences of reference genome assemblies, together with the names and lengths of their chromosomes and other contigs. In a GRR, a genome is a resource of type ``genome`` backed by a FASTA file (plain or bgzipped) and its index files. GAIn provides the :class:`~gain.genomic_resources.reference_genome.ReferenceGenome` Python object for accessing this information. Unlike a generic ``GenomicResource``, a ``ReferenceGenome`` provides methods for retrieving sequences and working with genomic coordinates.

Accessing a genome
------------------

A ``ReferenceGenome`` object can be built from a resource ID and a repository using :func:`~gain.genomic_resources.reference_genome.build_reference_genome_from_resource_id`:

.. code-block:: python

    import gain.grr as grr

    repo = grr.build_genomic_resource_repository()
    genome = grr.build_reference_genome_from_resource_id(
        "hg38/genomes/GRCh38-hg38", repo
    )

If you already have a ``GenomicResource`` object, you can use :func:`~gain.genomic_resources.reference_genome.build_reference_genome_from_resource` instead:

.. code-block:: python

    resource = repo.get_resource("hg38/genomes/GRCh38-hg38")
    genome = grr.build_reference_genome_from_resource(resource)

Both builder functions are available through ``gain.grr``.

Opening and closing a genome
----------------------------

A ``ReferenceGenome`` is initially closed. You must call :meth:`~gain.genomic_resources.reference_genome.ReferenceGenome.open` before retrieving DNA sequences, because the object uses an open FASTA file handle. The ``open()`` method returns the genome object itself, so it can be chained to the builder:

.. code-block:: python

    genome = grr.build_reference_genome_from_resource_id(
        "hg38/genomes/GRCh38-hg38", repo
    ).open()

For scripts and functions, using a ``with`` block ensures that the file handle is released when you finish, even if an error occurs:

.. code-block:: python

    with grr.build_reference_genome_from_resource_id(
        "hg38/genomes/GRCh38-hg38", repo
    ).open() as genome:
        print(genome.get_sequence("chr21", 5_030_000, 5_030_060))

The ``with`` statement closes the genome when the block ends, but it does not open it automatically. The ``.open()`` call is therefore necessary. Chromosome names and lengths remain accessible even when the genome is closed, but :meth:`~gain.genomic_resources.reference_genome.ReferenceGenome.get_sequence` requires the genome to be open.

You can close an opened genome explicitly with :meth:`~gain.genomic_resources.reference_genome.ReferenceGenome.close` and check its state using :meth:`~gain.genomic_resources.reference_genome.ReferenceGenome.is_open`.

Chromosome names and lengths
----------------------------

The :attr:`~gain.genomic_resources.reference_genome.ReferenceGenome.chromosomes` attribute lists the contigs in the order recorded in the FASTA index. To obtain the length of a particular chromosome, use :meth:`~gain.genomic_resources.reference_genome.ReferenceGenome.get_chrom_length`. To retrieve the complete mapping of contig names to lengths, use :meth:`~gain.genomic_resources.reference_genome.ReferenceGenome.get_all_chrom_lengths`:

.. code-block:: python

    print(genome.chromosomes)
    print(genome.get_chrom_length("chr21"))
    print(genome.get_all_chrom_lengths())

Retrieving the complete mapping does not require reading the genome sequence. The mapping returned by ``get_all_chrom_lengths()`` is the object's internal dictionary rather than a copy, so it should not be modified.

Different genome assemblies may use chromosome names such as ``chr1`` or ``1``. The :attr:`~gain.genomic_resources.reference_genome.ReferenceGenome.chrom_prefix` attribute reports the naming prefix determined from the FASTA index, allowing scripts to accommodate these differences.

For assemblies with defined pseudoautosomal regions, :meth:`~gain.genomic_resources.reference_genome.ReferenceGenome.is_pseudoautosomal` uses the ``PARS`` information in the resource definition. The configuration of genome resources, including their FASTA files, indexes, and pseudoautosomal regions, is described in :doc:`Genomic resources and repositories </grr>`.

Retrieving DNA sequences
------------------------

:meth:`~gain.genomic_resources.reference_genome.ReferenceGenome.get_sequence` retrieves the nucleotides in a specified interval as a string. GAIn uses 1-based coordinates with both endpoints included:

.. code-block:: python

    print(genome.get_sequence("chr21", 5_030_000, 5_030_060))

For large intervals, :meth:`~gain.genomic_resources.reference_genome.ReferenceGenome.fetch` provides the sequence in buffered chunks rather than returning one large string. This can be useful when processing long genomic intervals without holding the entire sequence in memory.

Dividing a genome into regions
------------------------------

:meth:`~gain.genomic_resources.reference_genome.ReferenceGenome.split_into_regions` divides a genome into ``Region`` objects of a requested size. This is useful when distributing a whole-genome analysis across multiple tasks or processes. You can also restrict the division to a particular chromosome:

.. code-block:: python

    for region in genome.split_into_regions(10_000_000, chromosome="chr21"):
        print(region)

The last region of each chromosome is represented as ``Region(chrom, start, None)``, where None indicates that the region extends to the end of the chromosome. Code processing these regions should therefore not assume that every region has an explicit ``end`` coordinate.





API
---

The class below is defined in the module
``gain.genomic_resources.reference_genome``, and so are the builders
``build_reference_genome_from_resource_id`` and
``build_reference_genome_from_resource``. ``ReferenceGenome`` is also
exported by ``gain.grr``, as ``grr.ReferenceGenome``. The builders are also
exported by ``gain.grr``, as ``grr.build_reference_genome_from_resource_id``
and ``grr.build_reference_genome_from_resource``.

.. currentmodule:: gain.genomic_resources.reference_genome

.. autosummary::

   build_reference_genome_from_resource
   build_reference_genome_from_resource_id
   ReferenceGenome
   ReferenceGenome.chrom_prefix
   ReferenceGenome.chromosomes
   ReferenceGenome.close
   ReferenceGenome.fetch
   ReferenceGenome.get_all_chrom_lengths
   ReferenceGenome.get_chrom_length
   ReferenceGenome.get_schema
   ReferenceGenome.get_sequence
   ReferenceGenome.is_open
   ReferenceGenome.is_pseudoautosomal
   ReferenceGenome.open
   ReferenceGenome.resource_id
   ReferenceGenome.split_into_regions

.. autofunction:: build_reference_genome_from_resource
   :no-index:

.. autofunction:: build_reference_genome_from_resource_id
   :no-index:

.. autoclass:: ReferenceGenome
   :no-index:
   :members:
