Gene scores
===========

.. note::

   Draft for review: this page is a revision of :doc:`gene_scores`. Both are
   published while the revision is discussed; one replaces the other.

A gene score attaches values to genes. A resource holds a table with one row
for each gene and one column for each score, such as a constraint measure or
a rank. The class that reads it is
:class:`~gain.gene_scores.gene_scores.GeneScore`.

``GeneScore`` is **not** a
:class:`~gain.genomic_resources.genomic_scores.GenomicScore`. A genomic score
attaches values to places in the genome, and a gene score attaches values to
gene symbols. The two share only the score catalogue: the score definitions
and the histogram reads. A gene score has no position, no allele and no
fragment reads, and nothing in :doc:`scores` applies to it.

Creating a gene score
---------------------

Build it from a resource id and a repository with
:func:`~gain.gene_scores.gene_scores.build_gene_score_from_resource_id`, or
from a resource you already hold with
:func:`~gain.gene_scores.gene_scores.build_gene_score_from_resource`. Both
are exported by ``gain.grr``:

.. code-block:: python

    import gain.grr as grr

    repo = grr.build_genomic_resource_repository()
    gene_score = grr.build_gene_score_from_resource_id(
        "gene_properties/gene_scores/LGD_rank", repo)

    print(list(gene_score.score_definitions))
    print(gene_score.get_gene_value("LGD_rank", "CHD8"))
    print(sorted(gene_score.get_genes("LGD_rank", score_min=0, score_max=100)))

For a resource with the scores ``LGD_rank``, the three calls print:

.. code-block:: text

    ['LGD_rank']
    12.0
    ['CHD8', 'POGZ', 'SCN2A']

The lifecycle
-------------

A gene score has no lifecycle: it has no ``open`` and no ``close``. The
builder reads the whole table into memory, so the returned object is ready
to use. The table is small enough for this: it has one row for each gene.

Reading values
--------------

The reads answer from the in-memory table. A read with an unknown score id
raises ``ValueError``, except ``get_gene_value``.

:meth:`~gain.gene_scores.gene_scores.GeneScore.get_gene_value`
    Returns the value of one score for one gene symbol. It returns ``None``
    when the gene is not in the table or when its value is missing. It also
    returns ``None`` for an unknown score id and does not raise
    ``ValueError``.

:meth:`~gain.gene_scores.gene_scores.GeneScore.get_genes`
    Returns the set of gene symbols whose value lies between ``score_min``
    and ``score_max``, both limits included. A limit left out is unbounded.
    Pass ``values`` instead to select the genes with one of the given values.
    Genes without a value never match.

:meth:`~gain.gene_scores.gene_scores.GeneScore.to_dict`
    Returns a ``{gene_symbol: value}`` dictionary for one score. Genes
    without a value are dropped.

:meth:`~gain.gene_scores.gene_scores.GeneScore.get_score_df`
    Returns a pandas ``DataFrame`` with the columns ``gene`` and the score,
    without the rows that have no value.

:meth:`~gain.gene_scores.gene_scores.GeneScore.get_min`
    Returns the smallest value of one score.

:meth:`~gain.gene_scores.gene_scores.GeneScore.get_max`
    Returns the largest value of one score.

:meth:`~gain.gene_scores.gene_scores.GeneScore.get_values`
    Returns the list of all values of one score.

.. code-block:: python

    print(gene_score.get_score_df("LGD_rank"))

.. code-block:: text

        gene  LGD_rank
    0   CHD8      12.0
    1   POGZ      85.5
    2   ANK2     140.0
    3  SCN2A       7.0

Score definitions and histograms
--------------------------------

The ``scores:`` block of the resource becomes one
:class:`~gain.gene_scores.gene_scores.GeneScoreDef` for each score. The keys
of the block are described on :doc:`/grr`, and the object is described here.
Read the definitions from ``score_definitions``, or one of them with
``get_score_definition``.

Each score can have a histogram. Read it with ``get_score_histogram``, as for
a genomic score. The result is a number, a categorical or a null histogram,
and :doc:`histograms` describes them. A gene score counts genes in each bin.
A gene score with one score needs no score id: ``get_score_histogram()``,
``get_histogram_config()`` and ``get_score_range()`` read that only score.

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
   :no-index:

.. autofunction:: build_gene_score_from_resource_id
   :no-index:

.. autoclass:: GeneScore
   :no-index:
   :members:

.. autoclass:: GeneScoreDef
   :no-index:
   :members:
