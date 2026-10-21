
Liftover chains
===============

Liftover chains convert genomic coordinates from one reference genome assembly to another, for example from ``hg19`` to ``hg38``. A liftover chain resource contains a UCSC chain file describing how regions of the two assemblies align. GAIn provides the :class:`~gain.genomic_resources.liftover_chain.LiftoverChain` Python class for performing these conversions using ``pyliftover``.

Accessing a liftover chain
--------------------------

A ``LiftoverChain`` object can be built from a resource ID and a repository using :func:`~gain.genomic_resources.liftover_chain.build_liftover_chain_from_resource_id`:

.. code-block:: python

    import gain.grr as grr

    repo = grr.build_genomic_resource_repository()
    chain = grr.build_liftover_chain_from_resource_id(
        "liftover/hg19_to_hg38", repo
    )

If you already have a ``GenomicResource`` object, use :func:`~gain.genomic_resources.liftover_chain.build_liftover_chain_from_resource` instead:

.. code-block:: python

    resource = repo.get_resource("liftover/hg19_to_hg38")
    chain = grr.build_liftover_chain_from_resource(resource)

Both builder functions are available through ``gain.grr``. GAIn reuses the same ``LiftoverChain`` object when a resource is requested more than once, so the returned object may already be open.

Opening and closing a liftover chain
------------------------------------

A newly built ``LiftoverChain`` object must be opened before converting coordinates. Call :meth:`~gain.genomic_resources.liftover_chain.LiftoverChain.open` to load the chain file. The method returns the object itself, allowing ``.open()`` to be chained to the builder. Calling ``open()`` on an already-open chain has no effect. Use :meth:`~gain.genomic_resources.liftover_chain.LiftoverChain.is_open` to check its state.

Unlike a genomic score, a liftover chain is not a context manager and cannot be used in a ``with`` block. It has a :meth:`~gain.genomic_resources.liftover_chain.LiftoverChain.close` method for consistency with other resource objects, but this method does not release any resources. Because repeated builds can return the same object, its state may be shared by different parts of a program.

Converting genomic coordinates
------------------------------

:meth:`~gain.genomic_resources.liftover_chain.LiftoverChain.convert_coordinate` takes a chromosome name and a 1-based genomic position. It returns a tuple containing the chromosome and 1-based position in the target assembly, the target strand, and the chain score.


.. code-block:: python

    chain.open()
    print(chain.convert_coordinate("chr1", 1_000_000))
    print(chain.convert_coordinate("chr1", 5_000_000))
    chain.close()

.. code-block:: text

    ('chr1', 1064620, '+', 20851231461)
    ('chr1', 4939940, '+', 20851231461)


The results depend on the alignments in the chain file. If a position does not lie within any aligned block, ``convert_coordinate()`` returns ``None``. A successfully converted position may retain the same chromosome name or numerical coordinate, so ``None`` is the indicator of an unsuccessful conversion. If a position maps to more than one target location, the method returns the first mapping.

Chromosome naming conventions
-----------------------------

Reference assemblies may use different chromosome naming conventions, such as ``chr1`` and ``1``. The optional ``chrom_prefix:`` configuration in a liftover chain resource specifies how names are adjusted before and after conversion. The ``variant_coordinates`` subsection transforms the input chromosome name before it is passed to the chain, while ``target_coordinates`` transforms the chromosome name returned by the chain.

These transformations allow callers to use the contig names expected by their input and target genomes without manually renaming them for the chain file. The configuration options, including adding or removing prefixes, are described in :doc:`Genomic resources and repositories </grr>`.


API
---

The class and the builders below are defined in the module
``gain.genomic_resources.liftover_chain``. ``LiftoverChain`` is also
exported by ``gain.grr``, as ``grr.LiftoverChain``. The builders are also
exported by ``gain.grr``, as ``grr.build_liftover_chain_from_resource_id``
and ``grr.build_liftover_chain_from_resource``.

.. currentmodule:: gain.genomic_resources.liftover_chain

.. autosummary::

   build_liftover_chain_from_resource
   build_liftover_chain_from_resource_id
   LiftoverChain
   LiftoverChain.close
   LiftoverChain.convert_coordinate
   LiftoverChain.files
   LiftoverChain.get_schema
   LiftoverChain.is_open
   LiftoverChain.map_chromosome
   LiftoverChain.open

.. autofunction:: build_liftover_chain_from_resource
   :no-index:

.. autofunction:: build_liftover_chain_from_resource_id
   :no-index:

.. autoclass:: LiftoverChain
   :no-index:
   :members:
