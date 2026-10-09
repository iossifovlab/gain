Data frames and AnnData
=======================

Two resource types hold plain tables rather than genomic data with a type of
its own. A ``data_frame`` resource holds one table. An ``ann_data`` resource
holds an annotated data matrix, typically single-cell expression data. Each
has a loader that returns the object of a well-known library, so GAIn adds no
class of its own on top.

The loaders differ from the builders of the other resource types in three
ways:

* They are named ``load_*``, not ``build_*``, because they read the data at
  once.
* They return a pandas or an anndata object, which ``gain.grr`` does not
  re-export. Import ``pandas`` or ``anndata`` yourself if you need the types.
* They hold nothing open for the lifecycle of GAIn: there is no ``open``,
  ``close`` or ``load`` to call. The one exception is an AnnData backed by a
  file, described below.

Data frames
-----------

:func:`~gain.genomic_resources.data_frame_resource.load_data_frame_from_resource_id`
returns a pandas ``DataFrame``:

.. code-block:: python

    from gain.grr import (
        build_genomic_resource_repository,
        load_data_frame_from_resource_id,
    )

    grr = build_genomic_resource_repository()
    frame = load_data_frame_from_resource_id("tables/gene_annotations", grr)

    print(type(frame).__name__, frame.shape)
    print(frame)

For a resource with three rows, the example prints:

.. code-block:: text

    DataFrame (3, 3)
      gene  score  label
    0   G1    0.1  alpha
    1   G2    0.2   beta
    2   G3    0.3  gamma

The ``format:`` key of the resource selects the reader: ``csv`` (the
default), ``tsv`` or ``excel``. The ``parameters:`` block passes keyword
arguments to the pandas reader (``read_csv`` or ``read_excel``). A ``tsv``
resource uses a tab as the separator unless ``parameters`` names its own.
A ``.gz`` file name selects gzip compression. The keys of the resource are
described on :doc:`/grr`.

The loader promises a single ``DataFrame``. A ``parameters`` block that makes
pandas return something else, such as ``chunksize`` or ``sheet_name: null``,
raises ``ValueError`` naming the resource.

AnnData
-------

:func:`~gain.genomic_resources.ann_data_resource.load_ann_data_from_resource_id`
returns an ``AnnData`` object:

.. code-block:: python

    from gain.grr import (
        build_genomic_resource_repository,
        load_ann_data_from_resource_id,
    )

    grr = build_genomic_resource_repository()
    ann_data = load_ann_data_from_resource_id("single_cell/pbmc_demo", grr)

    print(type(ann_data).__name__, ann_data.shape)
    print(ann_data.obs)
    print(ann_data.var)
    if ann_data.isbacked:
        ann_data.file.close()

For a resource with three cells and four genes, the example prints:

.. code-block:: text

    AnnData (3, 4)
           cell_type  n_genes
    cell                     
    CELL_1    neuron      120
    CELL_2      glia      340
    CELL_3    neuron      250
            gene_name  highly_variable
    gene                              
    ENSG001      ACTB             True
    ENSG002     GAPDH            False
    ENSG003    MALAT1             True
    ENSG004      XIST            False

The rows of ``obs`` are the cells, and the rows of ``var`` are the genes.

The data files must be reachable as local files. This holds for directory
GRRs and for caching protocols. Any other protocol raises ``ValueError``
that names the resource.

The ``format:`` key of the resource selects the reader. When the key is
missing, the suffix of the ``file:`` key decides:

.. list-table::
   :header-rows: 1
   :widths: 20 35 45

   * - Format
     - File
     - Notes
   * - ``h5ad``
     - ``*.h5ad``
     - The default when no suffix matches.
   * - ``10x_mtx``
     - ``matrix.mtx`` or ``matrix.mtx.gz``
     - A 10x matrix with its ``barcodes`` and ``features`` (or ``genes``)
       files, found next to the matrix file.
   * - ``10x_h5``
     - ``*.h5``
     - A 10x HDF5 matrix.

**The caller owns the file handle.** An ``h5ad`` file is opened read-only and
backed by the file, so a large matrix stays out of memory. The AnnData keeps
the file open until you close it. Call ``ann_data.file.close()`` when
``ann_data.isbacked`` is true, as in the example, in particular when you load
many resources in a loop. The two 10x formats are read into memory and hold
no handle.

The ``matrix_free`` keyword of
:func:`~gain.genomic_resources.ann_data_resource.load_ann_data_from_resource`
is for tools that read only ``obs`` and ``var``. It applies to the
``10x_mtx`` and ``10x_h5`` formats only. The ``h5ad`` format ignores it,
because the file already backs ``X``. **For a 10x resource, the ``X`` it
returns is a matrix of zeros, not the data of the resource.** Do not use it
to read values.

API
---

.. currentmodule:: gain.genomic_resources.data_frame_resource

.. autofunction:: load_data_frame_from_resource_id

.. autofunction:: load_data_frame_from_resource

.. currentmodule:: gain.genomic_resources.ann_data_resource

.. autofunction:: load_ann_data_from_resource_id

.. autofunction:: load_ann_data_from_resource
