Allele scores
=============

Allele score resources associate values with individual alleles, identified by
chromosome, position, reference allele, and alternative allele. Variant-level
scores such as ``CADD`` are typical examples. A resource may contain one or
several records for the same allele, for example when scores are reported
separately for different transcripts. This is specified by the
``allele_multiplicity`` setting in the resource definition, as described in the
:ref:`Allele scores <grr-allele-scores>` section of the GRR documentation.

GAIn provides the :class:`~gain.genomic_resources.genomic_scores.AlleleScore`
Python class for retrieving and processing these values. ``AlleleScore``
inherits the functionality described in :doc:`Genomic scores <scores>` and adds
methods for querying individual alleles and genomic regions. It also supports
retrieving multiple records for an allele and aggregating their values.

Accessing an allele score
-------------------------

An ``AlleleScore`` object can be built from a resource ID and a repository
using :func:`~gain.genomic_resources.genomic_scores.build_allele_score_from_resource_id`:

.. code-block:: python

    import gain.grr as grr

    repo = grr.build_genomic_resource_repository()
    score = grr.build_allele_score_from_resource_id(
        "hg38/scores/CADD_v1.7", repo
    )

If you already have a ``GenomicResource`` object, use
:func:`~gain.genomic_resources.genomic_scores.build_allele_score_from_resource`
instead:

.. code-block:: python

    resource = repo.get_resource("hg38/scores/CADD_v1.7")
    score = grr.build_allele_score_from_resource(resource)

Both builder functions are available through ``gain.grr``. The generic
:func:`~gain.genomic_resources.genomic_scores.build_score_from_resource_id`
function also returns an ``AlleleScore`` object when the specified resource
is an allele score. The object's multiplicity and score mode are determined
from the resource definition when it is built. These properties are explained
below.

Opening and closing an allele score
-----------------------------------

An ``AlleleScore`` object is initially closed and must be opened before
reading values. As with other genomic scores, call ``open()`` explicitly
and use a ``with`` block to close the object afterward:

.. code-block:: python

    with grr.build_allele_score_from_resource_id(
        "hg38/scores/CADD_v1.7", repo
    ).open() as score:
        print(score.get_allele_scores_for_allele("chr1", 69_094, "G", "A"))

The opening and closing behavior is described in more detail in
:doc:`Genomic scores <scores>`.

Retrieving allele scores
------------------------

Allele scores provide methods for retrieving values for a specified allele or for alleles found within a genomic interval. Queries for an individual allele use ``chrom``, ``pos``, ``ref``, and ``alt``. Region queries use ``chrom``, ``start``, and ``end``. These location arguments are positional, while the remaining arguments are supplied by keyword. All the allele-query methods listed below accept an optional ``score_filter``, as described in :doc:`Genomic scores <scores>`.

The methods differ in whether they return one record, all matching records,
or aggregated values. Methods ending in ``_rows`` return individual records
without combining them. Methods ending in ``_agg`` combine matching records
using aggregation queries. Methods without either suffix return the values
of a single record. The latter are appropriate only when the resource is
expected to have one record per allele.

Most read methods have plural and singular forms. Plural methods named
``get_allele_scores_...`` accept a list of score IDs (``scores``). Singular
methods named ``get_allele_score_...`` take one score ID (``score``).
Aggregation methods use score-and-aggregator queries instead. The region
aggregation method has no singular counterpart.

.. list-table:: Allele score query methods
   :header-rows: 1
   :stub-columns: 1
   :widths: 24 25 25 26

   * - Query
     - One record
     - All matching records (``_rows``)
     - Aggregated values (``_agg``)
   * - One allele (``for_allele``)
     - :meth:`~gain.genomic_resources.genomic_scores.AlleleScore.get_allele_scores_for_allele`
     - :meth:`~gain.genomic_resources.genomic_scores.AlleleScore.get_allele_scores_for_allele_rows`
     - :meth:`~gain.genomic_resources.genomic_scores.AlleleScore.get_allele_scores_for_allele_agg`
   * - One position (``at_position``)
     - Not implemented
     - Not implemented
     - Not implemented
   * - A region (``in_region``)
     - Not implemented
     - :meth:`~gain.genomic_resources.genomic_scores.AlleleScore.get_allele_scores_in_region_rows`
     - :meth:`~gain.genomic_resources.genomic_scores.AlleleScore.get_allele_scores_in_region_agg`

The methods marked as not implemented are reserved in the interface, but no
corresponding read methods are currently available.

For a specific allele, ``get_allele_scores_for_allele()`` returns a tuple of
values in the order of the requested scores, or ``None`` if no record is
found. Its singular counterpart,
:meth:`~gain.genomic_resources.genomic_scores.AlleleScore.get_allele_score_for_allele`,
returns one value. For alleles with potentially multiple records,
:meth:`~gain.genomic_resources.genomic_scores.AlleleScore.get_allele_scores_for_allele_rows`
returns a list of tuples, one per record. The singular
:meth:`~gain.genomic_resources.genomic_scores.AlleleScore.get_allele_score_for_allele_rows`
returns a list of values.

Region queries also report which alleles were found.
:meth:`~gain.genomic_resources.genomic_scores.AlleleScore.get_allele_scores_in_region_rows`
yields an :class:`~gain.genomic_resources.genomic_scores.AlleleEntry` for each
record, containing ``pos``, ``ref``, ``alt``, and ``values``. Its singular
counterpart,
:meth:`~gain.genomic_resources.genomic_scores.AlleleScore.get_allele_score_in_region_rows`,
yields tuples of ``(pos, ref, alt, value)``. In contrast, methods querying
one specified allele do not include its coordinates and nucleotides in their
results, since these were already supplied by the caller.

For example:

.. code-block:: python

    with grr.build_score_from_resource_id(
        "hg38/scores/CADD_v1.7", repo
    ).open() as score:
        values = score.get_allele_scores_for_allele(
            "chr1", 69_094, "G", "A"
        )
        print(values)

        for entry in score.get_allele_scores_in_region_rows(
            "chr1", 69_094, 69_096
        ):
            print(entry.pos, entry.ref, entry.alt, entry.values)

Aggregating allele scores
-------------------------

Aggregation methods combine values from the records found for one allele or
within a genomic region. Use
:meth:`~gain.genomic_resources.genomic_scores.AlleleScore.get_allele_scores_for_allele_agg`
to aggregate the records for a specified allele, or
:meth:`~gain.genomic_resources.genomic_scores.AlleleScore.get_allele_scores_in_region_agg`
to aggregate records within a region. For a single score and aggregator at a
specified allele, use
:meth:`~gain.genomic_resources.genomic_scores.AlleleScore.get_allele_score_for_allele_agg`.
There is no singular region aggregation method.

These methods return an
:class:`~gain.genomic_resources.genomic_scores.AlleleAggregate` object,
which contains the aggregated ``values`` and the allele keys matched by the
query. The allele keys are present only when the call asks for them with the
``allele_keys`` argument; otherwise ``allele_keys`` is ``None``. The values are ordered according to the aggregation queries, not
indexed by score ID. Thus, asking for the same score with two different
aggregators produces two results. Region aggregation processes the matching
records in a single traversal, keeping the aggregate values and matched
alleles consistent. The shared aggregation interface is also discussed in
:doc:`Genomic scores <scores>`.

Multiple records per allele
---------------------------

An allele score resource may contain multiple records for the same allele, identified by ``(chrom, pos, ref, alt)``. For example, a variant may have separate annotations for different transcripts. The ``allele_multiplicity`` setting in the resource definition specifies whether a resource allows only one record per allele (``one``, the default) or multiple records (``many``). This setting is available in Python through :attr:`~gain.genomic_resources.genomic_scores.AlleleScore.multiplicity`.

The multiplicity setting determines which methods can be used to retrieve allele scores. For resources with ``allele_multiplicity: many``, methods that return a single record are not appropriate because several records may match the same allele. Instead, use methods ending in ``_rows`` to retrieve all matching records or ``_agg`` to combine their values. These methods also work with ``one`` resources. When the data is valid, ``_rows`` returns at most one record for each allele in a ``one`` resource, while ``_agg`` aggregates that record.

GAIn checks whether the records in a resource agree with its declared multiplicity. A single-record read on a ``many`` resource always raises :class:`~gain.genomic_resources.genomic_scores.AlleleMultiplicityError`, even if the requested allele has only one record. For a ``one`` resource, finding multiple records for the same allele is a data error. Once multiplicity enforcement is enabled, this also raises ``AlleleMultiplicityError``. Single-record reads count matching records before applying ``score_filter``, so filtering cannot conceal a multiplicity violation.

Multiplicity is also checked when resource statistics are generated using ``grr_manage repo-stats`` or ``grr_manage repo-repair``. For a resource declaring ``allele_multiplicity: one``, duplicate allele records produce warnings before the enforcement release. During this period, single-record reads also warn and return the first matching record. The ``score_filter`` is applied only to that record, so the result may be ``None`` even if another matching record would pass the filter.

Starting with the enforcement release, duplicate allele records in a ``one`` resource raise ``AlleleMultiplicityError``, and a statistics build encountering such a violation produces no output. The enforcement release is defined by ``ALLELE_MULTIPLICITY_ENFORCEMENT_RELEASE`` (``2027.1.0``) in ``gain.genomic_resources.resource_types``. The ``allele_multiplicity_enforced()`` function in the same module indicates whether enforcement is active in the installed GAIn version.


Missing values and filtered results
-----------------------------------

The different read methods distinguish missing alleles and filtered records
in different ways. Single-record reads return ``None`` when no record is
found or the selected record is rejected by the filter. The ``_rows``
methods return an empty result in either case. Aggregation methods make an
additional distinction between an allele with no matching records and an
allele whose records were all filtered out. For single-record reads, the first three cases assume a resource
with ``allele_multiplicity: one``.

.. list-table:: Allele score query results
   :header-rows: 1
   :stub-columns: 1
   :widths: 26 25 21 28

   * - Situation
     - Single-record read
     - ``_rows`` read
     - ``for_allele_agg`` read
   * - No record for the allele
     - ``None``
     - Empty result
     - ``None``
   * - Records exist, but the filter rejects all of them
     - ``None``
     - Empty result
     - Aggregate over an empty selection
   * - Exactly one record
     - Its values
     - One record
     - Aggregate of one record
   * - Multiple records on a ``one`` resource
     - Warning and first record before enforcement, or ``AlleleMultiplicityError`` afterward
     - All matching records
     - Aggregate of all matching records
   * - Any allele on a ``many`` resource
     - ``AlleleMultiplicityError``, raised immediately
     - All matching records
     - Aggregate of all matching records
   * - Unknown contig
     - ``ValueError``
     - ``ValueError``
     - ``ValueError``

For one allele, an empty ``_rows`` result is an empty list. For a region, an
empty ``_rows`` result is a generator that yields no records. These methods
never return ``None``. The region aggregation method follows the same rules
as aggregation for one allele: it returns ``None`` if no record overlaps the
region, but returns an aggregate over an empty selection if records exist
and the filter rejects all of them. In an empty aggregate, ``max`` produces
``None`` and ``list`` produces ``[]``.

Allele score modes
------------------

Allele score resources also declare an ``allele_score_mode`` of either
``substitutions`` or ``alleles``. These are reported by
:meth:`~gain.genomic_resources.genomic_scores.AlleleScore.substitutions_mode`
and :meth:`~gain.genomic_resources.genomic_scores.AlleleScore.alleles_mode`.
Unlike ``allele_multiplicity``, the mode does not change how the read methods
work. A direct lookup for an insertion or deletion retrieves its exact
record, or returns ``None``, in either mode.

Instead, the mode determines how the allele score annotator processes a
``VCFAllele``. In ``substitutions`` mode, substitutions are matched exactly,
while insertions, deletions, and complex alleles are handled by aggregating
scores over the bases they cover. In ``alleles`` mode, every allele is
matched exactly. The annotator also considers ``allele_multiplicity``.
For a ``one`` resource, it uses a single-record lookup. For a ``many``
resource, it uses
:meth:`~gain.genomic_resources.genomic_scores.AlleleScore.get_allele_scores_for_allele_agg`,
aggregating each attribute with its configured aggregator.

API
---

The classes below are defined in the module
``gain.genomic_resources.genomic_scores.allele``, and the package
``gain.genomic_resources.genomic_scores`` exports them. ``AlleleScore`` is also
exported by ``gain.grr``, as ``grr.AlleleScore``.

The builders ``build_allele_score_from_resource_id`` and
``build_allele_score_from_resource`` build a closed score. They are
defined in the module ``gain.genomic_resources.genomic_scores.builders`` and
are imported from ``gain.genomic_resources.genomic_scores``. They are also
exported by ``gain.grr``, as ``grr.build_allele_score_from_resource_id``
and ``grr.build_allele_score_from_resource``.

.. currentmodule:: gain.genomic_resources.genomic_scores

.. autosummary::

   build_allele_score_from_resource
   build_allele_score_from_resource_id
   AlleleScore
   AlleleScore.DEFAULT_AGGREGATORS
   AlleleScore.HISTOGRAM_COUNT_UNIT
   AlleleScore.Mode
   AlleleScore.Multiplicity
   AlleleScore.alleles_mode
   AlleleScore.fetch_region_allele_arrays
   AlleleScore.get_allele_score_for_allele
   AlleleScore.get_allele_score_for_allele_agg
   AlleleScore.get_allele_score_for_allele_rows
   AlleleScore.get_allele_score_in_region_rows
   AlleleScore.get_allele_scores_for_allele
   AlleleScore.get_allele_scores_for_allele_agg
   AlleleScore.get_allele_scores_for_allele_rows
   AlleleScore.get_allele_scores_in_region_agg
   AlleleScore.get_allele_scores_in_region_rows
   AlleleScore.get_schema
   AlleleScore.multiplicity
   AlleleScore.substitutions_mode
   AlleleScore.supports_region_allele_arrays
   AlleleAggregate
   AlleleEntry
   AlleleMultiplicityError
   allele_key

Inherited from ``GenomicScore``, which has its own page,
:doc:`scores`:

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

.. autofunction:: build_allele_score_from_resource

.. autofunction:: build_allele_score_from_resource_id

.. autoclass:: AlleleScore
   :members:

.. autoclass:: AlleleAggregate
   :members:

.. autoclass:: AlleleEntry
   :members:

.. autoclass:: AlleleMultiplicityError
   :members:

.. autofunction:: allele_key
