Liftover chains
===============

A liftover chain converts a coordinate from one genome assembly to another,
for example from ``hg19`` to ``hg38``. The resource holds a UCSC chain file.
The class that reads it is
:class:`~gain.genomic_resources.liftover_chain.LiftoverChain`, a wrapper
around a ``pyliftover`` object.

Build a chain with the builder from ``gain.grr``, and call ``open`` before
the first conversion:

.. code-block:: python

    from gain.grr import (
        build_genomic_resource_repository,
        build_liftover_chain_from_resource_id,
    )

    grr = build_genomic_resource_repository()
    chain = build_liftover_chain_from_resource_id(
        "liftover/hg19_to_hg38", grr).open()

    print(chain.convert_coordinate("chr1", 5))
    print(chain.convert_coordinate("chr1", 500))
    chain.close()

For a chain that moves ``chr1`` up by ten bases and covers 100 bases, the
two conversions print:

.. code-block:: text

    ('chr1', 15, '+', 1000)
    None

The lifecycle
-------------

The builder memoizes chains for each resource. A second build of the same
resource returns the same chain, and that chain may already be open. Call
:meth:`~gain.genomic_resources.liftover_chain.LiftoverChain.open` before the
first conversion. It reads the chain file and returns the chain, so the call
chains. A call to ``open`` on an open chain does nothing.
:meth:`~gain.genomic_resources.liftover_chain.LiftoverChain.is_open`
reports the state.

Unlike a genomic score, a chain is not a context manager, so there is no
``with`` form. :meth:`~gain.genomic_resources.liftover_chain.LiftoverChain.close`
exists for symmetry with the other resource objects and releases nothing.
Because builds share the chain, treat it as shared state.

Converting a coordinate
-----------------------

:meth:`~gain.genomic_resources.liftover_chain.LiftoverChain.convert_coordinate`
takes a chromosome and a **1-based** position. It returns a tuple of the
target chromosome, the target 1-based position, the strand of the target and
the chain score. It returns ``None`` when the position is in no aligned block
of the chain, so a failed lift is distinguishable from a lift to the same
place. When the chain maps the position to more than one target, the method
returns the first one.

Contig names
------------

The two assemblies often name their contigs in different ways, for example
``chr1`` and ``1``. The ``chrom_prefix:`` block of the resource renames the
contig before the chain sees it (``variant_coordinates``) and after the
chain answers (``target_coordinates``). The keys of the block are described
on :doc:`/grr`. The conversion applies both renames, so the caller works only
with its own contig names.

API
---

.. currentmodule:: gain.genomic_resources.liftover_chain

.. autofunction:: build_liftover_chain_from_resource_id

.. autoclass:: LiftoverChain
   :members:
