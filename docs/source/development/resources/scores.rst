
Genomic scores
==============

Genomic scores associate values with genomic positions, alleles, or intervals. GAIn distinguishes three types of genomic scores according to what each record describes:

.. list-table::
   :header-rows: 1
   :widths: 22 33 45

   * - Type
     - What a record represents
     - Examples
   * - :doc:`Position scores <position_scores>`
     - A genomic position or a run of positions
     - Conservation scores such as ``phastCons100way``
   * - :doc:`Allele scores <allele_scores>`
     - An allele or substitution at a genomic position
     - Variant-level scores such as ``CADD``
   * - :doc:`Fragment scores <fragment_scores>`
     - A genomic interval with associated attributes
     - CNV collections, ATAC fragments, and region annotations

All three types share the :class:`~gain.genomic_resources.genomic_scores.GenomicScore` base class. It provides common functionality for opening and closing score resources, inspecting score definitions, retrieving records, and filtering data. Each specialized class also provides methods appropriate to the genomic features it represents. This chapter covers the shared Python interface. The following chapters describe the three score types individually.

Accessing a genomic score
-------------------------

The :func:`~gain.genomic_resources.genomic_scores.build_score_from_resource_id` function builds a genomic score object from a resource ID and a repository:

.. code-block:: python

    import gain.grr as grr

    repo = grr.build_genomic_resource_repository()
    score = grr.build_score_from_resource_id("hg38/scores/phastCons100way", repo)

If you already have a ``GenomicResource`` object, use :func:`~gain.genomic_resources.genomic_scores.build_score_from_resource` instead:

.. code-block:: python

    resource = repo.get_resource("hg38/scores/phastCons100way")
    score = grr.build_score_from_resource(resource)

Both functions are available through ``gain.grr``. They determine the score type from the resource definition and return the corresponding ``PositionScore``, ``AlleleScore``, or ``FragmentScore`` object. Each type also has its own builder functions, described in the corresponding chapter.

Opening and closing a genomic score
-----------------------------------

A ``GenomicScore`` object is initially closed. It uses an underlying data source, such as a tabix-indexed file, a bigWig, or a VCF, that must be opened before reading scores. Call :meth:`~gain.genomic_resources.genomic_scores.GenomicScore.open` to open the object and :meth:`~gain.genomic_resources.genomic_scores.GenomicScore.close` when you finish. The ``open()`` method returns the score object, so it can be chained to a builder function.

A ``with`` block closes the score automatically, including when an error occurs inside the block:

.. code-block:: python

    with grr.build_score_from_resource_id(
        "hg38/scores/phastCons100way", repo
    ).open() as score:
        print(score.get_scores_at_position("chr21", 5_030_000))

The ``with`` statement does not open the score automatically, so the ``.open()`` call is necessary. Attempting to read a closed score raises ``ValueError: genomic score <id> is not open``. Requiring an explicit ``open()`` call makes the cost of initializing a score clear and helps prevent file handles from being left open. You can check whether a score is open using :meth:`~gain.genomic_resources.genomic_scores.GenomicScore.is_open`. This behavior applies to all three genomic score types.

Score definitions
-----------------

A genomic score resource can provide one or more scores. The resource definition describes these in a ``scores:`` block, as explained in the `Scores <../../grr.html#scores>`_ section of the GRR documentation.

In Python, each declared score is represented by a :class:`~gain.genomic_resources.score_def.GenomicScoreDef` object. It contains information such as the score ID, value type, rules for converting values read from the data file, and the associated histogram configuration. Score definitions can be inspected through ``score.score_definitions`` or retrieved individually with ``get_score_definition()``:

.. code-block:: python

    for score_def in score.score_definitions.values():
        print(score_def.score_id, score_def.value_type)

    one = score.get_score_definition("phastCons100way")

:meth:`~gain.genomic_resources.score_def.GenomicScoreDef.parse_value` converts an individual raw value to its declared type. :meth:`~gain.genomic_resources.score_def.GenomicScoreDef.parse_array` performs the same conversion on an array of values. The array method supports efficient reads of large regions without constructing a separate Python object for every record.

Chromosome names and lengths
----------------------------

:meth:`~gain.genomic_resources.genomic_scores.GenomicScore.get_all_chromosomes` returns the contigs represented in the score's data, in the order stored in the table. :meth:`~gain.genomic_resources.genomic_scores.GenomicScore.has_chromosome` checks whether a particular contig is present.

Chromosome lengths can come from several sources. GAIn may use the reference genome associated with the score, the header of a bigWig file, an estimate from a tabix index, or the extent of records in an in-memory table. These sources are identified by :class:`~gain.genomic_resources.genomic_position_table.ChromLengthSource`. A length obtained from a reference genome or bigWig header can describe the complete chromosome, whereas a tabix estimate provides an upper bound derived from the indexed data rather than the true chromosome length.

By default, GAIn selects the best available source. You can inspect the available sources with ``chrom_length_sources``, request a specific source, and determine which source supplied a result:

.. code-block:: python

    with grr.build_score_from_resource_id(
        "hg38/scores/phastCons100way", repo
    ).open() as score:
        print(score.chrom_length_sources)
        print(score.get_chrom_length("chr21"))
        print(score.get_chrom_length_source("chr21"))
        print(score.get_chrom_length("chr21", source="tabix_estimate"))
        print(score.get_all_chrom_lengths(source="reference_genome"))

:meth:`~gain.genomic_resources.genomic_scores.GenomicScore.get_all_chrom_lengths` returns lengths for the contigs covered by the requested source. The reference-genome lengths reach the score through the ``statistics/chrom_lengths.json`` file created during repository repair. If a resource was repaired before that file was introduced, it may have only table-derived lengths until ``grr_manage repo-repair`` is run again. Requesting the unavailable ``reference_genome`` source then raises a ``ValueError``.

A ``ValueError`` is also raised when a chromosome-length operation cannot provide an answer, for example when the score is closed, the contig is absent from the score, the requested source has no length for it, or no available source can determine its length.

Reading score records and arrays
--------------------------------

:meth:`~gain.genomic_resources.genomic_scores.GenomicScore.fetch_records` yields the records in a genomic region, one at a time. This is the general interface for reading the underlying score data.

For score types and backends that support it, :meth:`~gain.genomic_resources.genomic_scores.GenomicScore.fetch_region_value_arrays` retrieves entire columns as arrays instead of returning a separate record for each row. This is useful when working with large numbers of score values. Before using it, check :meth:`~gain.genomic_resources.genomic_scores.GenomicScore.supports_region_value_arrays` for the scores you want to retrieve. Support depends on the requested value types as well as the underlying data backend.

The position, allele, and fragment score chapters describe methods for retrieving score values at specific genomic locations.

Filtering scores
----------------

:meth:`~gain.genomic_resources.genomic_scores.GenomicScore.compile_filter` converts a boolean expression involving the score's columns into a :class:`~gain.genomic_resources.score_filter.ScoreFilter` object. This filter can be passed to read methods through the ``score_filter`` argument, so that records are selected as they are read.

For example, the following code selects records with a ``phastCons100way`` score greater than 0.9:

.. code-block:: python

    with grr.build_score_from_resource_id(
        "hg38/scores/phastCons100way", repo
    ).open() as score:
        keep = score.compile_filter("phastCons100way > 0.9")
        for record in score.fetch_records(
            "chr21", 5_030_000, 5_040_000, score_filter=keep
        ):
            print(record)

Each filter is associated with the score object that created it. A filter compiled for one score cannot be used with another score object, even when their columns have the same names.


Aggregating scores across regions
---------------------------------

Allele and fragment scores provide methods for aggregating the values found in a genomic region. These methods are :meth:`~gain.genomic_resources.genomic_scores.AlleleScore.get_allele_scores_in_region_agg` and :meth:`~gain.genomic_resources.genomic_scores.FragmentScore.get_fragment_scores_overlapping_region_agg`. They process the matching records in a single traversal and return an :class:`~gain.genomic_resources.genomic_scores.AlleleAggregate` or :class:`~gain.genomic_resources.genomic_scores.FragmentAggregate` object, respectively.

Each aggregate contains the reduced ``values`` and information about the records used to calculate them. For allele scores, this includes the matched allele keys, when the call asks for them with the ``allele_keys`` argument. For fragment scores, it includes the number of fragments. Keeping these results together ensures that the values and accompanying information describe the same set of records.

The ``values`` are returned in the order of the aggregation queries, rather than indexed by score ID. For example, requesting the same score with two different aggregators produces two values, one for each query. The allele and fragment score chapters describe their aggregation methods in more detail.

Score histograms
----------------

A genomic score may have a histogram describing the distribution of its values. The ``get_score_histogram()`` method retrieves the histogram associated with a score:

.. code-block:: python

    hist = score.get_score_histogram("phastCons100way")

The result may be a numeric, categorical, or null histogram. These types are described in :ref:`Reading a number histogram <histograms-reading-number>`, :ref:`Reading a categorical histogram <histograms-reading-categorical>`, and :ref:`Reading a null histogram <histograms-reading-null>`. Check the histogram type before using attributes specific to that type.

When a resource defines only one score, the score ID can be omitted from ``get_score_histogram()``, ``get_histogram_config()``, ``get_score_range()``, and the two methods that retrieve histogram image URLs. These methods then use that resource's single score automatically. The dedicated :doc:`Histograms <histograms>` chapter explains histogram configurations, contents, and retrieval in more detail.


API
---

``GenomicScore`` is defined in the module
``gain.genomic_resources.genomic_scores.base`` and is imported from the
package ``gain.genomic_resources.genomic_scores``. ``GenomicScoreDef`` is
defined in the module ``gain.genomic_resources.score_def``. ``ScoreFilter`` is
defined in the module ``gain.genomic_resources.score_filter``. ``GenomicScore``
is also exported by ``gain.grr``, as ``grr.GenomicScore``. The generic
builders ``build_score_from_resource_id`` and ``build_score_from_resource``
are defined in the module ``gain.genomic_resources.genomic_scores.builders``
and are imported from the package ``gain.genomic_resources.genomic_scores``.
They are also exported by ``gain.grr``, as
``grr.build_score_from_resource_id`` and ``grr.build_score_from_resource``.

.. currentmodule:: gain.genomic_resources.genomic_scores

.. autosummary::

   build_score_from_resource
   build_score_from_resource_id
   GenomicScore
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

.. currentmodule:: gain.genomic_resources.score_def

.. autosummary::

   GenomicScoreDef
   GenomicScoreDef.parse_array
   GenomicScoreDef.parse_value

.. currentmodule:: gain.genomic_resources.score_filter

.. autosummary::

   ScoreFilter
   ScoreFilter.require_owner
   ScoreFilter.select

.. currentmodule:: gain.genomic_resources.genomic_scores

.. autofunction:: build_score_from_resource

.. autofunction:: build_score_from_resource_id

.. autoclass:: GenomicScore
   :members:

.. currentmodule:: gain.genomic_resources.score_def

.. autoclass:: GenomicScoreDef
   :members:

.. currentmodule:: gain.genomic_resources.score_filter

.. autoclass:: ScoreFilter
   :members:
