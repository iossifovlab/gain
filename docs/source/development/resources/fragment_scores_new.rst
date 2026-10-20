Fragment scores
===============

.. note::

   Draft for review: this page is a revision of :doc:`fragment_scores`. Both are
   published while the revision is discussed; one replaces the other.

:class:`~gain.genomic_resources.genomic_scores.FragmentScore` is the kind whose records are
intervals. A fragment is a span of the genome carrying attributes:
copy-number variants, ATAC fragments and region annotations are the typical
examples. Unlike a position score, a fragment keeps its own extent, and
fragments may overlap one another. The YAML side of the resource is
documented in :ref:`grr-fragment-scores`.

Creating a fragment score
-------------------------

Build it with :func:`~gain.genomic_resources.genomic_scores.build_fragment_score_from_resource_id`
or, from a resource you already hold,
:func:`~gain.genomic_resources.genomic_scores.build_fragment_score_from_resource`. The resource
declares ``type: fragment_score``; the older name ``cnv_collection`` is
still accepted, with a deprecation warning. The generic
:func:`~gain.genomic_resources.genomic_scores.build_score_from_resource_id` returns the same
object. Both typed builders are exported by ``gain.grr``:

.. code-block:: python

    import gain.grr as grr

    repo = grr.build_genomic_resource_repository()
    score = grr.build_fragment_score_from_resource_id(
        "hg38/scores/my_CNVcollection", repo)

The lifecycle
-------------

As with every score, the object is built closed. Call ``open()`` before the
first read and let a ``with`` block close it; the rule is described on
:doc:`scores`:

.. code-block:: python

    with grr.build_fragment_score_from_resource_id(
            "hg38/scores/my_CNVcollection", repo).open() as score:
        for begin, end, values in score.get_fragment_scores_overlapping_region(
                "chr1", 1_000_000, 2_000_000):
            print(begin, end, values)

Groups of methods
-----------------

Each read exists in a plural form, which takes a ``scores`` list and returns
values parallel to it, and a singular form that takes one ``score``. The
locus is positional; every other argument is keyword-only. The method name
says which relation to the query it uses:

.. list-table::
   :header-rows: 1
   :widths: 25 45 30

   * - Relation
     - Methods
     - Answers
   * - Overlapping a region
     - :meth:`~gain.genomic_resources.genomic_scores.FragmentScore.get_fragment_scores_overlapping_region`,
       :meth:`~gain.genomic_resources.genomic_scores.FragmentScore.get_fragment_score_overlapping_region`
     - ``(begin, end, values)`` for each fragment that intersects the region
   * - Covering a position
     - :meth:`~gain.genomic_resources.genomic_scores.FragmentScore.get_fragment_scores_at_position`,
       :meth:`~gain.genomic_resources.genomic_scores.FragmentScore.get_fragment_score_at_position`
     - the fragments that contain one point
   * - Starting in a region
     - :meth:`~gain.genomic_resources.genomic_scores.FragmentScore.get_fragment_scores_starting_in_region`,
       :meth:`~gain.genomic_resources.genomic_scores.FragmentScore.get_fragment_score_starting_in_region`
     - the fragments that begin inside the region, so that adjacent windows
       partition the fragments
   * - Reduced
     - :meth:`~gain.genomic_resources.genomic_scores.FragmentScore.get_fragment_scores_overlapping_region_agg`,
       :meth:`~gain.genomic_resources.genomic_scores.FragmentScore.get_fragment_score_overlapping_region_agg`
     - a :class:`~gain.genomic_resources.genomic_scores.FragmentAggregate`: one value for each query
       and the number of fragments seen
   * - Binned
     - :meth:`~gain.genomic_resources.genomic_scores.FragmentScore.get_scores_in_bins`
     - one reduced value for each fixed-width bin

The reads that return fragments accept a ``score_filter``, compiled by
:meth:`~gain.genomic_resources.genomic_scores.GenomicScore.compile_filter`; the overlapping reads
also accept the two overlap-fraction thresholds
``min_region_overlap_fraction`` and ``min_fragment_overlap_fraction``.

Which relation to use
---------------------

The method names of :class:`~gain.genomic_resources.genomic_scores.FragmentScore` say exactly which relation to the query region they use:
``overlapping_region`` for fragments that intersect it,
``starting_in_region`` for fragments that begin inside it, ``at_position``
for those covering one point. The distinction matters: summing a score over
overlapping fragments double-counts a fragment that straddles two adjacent
query windows, and the ``starting_in`` form is the one that tiles.

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
   :no-index:

.. autofunction:: build_fragment_score_from_resource_id
   :no-index:

.. autoclass:: FragmentScore
   :no-index:
   :members:

.. autoclass:: FragmentAggregate
   :no-index:
   :members:
