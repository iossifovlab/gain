
Data frames and AnnData
=======================

GAIn supports two resource types for working with tabular and matrix-based data. A ``data_frame`` resource contains a table, while an ``ann_data`` resource contains an annotated data matrix, commonly used for single-cell gene expression data.

Unlike other genomic resource types, these resources do not have dedicated GAIn Python classes. Instead, GAIn provides loader functions that return objects from the pandas and anndata libraries. The loaders read the data directly, without requiring a separate ``open()`` or ``load()`` call.

Loading a data frame
--------------------

A data frame can be loaded from a resource ID and a repository using :func:`~gain.genomic_resources.data_frame_resource.load_data_frame_from_resource_id`:

.. code-block:: python

    import gain.grr as grr

    repo = grr.build_genomic_resource_repository()
    frame = grr.load_data_frame_from_resource_id(
        "DATA_FRAME_RESOURCE_ID", repo
    )

If you already have a ``GenomicResource`` object, use :func:`~gain.genomic_resources.data_frame_resource.load_data_frame_from_resource` instead:

.. code-block:: python

    resource = repo.get_resource("DATA_FRAME_RESOURCE_ID")
    frame = grr.load_data_frame_from_resource(resource)

Both functions are available through ``gain.grr`` and return a pandas ``DataFrame``. The resulting table can be accessed using standard pandas operations:

.. code-block:: python

    print(frame.shape)
    print(frame.head())

The ``format:`` setting in the resource definition determines how the table is read. GAIn supports ``csv`` (the default), ``tsv``, and ``excel`` formats. The ``parameters:`` block can pass additional arguments to the corresponding pandas reader, such as ``read_csv()`` or ``read_excel()``.

For TSV files, a tab separator is used unless another separator is specified. Files ending in ``.gz`` are read using gzip compression.

The loader must return a single pandas ``DataFrame``. If the supplied parameters cause pandas to return another type, such as when using ``chunksize`` or ``sheet_name: null``, the loader raises a ``ValueError`` identifying the resource.

Loading an AnnData object
-------------------------

An ``ann_data`` resource contains an annotated data matrix, typically representing cells and their measured molecular features. GAIn uses the anndata library to read these resources.

An AnnData object can be loaded using :func:`~gain.genomic_resources.ann_data_resource.load_ann_data_from_resource_id`:

.. code-block:: python

    ann_data = grr.load_ann_data_from_resource_id(
        "ANN_DATA_RESOURCE_ID", repo
    )

Alternatively, an existing ``GenomicResource`` object can be passed to :func:`~gain.genomic_resources.ann_data_resource.load_ann_data_from_resource`:

.. code-block:: python

    resource = repo.get_resource("ANN_DATA_RESOURCE_ID")
    ann_data = grr.load_ann_data_from_resource(resource)

Both functions return an ``AnnData`` object. Its main components include ``X``, the data matrix, ``obs``, annotations for observations such as cells, and ``var``, annotations for variables such as genes.

For example:

.. code-block:: python

    print(ann_data.shape)
    print(ann_data.obs.head())
    print(ann_data.var.head())

For illustration, an AnnData object containing three cells and four genes
might produce the following output:

.. code-block:: text

    (3, 4)
           cell_type  n_genes
    cell
    CELL_1    neuron        3
    CELL_2      glia        2
    CELL_3    neuron        4

            gene_name  highly_variable
    gene
    ENSG001      ACTB             True
    ENSG002     GAPDH            False
    ENSG003    MALAT1             True
    ENSG004      XIST            False

The rows of ``obs`` represent cells, while the rows of ``var``
represent genes. The ``shape`` gives the number of cells and genes
in the matrix.


AnnData resources must be accessible through local files. Directory-based GRRs and supported caching protocols provide this access. Other storage protocols raise a ``ValueError`` identifying the resource.

AnnData file formats
--------------------

The ``format:`` setting in the resource definition determines how AnnData is loaded. If no format is specified, GAIn determines the format from the file name.

.. list-table::
   :header-rows: 1
   :widths: 20 35 45

   * - Format
     - File
     - Description
   * - ``h5ad``
     - ``*.h5ad``
     - An AnnData HDF5 file. This is also the default if no recognized file suffix is found.
   * - ``10x_mtx``
     - ``matrix.mtx`` or ``matrix.mtx.gz``
     - A 10x matrix with accompanying ``barcodes`` and ``features`` (or ``genes``) files in the same directory.
   * - ``10x_h5``
     - ``*.h5``
     - A 10x HDF5 matrix.

The resource configuration options are described in :doc:`Genomic resources and repositories </grr>`.

Matrix-free loading
-------------------

For some analyses, only the cell and gene annotations are needed, without the expression matrix itself. The ``matrix_free`` argument of :func:`~gain.genomic_resources.ann_data_resource.load_ann_data_from_resource` supports this use case for ``10x_mtx`` and ``10x_h5`` resources.

When ``matrix_free=True``, the returned AnnData object contains the annotations but its ``X`` matrix is filled with zeros rather than the original expression values. This option is therefore appropriate for inspecting ``obs`` and ``var``, but not for analyzing the expression matrix.

For ``h5ad`` resources, ``matrix_free`` is ignored because the matrix is already accessed through a backed file.

Backed AnnData objects
----------------------

An AnnData object loaded from an h5ad resource retains an open file handle. GAIn loads these files in read-only backed mode, allowing the expression matrix to remain on disk rather than being loaded entirely into memory.

The ``isbacked`` attribute indicates whether an AnnData object is backed by a file. When it is, the caller is responsible for closing the file after use:

.. code-block:: python

    if ann_data.isbacked:
        ann_data.file.close()

This is particularly important when loading multiple AnnData resources in a loop, since otherwise their file handles may remain open.

The ``10x_mtx`` and ``10x_h5`` formats are loaded into memory and do not retain open file handles. Similarly, data frames are read directly into memory and require no explicit closing.

API
---

The functions below are defined in the modules
``gain.genomic_resources.data_frame_resource`` and
``gain.genomic_resources.ann_data_resource``. ``gain.grr`` exports all four,
as ``grr.load_data_frame_from_resource``,
``grr.load_data_frame_from_resource_id``,
``grr.load_ann_data_from_resource`` and
``grr.load_ann_data_from_resource_id``. The page has no class: the loaders
return the objects of pandas and anndata.

Data frame loaders
~~~~~~~~~~~~~~~~~~

.. currentmodule:: gain.genomic_resources.data_frame_resource

.. autosummary::

   load_data_frame_from_resource
   load_data_frame_from_resource_id

.. autofunction:: load_data_frame_from_resource
   :no-index:

.. autofunction:: load_data_frame_from_resource_id
   :no-index:

AnnData loaders
~~~~~~~~~~~~~~~

.. currentmodule:: gain.genomic_resources.ann_data_resource

.. autosummary::

   load_ann_data_from_resource
   load_ann_data_from_resource_id

.. autofunction:: load_ann_data_from_resource
   :no-index:

.. autofunction:: load_ann_data_from_resource_id
   :no-index:
