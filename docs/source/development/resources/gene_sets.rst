Gene set collections
====================

A gene set collection is a named group of gene sets. Each gene set has a
name, a description and a list of gene symbols, for example the candidate
genes of a disorder. The class that reads a resource is
:class:`~gain.gene_sets.gene_set.GeneSetCollection`, and each of its sets is
a :class:`~gain.gene_sets.gene_set.GeneSet`.

Build a collection with the builder from ``gain.grr``, and call ``load``
before the first read:

.. code-block:: python

    from gain.grr import (
        build_gene_set_collection_from_resource_id,
        build_genomic_resource_repository,
    )

    grr = build_genomic_resource_repository()
    collection = build_gene_set_collection_from_resource_id(
        "gene_properties/gene_sets/main", grr).load()

    for gene_set in collection.get_all_gene_sets():
        print(gene_set.name, gene_set.count, gene_set.desc)
    print(collection.get_gene_set("epilepsy").syms)

For a collection with two gene sets, the example prints:

.. code-block:: text

    autism_candidates 3 Autism candidates
    epilepsy 2 Epilepsy genes
    ['SCN2A', 'CHD8']

Loading
-------

The builder memoizes collections for each resource. A second build of the
same resource returns the same collection, and that collection may already be
loaded. Call
:meth:`~gain.gene_sets.gene_set.GeneSetCollection.load` before the first read.
It reads the gene sets from the resource and returns the collection, so the
call chains. A call to ``load`` on a loaded collection does nothing.
:meth:`~gain.gene_sets.gene_set.GeneSetCollection.is_loaded` reports the
state. A collection holds no file handle, so it has no ``close``.

Reading gene sets
-----------------

:meth:`~gain.gene_sets.gene_set.GeneSetCollection.get_gene_set` returns one
gene set by its name, or ``None`` when the name is unknown.
:meth:`~gain.gene_sets.gene_set.GeneSetCollection.get_all_gene_sets` returns
all of them as a list.

A :class:`~gain.gene_sets.gene_set.GeneSet` has four attributes: ``name``,
``desc``, ``syms`` (the gene symbols) and ``count`` (the number of symbols).

The collection also carries its ``collection_id``, taken from the ``id:`` key
of the resource, and the ``web_label`` and ``web_format_str`` that a web
interface uses to show the sets.

Resource formats
----------------

The ``format:`` key of the resource chooses how the sets are stored: one file
for each set in a directory (``directory``), a GMT file (``gmt``), or a
mapping file from gene to set (``map``). The three formats give the same
objects. The keys of each format are described on :doc:`/grr`.

Statistics
----------

A statistics build stores two histograms for a collection: the number of
genes in each gene set, and the number of gene sets that contain each gene.
:meth:`~gain.gene_sets.gene_set.GeneSetCollection.get_genes_per_gene_set_hist`
and
:meth:`~gain.gene_sets.gene_set.GeneSetCollection.get_gene_sets_per_gene_hist`
read them, and each returns ``None`` when the resource has no stored
histogram. :doc:`histograms` describes the result.

API
---

.. currentmodule:: gain.gene_sets.gene_set

.. autofunction:: build_gene_set_collection_from_resource_id

.. autoclass:: GeneSetCollection
   :members:

.. autoclass:: GeneSet
   :members:
