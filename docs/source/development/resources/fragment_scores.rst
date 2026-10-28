
Fragment scores
===============

Fragment score resources associate values with genomic intervals. Each record describes a fragment with its own start and end coordinates and one or more attributes. Examples include copy-number variants, ATAC fragments, and other region annotations. Unlike position scores, which describe values at genomic positions, fragment scores preserve the extent of each interval. Different fragments may also overlap one another.

GAIn provides the :class:`~gain.genomic_resources.genomic_scores.FragmentScore` Python class for retrieving and processing these records. ``FragmentScore`` inherits the functionality described in :doc:`Genomic scores <scores>` and adds methods for querying fragments according to their positions relative to a genomic interval or position. The resource configuration is described in the :ref:`Fragment scores <grr-fragment-scores>` section of the GRR documentation.

Accessing a fragment score
--------------------------

A ``FragmentScore`` object can be built from a resource ID and a repository using :func:`~gain.genomic_resources.genomic_scores.build_fragment_score_from_resource_id`:

.. code-block:: python

    import gain.grr as grr

    repo = grr.build_genomic_resource_repository()
    score = grr.build_fragment_score_from_resource_id(
        "hg38/scores/my_CNVcollection", repo
    )

If you already have a ``GenomicResource`` object, use :func:`~gain.genomic_resources.genomic_scores.build_fragment_score_from_resource` instead:

.. code-block:: python

    resource = repo.get_resource("hg38/scores/my_CNVcollection")
    score = grr.build_fragment_score_from_resource(resource)

Both builder functions are available through ``gain.grr``. The generic :func:`~gain.genomic_resources.genomic_scores.build_score_from_resource_id` function also returns a ``FragmentScore`` object when the specified resource is a fragment score.

Fragment score resources use ``type: fragment_score`` in their resource definitions. The older type name ``cnv_collection`` is still accepted, but produces a deprecation warning.

Opening and closing a fragment score
------------------------------------

A ``FragmentScore`` object is initially closed and must be opened before reading its values. As with other genomic scores, call ``open()`` explicitly and use a ``with`` block to close the object afterward:

.. code-block:: python

    with grr.build_fragment_score_from_resource_id(
        "hg38/scores/my_CNVcollection", repo
    ).open() as score:
        for begin, end, values in score.get_fragment_scores_overlapping_region(
            "chr1", 1_000_000, 2_000_000
        ):
            print(begin, end, values)

This example retrieves fragments that overlap the specified region, reporting each fragment's start and end coordinates and its associated score values. The opening and closing behavior is described in more detail in :doc:`Genomic scores <scores>`.

Retrieving fragment scores
--------------------------

Fragment scores provide three ways to select records according to their genomic locations:

* Overlapping a region: retrieve fragments that intersect the query interval, including fragments that extend beyond its boundaries.
* Covering a position: retrieve fragments that contain the specified genomic position.
* Starting in a region: retrieve fragments whose starting coordinates fall within the query interval, even if they extend beyond it.

Most query methods have plural and singular forms. Plural methods, named ``get_fragment_scores_...``, take a list of score IDs (``scores``) and return values in the corresponding order for each matching fragment. Singular methods, named ``get_fragment_score_...``, take one score ID (``score``) and return its value for each matching fragment. The genomic location is supplied through positional arguments, while the remaining arguments are supplied by keyword.

.. list-table:: Fragment score query methods
   :header-rows: 1
   :widths: 25 45 30

   * - Query
     - Methods
     - Result
   * - Overlapping a region
     - :meth:`~gain.genomic_resources.genomic_scores.FragmentScore.get_fragment_scores_overlapping_region`, :meth:`~gain.genomic_resources.genomic_scores.FragmentScore.get_fragment_score_overlapping_region`
     - ``(begin, end, values)`` for each overlapping fragment
   * - Covering a position
     - :meth:`~gain.genomic_resources.genomic_scores.FragmentScore.get_fragment_scores_at_position`, :meth:`~gain.genomic_resources.genomic_scores.FragmentScore.get_fragment_score_at_position`
     - Fragments containing the specified position
   * - Starting in a region
     - :meth:`~gain.genomic_resources.genomic_scores.FragmentScore.get_fragment_scores_starting_in_region`, :meth:`~gain.genomic_resources.genomic_scores.FragmentScore.get_fragment_score_starting_in_region`
     - Fragments whose starts fall within the region
   * - Column arrays
     - :meth:`~gain.genomic_resources.genomic_scores.FragmentScore.get_fragment_arrays_overlapping_region`, :meth:`~gain.genomic_resources.genomic_scores.FragmentScore.get_fragment_arrays_starting_in_region`
     - Batches of ``(begin, end, {score_id: values})`` numpy arrays, with the fragments of the matching record read
   * - Aggregated region
     - :meth:`~gain.genomic_resources.genomic_scores.FragmentScore.get_fragment_scores_overlapping_region_agg`, :meth:`~gain.genomic_resources.genomic_scores.FragmentScore.get_fragment_score_overlapping_region_agg`
     - Aggregated values and the number of matching fragments
   * - Binned region
     - :meth:`~gain.genomic_resources.genomic_scores.FragmentScore.get_scores_in_bins`
     - Aggregated values for fixed-width bins

The distinction between overlapping and starting in a region matters when analyzing adjacent genomic windows. A fragment extending across the boundary of two windows appears in the results of both overlapping-region queries. Counting or summing these results across windows can therefore count the same fragment more than once. With ``starting_in_region`` queries, each fragment belongs to the window containing its starting coordinate, so adjacent nonoverlapping windows can divide the fragments without duplication.

Methods that return individual fragments accept an optional ``score_filter``, created using :meth:`~gain.genomic_resources.genomic_scores.GenomicScore.compile_filter`. Overlapping-region reads also accept ``min_region_overlap_fraction`` and ``min_fragment_overlap_fraction`` to restrict results according to how much a fragment overlaps the query region. The general filtering interface is explained in :doc:`Genomic scores <scores>`.

Aggregating fragment scores
---------------------------

When you need a summary of the fragments overlapping a genomic region rather than the individual records, use :meth:`~gain.genomic_resources.genomic_scores.FragmentScore.get_fragment_scores_overlapping_region_agg` or its singular counterpart, :meth:`~gain.genomic_resources.genomic_scores.FragmentScore.get_fragment_score_overlapping_region_agg`.

These methods combine the matching score values using the requested aggregators and return a :class:`~gain.genomic_resources.genomic_scores.FragmentAggregate` object. The result contains an aggregated value for each query and the number of fragments seen. The shared aggregation interface is described in :doc:`Genomic scores <scores>`.

Retrieving scores in bins
-------------------------

:meth:`~gain.genomic_resources.genomic_scores.FragmentScore.get_scores_in_bins` divides a query region into fixed-width bins and calculates aggregated scores for each bin. Instead of returning the individual fragments or a single aggregate for the entire region, it provides a series of summaries across the interval. This is useful when displaying or analyzing fragment scores at a chosen genomic resolution.


API
---

The classes below are defined in the module
``gain.genomic_resources.genomic_scores``. ``FragmentScore`` is also exported
by ``gain.grr``, as ``grr.FragmentScore``.

The builders ``build_fragment_score_from_resource_id`` and
``build_fragment_score_from_resource`` build a closed score. They are
defined in the module ``gain.genomic_resources.genomic_scores.builders`` and
are imported from ``gain.genomic_resources.genomic_scores``. They are also
exported by ``gain.grr``, as ``grr.build_fragment_score_from_resource_id``
and ``grr.build_fragment_score_from_resource``.

.. currentmodule:: gain.genomic_resources.genomic_scores

.. autosummary::

   build_fragment_score_from_resource
   build_fragment_score_from_resource_id
   FragmentScore
   FragmentScore.DEFAULT_AGGREGATORS
   FragmentScore.HISTOGRAM_COUNT_UNIT
   FragmentScore.get_fragment_arrays_overlapping_region
   FragmentScore.get_fragment_arrays_starting_in_region
   FragmentScore.get_fragment_score_at_position
   FragmentScore.get_fragment_score_overlapping_region
   FragmentScore.get_fragment_score_overlapping_region_agg
   FragmentScore.get_fragment_score_starting_in_region
   FragmentScore.get_fragment_scores_at_position
   FragmentScore.get_fragment_scores_overlapping_region
   FragmentScore.get_fragment_scores_overlapping_region_agg
   FragmentScore.get_fragment_scores_starting_in_region
   FragmentScore.get_schema
   FragmentScore.get_scores_in_bins
   FragmentAggregate

Inherited from ``GenomicScore``. The entries link to the page :doc:`scores`.

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

.. autofunction:: build_fragment_score_from_resource

.. autofunction:: build_fragment_score_from_resource_id

.. autoclass:: FragmentScore
   :members:

.. autoclass:: FragmentAggregate
   :members:
