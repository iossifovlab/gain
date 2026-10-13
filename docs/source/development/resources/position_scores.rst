Position scores
===============

:class:`~gain.genomic_resources.genomic_scores.PositionScore` is the kind whose records are
per-base: each record gives one or more scores for a position or a run of
positions. Typical resources are conservation tracks such as
``phastCons100way`` and ``phyloP100way``, GC content and recombination rates:
anything that depends only on the genomic position. The data lives in a
tabix-indexed file, a bigWig or an in-memory table. The YAML side of the
resource is documented in :ref:`grr-position-scores`.

Creating a position score
-------------------------

Build it from a resource id and a repository with
:func:`~gain.genomic_resources.genomic_scores.build_position_score_from_resource_id`, or from a
resource you already hold with
:func:`~gain.genomic_resources.genomic_scores.build_position_score_from_resource`. Both are
exported by ``gain.grr``. The generic
:func:`~gain.genomic_resources.genomic_scores.build_score_from_resource_id` returns the same
object when the resource is a position score:

.. code-block:: python

    import gain.grr as grr

    repo = grr.build_genomic_resource_repository()
    score = grr.build_position_score_from_resource_id(
        "hg38/scores/phastCons100way", repo)

The score is built closed. Call ``open()`` before the first read and let a
``with`` block close it; the rule is described on :doc:`scores`:

.. code-block:: python

    with grr.build_position_score_from_resource_id(
            "hg38/scores/phastCons100way", repo).open() as score:
        print(score.get_scores_at_position("chr21", 5_030_000))

Groups of methods
-----------------

Every read takes the locus first (``chrom`` and either ``pos`` or
``start`` and ``end``). The reads come in a plural form and a singular form,
named without the ``s``. The plural point and region reads take a list of
score ids and answer a tuple of values parallel to it, or every score the
resource defines when the list is omitted. The reduced and binned reads take
a list of aggregation queries instead, one for each score and aggregator. The
singular form takes one ``score`` (and, for the reduced and binned reads, an
``aggregator``) and answers one value.

.. list-table::
   :header-rows: 1
   :widths: 25 45 30

   * - Group
     - Methods
     - Answers
   * - A position
     - :meth:`~gain.genomic_resources.genomic_scores.PositionScore.get_scores_at_position`,
       :meth:`~gain.genomic_resources.genomic_scores.PositionScore.get_score_at_position`
     - the values at one position
   * - A region, per position
     - :meth:`~gain.genomic_resources.genomic_scores.PositionScore.get_scores_in_region`,
       :meth:`~gain.genomic_resources.genomic_scores.PositionScore.get_score_in_region`
     - one entry for every position of the interval
   * - A region, reduced
     - :meth:`~gain.genomic_resources.genomic_scores.PositionScore.get_scores_in_region_agg`,
       :meth:`~gain.genomic_resources.genomic_scores.PositionScore.get_score_in_region_agg`
     - one value for each query
   * - A region, binned
     - :meth:`~gain.genomic_resources.genomic_scores.PositionScore.get_scores_in_bins`,
       :meth:`~gain.genomic_resources.genomic_scores.PositionScore.get_score_in_bins`
     - one reduced value for each fixed-width bin
   * - Raw records
     - :meth:`~gain.genomic_resources.genomic_scores.GenomicScore.fetch_records`,
       :meth:`~gain.genomic_resources.genomic_scores.GenomicScore.fetch_region_segments_scores`
     - the table's records, at their own extent

The last group belongs to the base class and is described on
:doc:`scores`, together with the lifecycle, filtering, histograms and the
chromosome reads that all kinds share.

Reading positions and regions
-----------------------------

:meth:`~gain.genomic_resources.genomic_scores.PositionScore.get_scores_at_position`
returns one tuple for one position;
:meth:`~gain.genomic_resources.genomic_scores.PositionScore.get_scores_in_region`
yields one tuple per position of an interval. Both return ``None`` in the
slot of a score that has no value there, which is how "uncovered" is
distinguished from a genuine zero:

.. code-block:: python

    for values in score.get_scores_in_region("chr21", 5_030_000, 5_030_010):
        print(values)

Aggregating a region
--------------------

Where you want an interval reduced to a single number rather than a value per
base, use the aggregating forms —
:meth:`~gain.genomic_resources.genomic_scores.PositionScore.get_score_in_region_agg`
for one score, or
:meth:`~gain.genomic_resources.genomic_scores.PositionScore.get_scores_in_region_agg`
for several queries at once. Each score's default aggregator comes from its
configuration; ``DEFAULT_AGGREGATORS`` on each class gives the fallback per
value type when the configuration names none.
:meth:`~gain.genomic_resources.genomic_scores.PositionScore.get_scores_in_bins`
is the same reduction applied to a grid of fixed-width bins, which is what a
genome-browser-style plot wants.

API
---

The class below is defined in the module
``gain.genomic_resources.genomic_scores.position`` and is imported from
``gain.genomic_resources.genomic_scores``. ``PositionScore`` is also exported
by ``gain.grr``, as ``grr.PositionScore``.

The factories ``build_position_score_from_resource_id`` and
``build_position_score_from_resource`` build a closed score. They are
documented in the :ref:`factories` chapter.

.. currentmodule:: gain.genomic_resources.genomic_scores

.. autosummary::

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

   GenomicScore.aggregate_region
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
   GenomicScore.get_default_annotation_attribute
   GenomicScore.get_default_annotation_attributes
   GenomicScore.get_score_value_from_record
   GenomicScore.get_score_values_from_record
   GenomicScore.has_chromosome
   GenomicScore.is_open
   GenomicScore.open
   GenomicScore.record_weights
   GenomicScore.resource_files
   GenomicScore.supports_region_value_arrays
   GenomicScore.values_from_records

.. autoclass:: PositionScore
   :members:
