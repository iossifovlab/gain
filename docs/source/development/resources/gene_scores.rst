
Gene scores
===========

Gene score resources associate values with individual genes, identified by their gene symbols. Each resource contains a table with one row per gene and one or more columns of scores, such as gene constraint measures or rankings. GAIn provides the :class:`~gain.gene_scores.gene_scores.GeneScore` Python class for accessing and processing these values.

Unlike position, allele, and fragment scores, gene scores are not associated with genomic coordinates. The ``GeneScore`` class is therefore separate from the ``GenomicScore`` class described in :doc:`Genomic scores <scores>`. However, both provide similar functionality for accessing score definitions and histograms.

Accessing a gene score
----------------------

A ``GeneScore`` object can be built from a resource ID and a repository using :func:`~gain.gene_scores.gene_scores.build_gene_score_from_resource_id`:

.. code-block:: python

    import gain.grr as grr

    repo = grr.build_genomic_resource_repository()
    gene_score = grr.build_gene_score_from_resource_id(
        "gene_properties/gene_scores/LGD", repo
    )

If you already have a ``GenomicResource`` object, use :func:`~gain.gene_scores.gene_scores.build_gene_score_from_resource` instead:

.. code-block:: python

    resource = repo.get_resource("gene_properties/gene_scores/LGD")
    gene_score = grr.build_gene_score_from_resource(resource)

Both builder functions are available through ``gain.grr``.

Unlike genomic scores, a ``GeneScore`` object does not require ``open()`` or ``close()``. The builder loads the entire gene score table into memory, so the resulting object is ready to use immediately.

Retrieving gene scores
----------------------

:meth:`~gain.gene_scores.gene_scores.GeneScore.get_gene_value` retrieves the value of a specified score for a gene symbol. For example:

.. code-block:: python

    print(gene_score.get_gene_value("LGD_rank", "CHD8"))

.. code-block:: text

    83.0

The method returns ``None`` if the gene is absent from the table, its score value is missing, or the specified score ID is unknown. Other methods for reading gene scores raise ``ValueError`` when given an unknown score ID.

Several methods provide access to the score values in different forms:

* :meth:`~gain.gene_scores.gene_scores.GeneScore.to_dict` returns a dictionary mapping gene symbols to their score values, excluding genes with missing values.
* :meth:`~gain.gene_scores.gene_scores.GeneScore.get_score_df` returns a pandas ``DataFrame`` containing the columns ``gene`` and the requested score, excluding rows with missing values.
* :meth:`~gain.gene_scores.gene_scores.GeneScore.get_min` and :meth:`~gain.gene_scores.gene_scores.GeneScore.get_max` return the minimum and maximum values of a score.
* :meth:`~gain.gene_scores.gene_scores.GeneScore.get_values` returns a list of all values for a score.

For example, the first five rows of the gene-score DataFrame can be displayed as follows:


.. code-block:: python

    print(gene_score.get_score_df("LGD_rank").head())

.. code-block:: text

        gene  LGD_rank
    0   LRP1       1.0
    1  TRRAP       3.0
    2 ANKRD11      5.0
    3  ZFHX3       8.0
    4  HERC2      25.0


Finding genes by score
----------------------

:meth:`~gain.gene_scores.gene_scores.GeneScore.get_genes` retrieves gene symbols whose score values satisfy specified criteria. The ``score_min`` and ``score_max`` arguments define an inclusive range, meaning genes with scores equal to either boundary are included. If either argument is omitted, the corresponding end of the range is unrestricted.

For example, to retrieve genes with an ``LGD_rank`` between 0 and 100:

.. code-block:: python

    genes = gene_score.get_genes(
        "LGD_rank", score_min=0, score_max=100
    )
    print(sorted(genes)[:10])

.. code-block:: text

    ['ACTG1', 'ANK2', 'ANKHD1', 'ANKRD11', 'ANKRD17', 'ARID1A', 'ASH1L', 'BAHCC1', 'BCL9L', 'BPTF']

The method returns a set of gene symbols. Alternatively, the ``values`` argument can be used to select genes whose scores match any of the specified values rather than a numerical range. Genes with missing score values are excluded from the results.

Score definitions and histograms
--------------------------------

Each gene score resource defines its scores in the ``scores:`` block of the resource definition, as described in :doc:`Genomic resources and repositories </grr>`. In Python, each score is represented by a :class:`~gain.gene_scores.gene_scores.GeneScoreDef` object.

The available definitions can be inspected through ``score_definitions``, or an individual definition can be retrieved using ``get_score_definition()``:

.. code-block:: python

    print(list(gene_score.score_definitions))
    score_def = gene_score.get_score_definition("LGD_rank")

For this resource, the first statement returns:

.. code-block:: text

    ['LGD_score', 'LGD_rank']

Gene scores can also have histograms describing the distribution of their values across genes. The ``get_score_histogram()`` method retrieves the histogram associated with a score:

.. code-block:: python

    hist = gene_score.get_score_histogram("LGD_rank")

The result may be a numeric, categorical, or null histogram, as described in :doc:`Histograms <histograms>`. For gene scores, histogram bins count genes rather than genomic positions or alleles.

When a resource defines only one score, the score ID can be omitted from ``get_score_histogram()``, ``get_histogram_config()``, and ``get_score_range()``. These methods then use the resource's single score automatically.


API
---

The classes below are defined in the module
``gain.gene_scores.gene_scores``. ``GeneScore`` is also exported by
``gain.grr``, as ``grr.GeneScore``. The builders
``build_gene_score_from_resource_id`` and
``build_gene_score_from_resource`` are defined in the same module. They are
also exported by ``gain.grr``, as ``grr.build_gene_score_from_resource_id``
and ``grr.build_gene_score_from_resource``.

.. currentmodule:: gain.gene_scores.gene_scores

.. autosummary::

   build_gene_score_from_resource
   build_gene_score_from_resource_id
   GeneScore
   GeneScore.HISTOGRAM_COUNT_UNIT
   GeneScore.build_score_help
   GeneScore.files
   GeneScore.get_gene_value
   GeneScore.get_genes
   GeneScore.get_max
   GeneScore.get_min
   GeneScore.get_schema
   GeneScore.get_score_df
   GeneScore.get_values
   GeneScore.get_x_scale
   GeneScore.get_y_scale
   GeneScore.to_dict
   GeneScore.to_tsv
   GeneScoreDef

.. autofunction:: build_gene_score_from_resource

.. autofunction:: build_gene_score_from_resource_id

.. autoclass:: GeneScore
   :members:

.. autoclass:: GeneScoreDef
   :members:
