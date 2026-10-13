Allele scores
=============

:class:`~gain.genomic_resources.genomic_scores.AlleleScore` is the kind whose records are
alleles: each record gives scores for one substitution or allele, identified
by chromosome, position, reference and alternative. Variant-level scores such
as ``CADD`` are the typical example. A resource may hold several rows for one
allele, usually one for each transcript, and says so with the
``allele_multiplicity`` key. The YAML side of the resource is documented in
:ref:`grr-allele-scores`.

Creating an allele score
------------------------

Build it with :func:`~gain.genomic_resources.genomic_scores.build_allele_score_from_resource_id`
or, from a resource you already hold,
:func:`~gain.genomic_resources.genomic_scores.build_allele_score_from_resource`. The generic
:func:`~gain.genomic_resources.genomic_scores.build_score_from_resource_id` returns the same
object:

.. code-block:: python

    import gain.grr as grr

    repo = grr.build_genomic_resource_repository()
    with grr.build_allele_score_from_resource_id(
            "hg38/scores/CADD_v1.7", repo).open() as score:
        print(score.get_allele_scores_for_allele("chr1", 69_094, "G", "A"))

The score is built closed; see :doc:`scores` for the lifecycle. Three
properties describe the resource and are fixed when it is built:
:attr:`~gain.genomic_resources.genomic_scores.AlleleScore.multiplicity`,
:meth:`~gain.genomic_resources.genomic_scores.AlleleScore.substitutions_mode` and
:meth:`~gain.genomic_resources.genomic_scores.AlleleScore.alleles_mode`. They are explained below.

Groups of methods
-----------------

The read methods are organised by what they look at and what they do with the
rows they find:

* **For one allele** (``for_allele``): the rows of one
  ``(chrom, pos, ref, alt)``.
* **In a region** (``in_region``): the rows whose position falls in an
  interval.
* **Bare reads** answer exactly one row, **rows** (``_rows``) answer every row
  unreduced, and **folds** (``_agg``) reduce the rows with an aggregator and
  answer an :class:`~gain.genomic_resources.genomic_scores.AlleleAggregate`.

The grid below shows how the groups combine.

The allele plane
----------------

The reads are a plane in the same style as the :doc:`position <position_scores>`
and :doc:`fragment <fragment_scores>` reads. Each read takes its locus
positionally and everything else as a keyword, and every read accepts a
``score_filter``. Each has a plural form, which takes a ``scores`` list, and a
singular form, without the ``s``, which takes one ``score``; the region fold
has no singular form. What a read answers depends on its reduction, as
described after the table. The reads sit on a grid: three *loci*
(where to look) by three *reductions* (what to do with the rows found there).

.. list-table:: The allele plane
   :header-rows: 1
   :stub-columns: 1
   :widths: 25 25 25 25

   * - Locus
     - bare: exactly one row
     - ``_rows``: unreduced
     - ``_agg``: folded
   * - ``for_allele(chrom, pos, ref, alt, *, …)``
     - :meth:`~gain.genomic_resources.genomic_scores.AlleleScore.get_allele_scores_for_allele`
     - :meth:`~gain.genomic_resources.genomic_scores.AlleleScore.get_allele_scores_for_allele_rows`
     - :meth:`~gain.genomic_resources.genomic_scores.AlleleScore.get_allele_scores_for_allele_agg`
   * - ``at_position(chrom, pos, *, …)``
     - reserved
     - reserved
     - —
   * - ``in_region(chrom, start, end, *, …)``
     - reserved
     - :meth:`~gain.genomic_resources.genomic_scores.AlleleScore.get_allele_scores_in_region_rows`
     - :meth:`~gain.genomic_resources.genomic_scores.AlleleScore.get_allele_scores_in_region_agg`

Every built cell except the region fold
:meth:`~gain.genomic_resources.genomic_scores.AlleleScore.get_allele_scores_in_region_agg`
also has a singular form, which drops the ``s`` and takes one ``score``:
:meth:`~gain.genomic_resources.genomic_scores.AlleleScore.get_allele_score_for_allele`,
:meth:`~gain.genomic_resources.genomic_scores.AlleleScore.get_allele_score_for_allele_rows`,
:meth:`~gain.genomic_resources.genomic_scores.AlleleScore.get_allele_score_in_region_rows`
and
:meth:`~gain.genomic_resources.genomic_scores.AlleleScore.get_allele_score_for_allele_agg`.
No ``for_allele`` read carries a locus in its answer, because the caller
already knows the allele: the bare read answers one tuple of values parallel
to the requested scores, and its singular form one value; the ``_rows`` read
answers a list of such tuples, one per row, and its singular form a list of
values. The plural region read
:meth:`~gain.genomic_resources.genomic_scores.AlleleScore.get_allele_scores_in_region_rows`
yields one :class:`~gain.genomic_resources.genomic_scores.AlleleEntry` per
row, a named tuple of ``pos``, ``ref``, ``alt`` and ``values``, because there
the position and the nucleotides are new information; its singular form yields
a plain ``(pos, ref, alt, value)`` tuple. The ``_agg`` reads answer an
:class:`~gain.genomic_resources.genomic_scores.AlleleAggregate`, described
in :ref:`region folds <genomic-score-region-folds>`.
The ``reserved`` cells have a name and a meaning but no method yet.

.. code-block:: python

    with grr.build_score_from_resource_id("hg38/scores/CADD_v1.7", repo).open() as score:
        score.get_allele_scores_for_allele("chr1", 69_094, "G", "A")
        # the values of the allele's only row, or None
        for entry in score.get_allele_scores_in_region_rows("chr1", 69_094, 69_096):
            print(entry.pos, entry.ref, entry.alt, entry.values)

**How many rows an allele may have.** Some allele tables have several rows for
one ``(chrom, pos, ref, alt)``, usually one per transcript. A resource says
whether it does with the top-level ``allele_multiplicity`` key, ``one`` (the
default) or ``many``, documented on :ref:`grr-allele-scores` and reported by
:attr:`~gain.genomic_resources.genomic_scores.AlleleScore.multiplicity`. Only
the bare reads depend on it. They answer exactly one row, so on a ``many``
resource they raise
:class:`~gain.genomic_resources.genomic_scores.AlleleMultiplicityError` at the
call, before reading anything; read every row with ``_rows``, or fold them
with ``_agg``. On a ``one`` resource, several rows for the allele are a data
error. The bare read counts the rows before applying the ``score_filter``, so a
filter that hides one of them does not hide the error. The allele statistics
build (``grr_manage repo-stats`` or ``repo-repair``) checks the same promise
over the whole table, and the bare read's own check is the backstop for data
that changed after its statistics were built. Before GAIn
``ALLELE_MULTIPLICITY_ENFORCEMENT_RELEASE`` (``2027.1.0``, in
``gain.genomic_resources.resource_types``), both only warn, and the bare read
answers the first row. From that release on, both raise
:class:`~gain.genomic_resources.genomic_scores.AlleleMultiplicityError`, and
the statistics build writes nothing. ``allele_multiplicity_enforced()`` in the
same module says which applies to the installed version.

The ``_rows`` and ``_agg`` reads accept either multiplicity. On a ``one``
resource ``_rows`` answers at most one row, and ``_agg`` folds that one row.

**What each read answers.** The three reductions answer "nothing here"
differently, and only the folds tell "nothing here" apart from "everything
here was filtered out":

.. list-table:: Outcomes, side by side
   :header-rows: 1
   :stub-columns: 1
   :widths: 25 25 20 30

   * - Situation
     - bare
     - ``_rows``
     - ``for_allele_agg``
   * - No row for the allele
     - ``None``
     - empty
     - ``None``
   * - Rows exist, the filter rejects all
     - ``None``
     - empty
     - an aggregate over an empty selection, not ``None``
   * - Exactly one row
     - its values
     - one entry
     - the fold of one row
   * - Two or more rows on a ``one`` resource
     - before the enforcement release: the first row and a warning, with the
       filter applied to that row alone, so a filter rejecting it gives
       ``None`` even when it would keep a later row; from it on:
       :class:`~gain.genomic_resources.genomic_scores.AlleleMultiplicityError`
     - all rows
     - the fold of all rows
   * - Any allele on a ``many`` resource
     - :class:`~gain.genomic_resources.genomic_scores.AlleleMultiplicityError`,
       at the call
     - all rows
     - the fold of all rows
   * - Unknown contig
     - ``ValueError``, raised at the call
     - ``ValueError``, raised at the call
     - ``ValueError``, raised at the call

"Empty" is an empty list from
:meth:`~gain.genomic_resources.genomic_scores.AlleleScore.get_allele_scores_for_allele_rows`
and a generator that yields nothing from
:meth:`~gain.genomic_resources.genomic_scores.AlleleScore.get_allele_scores_in_region_rows`;
a ``_rows`` read never answers ``None``. The region fold answers like the
``for_allele`` fold: ``None`` when no row overlaps the region, and an aggregate
over an empty selection when the filter rejected every row. In that aggregate
each aggregator answers for no rows: ``max`` gives ``None`` and ``list`` gives
``[]``.

**The mode is the annotator's, not the reads'.** An allele score also declares
``allele_score_mode``, ``substitutions`` or ``alleles``, reported by
:meth:`~gain.genomic_resources.genomic_scores.AlleleScore.substitutions_mode`
and :meth:`~gain.genomic_resources.genomic_scores.AlleleScore.alleles_mode`.
No read of the plane consults it: an exact-allele read answers an indel's own
row, or ``None``, on either kind of resource. The mode tells the allele score
annotator how to route a ``VCFAllele``. On a ``substitutions`` resource only a
substitution is matched exactly, and any other allele (insertion, deletion,
complex) is reduced over the bases it covers; on an ``alleles`` resource every
allele is matched exactly. The annotator reads ``allele_multiplicity`` too: it
matches an allele with the bare read on a ``one`` resource and with
:meth:`~gain.genomic_resources.genomic_scores.AlleleScore.get_allele_scores_for_allele_agg`
on a ``many`` resource, folding each attribute with its aggregator.

API
---

The classes below are defined in the module
``gain.genomic_resources.genomic_scores.allele``, and the package
``gain.genomic_resources.genomic_scores`` exports them. ``AlleleScore`` is also
exported by ``gain.grr``, as ``grr.AlleleScore``.

.. currentmodule:: gain.genomic_resources.genomic_scores

.. autosummary::

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

.. autoclass:: AlleleScore
   :members:

.. autoclass:: AlleleAggregate
   :members:

.. autoclass:: AlleleEntry
   :members:

.. autoclass:: AlleleMultiplicityError
   :members:

.. autofunction:: allele_key
