
Position scores
===============

Position score resources associate values with genomic positions. A record can describe a single position or a genomic interval, and the values depend only on the genomic location rather than on a particular allele. Examples include conservation scores such as ``phastCons100way`` and ``phyloP100way``, GC content, and recombination rates. These resources can be stored in tabix-indexed files, bigWig files, or in-memory tables. Their configuration is described in the :ref:`Position scores <grr-position-scores>` section of the GRR documentation.

GAIn provides the :class:`~gain.genomic_resources.genomic_scores.PositionScore` Python class for retrieving and processing these values. ``PositionScore`` inherits the functionality described in :doc:`Genomic scores <scores>` and adds methods for querying individual positions, intervals, and bins.

Accessing a position score
--------------------------

A ``PositionScore`` object can be built from a resource ID and a repository using :func:`~gain.genomic_resources.genomic_scores.build_position_score_from_resource_id`:

.. code-block:: python

    import gain.grr as grr

    repo = grr.build_genomic_resource_repository()
    score = grr.build_position_score_from_resource_id(
        "hg38/scores/phastCons100way", repo
    )

If you already have a ``GenomicResource`` object, use :func:`~gain.genomic_resources.genomic_scores.build_position_score_from_resource` instead:

.. code-block:: python

    resource = repo.get_resource("hg38/scores/phastCons100way")
    score = grr.build_position_score_from_resource(resource)

Both functions are available through ``gain.grr``. The generic :func:`~gain.genomic_resources.genomic_scores.build_score_from_resource_id` function also returns a ``PositionScore`` object when the specified resource is a position score.

Opening and closing a position score
------------------------------------

Position scores are initially closed and must be opened before reading their values. As with other genomic scores, call ``open()`` explicitly and use a ``with`` block to ensure that the object is closed afterward:

.. code-block:: python

    with grr.build_position_score_from_resource_id(
        "hg38/scores/phastCons100way", repo
    ).open() as score:
        print(score.get_scores_at_position("chr21", 5_030_000))

The opening and closing behavior is described in more detail in :doc:`Genomic scores <scores>`.



Retrieving position scores
--------------------------

Position scores provide several methods for retrieving values at individual genomic positions or across intervals. Each query specifies a chromosome and either a position (``pos``) or interval boundaries (``start`` and ``end``).

Most operations have both plural and singular forms. Plural methods, named ``get_scores_...``, retrieve multiple scores. For position and region queries, they accept a list of score IDs and return tuples with values in the corresponding order. If the list is omitted, they retrieve all scores defined by the resource. Singular methods, named ``get_score_...``, retrieve one specified score and return individual values rather than tuples.

Aggregation and binning methods follow a similar distinction, but their plural forms accept a list of aggregation queries, each specifying a score and an aggregator. Their singular forms take a single score and aggregator.

.. list-table::
   :header-rows: 1
   :widths: 24 48 28

   * - Query
     - Methods
     - Result
   * - Single position
     - :meth:`~gain.genomic_resources.genomic_scores.PositionScore.get_scores_at_position`, :meth:`~gain.genomic_resources.genomic_scores.PositionScore.get_score_at_position`
     - Score values at one position
   * - Positions across a region
     - :meth:`~gain.genomic_resources.genomic_scores.PositionScore.get_scores_in_region`, :meth:`~gain.genomic_resources.genomic_scores.PositionScore.get_score_in_region`
     - Values for each position in the region
   * - Aggregated region
     - :meth:`~gain.genomic_resources.genomic_scores.PositionScore.get_scores_in_region_agg`, :meth:`~gain.genomic_resources.genomic_scores.PositionScore.get_score_in_region_agg`
     - One aggregated value per query
   * - Binned region
     - :meth:`~gain.genomic_resources.genomic_scores.PositionScore.get_scores_in_bins`, :meth:`~gain.genomic_resources.genomic_scores.PositionScore.get_score_in_bins`
     - Aggregated values for each bin
   * - Raw records
     - :meth:`~gain.genomic_resources.genomic_scores.GenomicScore.fetch_records`, :meth:`~gain.genomic_resources.genomic_scores.GenomicScore.fetch_region_segments_scores`
     - Records at their original genomic extents

For example, ``get_scores_at_position()`` returns a tuple containing the requested score values at a single genomic position, while ``get_scores_in_region()`` yields one tuple for each position in an interval:

.. code-block:: python

    with score.open() as opened_score:
        print(opened_score.get_scores_at_position("chr21", 5_030_000))

        for values in opened_score.get_scores_in_region(
            "chr21", 5_030_000, 5_030_010
        ):
            print(values)

Use the corresponding singular methods, ``get_score_at_position()`` and ``get_score_in_region()``, when you need only one score. If a score has no value at a position, the result contains ``None`` for that score. This distinguishes positions without data from positions whose score is genuinely zero.

The raw-record methods in the table are inherited from ``GenomicScore``. The shared interfaces for filtering, histograms, chromosome information, and reading data arrays are documented in :doc:`Genomic scores <scores>`. Aggregation and binning are described in the following subsections.





Aggregating scores across regions
---------------------------------

When you need one summary value for a genomic interval rather than a value at each position, use :meth:`~gain.genomic_resources.genomic_scores.PositionScore.get_score_in_region_agg` for a single score or :meth:`~gain.genomic_resources.genomic_scores.PositionScore.get_scores_in_region_agg` for multiple aggregation queries.

An aggregator determines how values are combined across a region. Each score can specify a default aggregator in its resource configuration. If none is specified, GAIn uses the fallback for that value type defined in ``DEFAULT_AGGREGATORS``. For multiple queries, the returned values follow the order of the queries, including when the same score is requested with different aggregators.

Retrieving scores in bins
-------------------------

:meth:`~gain.genomic_resources.genomic_scores.PositionScore.get_score_in_bins` and :meth:`~gain.genomic_resources.genomic_scores.PositionScore.get_scores_in_bins` aggregate scores within a series of fixed-width bins across a genomic region. Instead of producing a single result for the entire region, they produce a summary for each bin.

This is useful for displaying position scores at different genomic resolutions, such as in a genome-browser-style plot. As with region aggregation, the singular method takes one score and aggregator, while the plural method accepts multiple aggregation queries.


API
---

The class below is defined in the module
``gain.genomic_resources.genomic_scores.position`` and is imported from
``gain.genomic_resources.genomic_scores``. ``PositionScore`` is also exported
by ``gain.grr``, as ``grr.PositionScore``.

The builders ``build_position_score_from_resource_id`` and
``build_position_score_from_resource`` build a closed score. They are
defined in the module ``gain.genomic_resources.genomic_scores.builders`` and
are imported from ``gain.genomic_resources.genomic_scores``. They are also
exported by ``gain.grr``, as ``grr.build_position_score_from_resource_id``
and ``grr.build_position_score_from_resource``.

.. currentmodule:: gain.genomic_resources.genomic_scores

.. autosummary::

   build_position_score_from_resource
   build_position_score_from_resource_id
   PositionScore
   PositionScore.get_schema
   PositionScore.get_score_at_position
   PositionScore.get_score_in_bins
   PositionScore.get_score_in_region
   PositionScore.get_score_in_region_agg
   PositionScore.get_scores_at_position
   PositionScore.get_scores_in_bins
   PositionScore.get_scores_in_region
   PositionScore.get_scores_in_region_agg
   PositionScore.resolve_aggregation_queries

Inherited from ``GenomicScore``, which :doc:`scores` describes:

.. autosummary::

   GenomicScore.chrom_length_source
   GenomicScore.chrom_length_sources
   GenomicScore.close
   GenomicScore.compile_filter
   GenomicScore.fetch_records
   GenomicScore.fetch_region_segments_scores
   GenomicScore.fetch_region_value_arrays
   GenomicScore.get_all_chrom_lengths
   GenomicScore.get_all_chromosomes
   GenomicScore.get_chrom_length
   GenomicScore.get_chrom_length_source
   GenomicScore.get_config
   GenomicScore.get_score_value_from_record
   GenomicScore.has_chromosome
   GenomicScore.is_open
   GenomicScore.open
   GenomicScore.supports_region_value_arrays

.. autofunction:: build_position_score_from_resource

.. autofunction:: build_position_score_from_resource_id

.. autoclass:: PositionScore
   :members:
