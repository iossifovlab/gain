
Gene set collections
====================

Gene set collections organize related groups of genes. Each gene set has a name, a description, and a list of gene symbols. For example, the Gene Ontology (GO) collection contains gene sets associated with biological processes, molecular functions, and cellular components. GAIn provides the :class:`~gain.gene_sets.gene_set.GeneSetCollection` Python class for accessing these resources. Individual gene sets are represented by :class:`~gain.gene_sets.gene_set.GeneSet` objects.

Accessing a gene set collection
-------------------------------

A ``GeneSetCollection`` object can be built from a resource ID and a repository using :func:`~gain.gene_sets.gene_set.build_gene_set_collection_from_resource_id`:

.. code-block:: python

    import gain.grr as grr

    repo = grr.build_genomic_resource_repository()
    collection = grr.build_gene_set_collection_from_resource_id(
        "gene_properties/gene_sets/GO_2025-07-22_release", repo
    )

If you already have a ``GenomicResource`` object, use :func:`~gain.gene_sets.gene_set.build_gene_set_collection_from_resource` instead:

.. code-block:: python

    resource = repo.get_resource(
        "gene_properties/gene_sets/GO_2025-07-22_release"
    )
    collection = grr.build_gene_set_collection_from_resource(resource)

Both builder functions are available through ``gain.grr``.

Loading a gene set collection
-----------------------------

A newly built collection must be loaded before its gene sets can be accessed. Call :meth:`~gain.gene_sets.gene_set.GeneSetCollection.load` to read the gene sets from the resource into memory:

.. code-block:: python

    collection.load()

The ``load()`` method returns the collection itself, so it can also be chained to a builder function. GAIn reuses the same ``GeneSetCollection`` object when the same resource is requested again, and that object may already be loaded. Calling ``load()`` on an already-loaded collection has no effect. Use :meth:`~gain.gene_sets.gene_set.GeneSetCollection.is_loaded` to check its state. The collection does not maintain an open file handle and has no ``close()`` method.

Retrieving gene sets
--------------------

:meth:`~gain.gene_sets.gene_set.GeneSetCollection.get_all_gene_sets` returns a list of all gene sets in the collection. :meth:`~gain.gene_sets.gene_set.GeneSetCollection.get_gene_set` retrieves one gene set by name and returns ``None`` if the name is not found.

A ``GeneSet`` object has four attributes: ``name``, ``desc`` (description), ``syms`` (gene symbols), and ``count`` (number of gene symbols).

For example, the following code retrieves the number of gene sets in the GO collection and examines one of them:

.. code-block:: python

    gene_sets = collection.get_all_gene_sets()
    print("Number of gene sets:", len(gene_sets))

    selected = collection.get_gene_set(
        "GO:0000009-alpha-1,6-mannosyltransferase_activity"
    )
    print(selected.name, selected.count)
    print(selected.syms)

.. code-block:: text

    Number of gene sets: 21608
    GO:0000009-alpha-1,6-mannosyltransferase_activity 3
    ['ALG12', 'ALG2', 'PIGV']

The collection also has a ``collection_id``, taken from the resource's ``id:`` setting. Its ``web_label`` and ``web_format_str`` attributes provide formatting information used when displaying gene sets in a web interface.

Resource formats
----------------

The ``format:`` setting in the resource definition determines how gene sets are stored. GAIn supports three formats:

* ``directory`` stores each gene set in a separate file within a directory.
* ``gmt`` stores the gene sets in a GMT file.
* ``map`` stores mappings from genes to gene sets.

All three formats are loaded into the same ``GeneSetCollection`` and ``GeneSet`` Python objects. The resource configuration options are described in :doc:`Genomic resources and repositories </grr>`.

Gene set statistics
-------------------

A statistics build stores two histograms for a gene set collection. One describes the number of genes in each gene set. The other describes the number of gene sets containing each gene.

The methods :meth:`~gain.gene_sets.gene_set.GeneSetCollection.get_genes_per_gene_set_hist` and :meth:`~gain.gene_sets.gene_set.GeneSetCollection.get_gene_sets_per_gene_hist` retrieve these histograms:

.. code-block:: python

    genes_per_set = collection.get_genes_per_gene_set_hist()
    sets_per_gene = collection.get_gene_sets_per_gene_hist()

Each method returns ``None`` if the corresponding histogram is not stored in the resource. Histogram objects and their contents are explained in :doc:`Histograms <histograms>`.


API
---

The classes below are defined in the module ``gain.gene_sets.gene_set``.
``GeneSetCollection`` is also exported by ``gain.grr``, as
``grr.GeneSetCollection``. The builders
``build_gene_set_collection_from_resource_id`` and
``build_gene_set_collection_from_resource`` are defined in the same module.
They are also exported by ``gain.grr``, as
``grr.build_gene_set_collection_from_resource_id`` and
``grr.build_gene_set_collection_from_resource``.

.. currentmodule:: gain.gene_sets.gene_set

.. autosummary::

   build_gene_set_collection_from_resource
   build_gene_set_collection_from_resource_id
   GeneSetCollection
   GeneSetCollection.files
   GeneSetCollection.get_all_gene_sets
   GeneSetCollection.get_gene_collection_count_statistics
   GeneSetCollection.get_gene_set
   GeneSetCollection.get_gene_sets_list_statistics
   GeneSetCollection.get_gene_sets_per_gene_hist
   GeneSetCollection.get_genes_per_gene_set_hist
   GeneSetCollection.is_loaded
   GeneSetCollection.load
   GeneSet

.. autofunction:: build_gene_set_collection_from_resource
   :no-index:

.. autofunction:: build_gene_set_collection_from_resource_id
   :no-index:

.. autoclass:: GeneSetCollection
   :no-index:
   :members:

.. autoclass:: GeneSet
   :no-index:
   :members:
