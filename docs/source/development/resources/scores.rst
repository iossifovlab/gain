Genomic scores
==============

A genomic score attaches values to places in the genome. GAIn has three kinds,
and which one a resource is decides what "a place" means:

.. list-table::
   :header-rows: 1
   :widths: 20 30 50

   * - Kind
     - A record is
     - Typical resource
   * - :class:`~gain.genomic_resources.genomic_scores.PositionScore`
     - one position
     - conservation tracks such as ``phastCons100way``
   * - :class:`~gain.genomic_resources.genomic_scores.AlleleScore`
     - one substitution or allele at a position
     - variant-level scores such as ``CADD``
   * - :class:`~gain.genomic_resources.genomic_scores.FragmentScore`
     - an interval carrying attributes
     - CNV collections, ATAC fragments, region annotations

All three are built by the same function, which reads the resource's declared
type and returns the matching class:

.. code-block:: python

    from gain.genomic_resources.repository_factory import build_genomic_resource_repository
    from gain.genomic_resources.genomic_scores import build_score_from_resource_id

    grr = build_genomic_resource_repository()
    score = build_score_from_resource_id("hg38/scores/phastCons100way", grr)

They share the base class
:class:`~gain.genomic_resources.genomic_scores.GenomicScore`, which owns
everything that does not depend on what a record means: the lifecycle, the
score definitions, the filter compiler, and the raw record and column-array
reads.

The lifecycle
-------------

A score holds an open table — a tabix file, a bigWig, or a VCF — so it is
built closed. :meth:`~gain.genomic_resources.genomic_scores.GenomicScore.open`
opens it and returns it, so the call chains, and
:meth:`~gain.genomic_resources.genomic_scores.GenomicScore.is_open` reports
the state.

**Entering the context manager does not open the score.** ``__enter__``
returns the score unchanged; what the ``with`` block gives you is the
guaranteed :meth:`~gain.genomic_resources.genomic_scores.GenomicScore.close`
on the way out. So call ``open()`` explicitly and let ``with`` manage the
closing:

.. code-block:: python

    with build_score_from_resource_id("hg38/scores/phastCons100way", grr).open() as score:
        print(score.get_scores_at_position("chr21", 5_030_000))

Omitting the ``.open()`` raises ``ValueError: genomic score <id> is not
open`` on the first read.

This is stated once here and holds for all three kinds. Reading from a closed
score is an error, not an implicit open — a score that opened itself on first
use would make the cost of the first read unpredictable, and would hide a
missing ``close`` in long-running code.

Position scores
---------------

:class:`~gain.genomic_resources.genomic_scores.PositionScore` is the kind
whose reads are per-base.
:meth:`~gain.genomic_resources.genomic_scores.PositionScore.get_scores_at_position`
returns one tuple for one position;
:meth:`~gain.genomic_resources.genomic_scores.PositionScore.get_scores_in_region`
yields one tuple per position of an interval. Both return ``None`` in the
slot of a score that has no value there, which is how "uncovered" is
distinguished from a genuine zero:

.. code-block:: python

    for values in score.get_scores_in_region("chr21", 5_030_000, 5_030_010):
        print(values)

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

Allele scores
-------------

:class:`~gain.genomic_resources.genomic_scores.AlleleScore` has a plane of
reads in the same style as the position reads above and the fragment reads
below. Each read takes its locus positionally and everything else as a
keyword, and has a singular form for one score; what it answers depends on its
reduction, as described after the table. The reads sit on a grid: three *loci*
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
below. The ``reserved`` cells have a name and a meaning but no method yet.

.. code-block:: python

    with build_score_from_resource_id("hg38/scores/CADD_v1.7", grr).open() as score:
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

Fragment scores
---------------

:class:`~gain.genomic_resources.genomic_scores.FragmentScore` reads intervals.
Its method names say exactly which relation to the query region they use —
``overlapping_region`` for fragments that intersect it,
``starting_in_region`` for fragments that begin inside it, ``at_position``
for those covering one point. The distinction matters: summing a score over
overlapping fragments double-counts a fragment that straddles two adjacent
query windows, and the ``starting_in`` form is the one that tiles.

The allele and fragment kinds both have a region fold —
:meth:`~gain.genomic_resources.genomic_scores.AlleleScore.get_allele_scores_in_region_agg`
and
:meth:`~gain.genomic_resources.genomic_scores.FragmentScore.get_fragment_scores_overlapping_region_agg`
— that answers off a single walk of the region and returns a small record
rather than a bare tuple —
:class:`~gain.genomic_resources.genomic_scores.AlleleAggregate` and
:class:`~gain.genomic_resources.genomic_scores.FragmentAggregate`. Each
carries the reduced ``values`` alongside what the walk saw (the matched
allele keys, or the fragment count), because the two halves are only
guaranteed to agree when they come off the same walk.

In every case ``values`` is parallel to the *queries* that were asked, not
keyed by score id — one score asked twice with two aggregators is two
queries and therefore two values.

Common to all genomic scores
----------------------------

Score definitions
~~~~~~~~~~~~~~~~~

A score resource declares its columns in a ``scores:`` block, documented as
YAML on :ref:`grr-position-scores` and the sibling sections for allele and
fragment scores. At runtime each entry of that block becomes one
:class:`~gain.genomic_resources.score_def.GenomicScoreDef` — the object that
knows a score's id, its value type, how to turn a raw cell into a value, and
which histogram configuration belongs to it.

That is the boundary: the *keys* are described on :doc:`/grr`, the *object*
here.

.. code-block:: python

    for score_def in score.score_definitions.values():
        print(score_def.score_id, score_def.value_type)

    one = score.get_score_definition("phastCons100way")

:meth:`~gain.genomic_resources.score_def.GenomicScoreDef.parse_value` and
:meth:`~gain.genomic_resources.score_def.GenomicScoreDef.parse_array` are the
scalar and vectorised forms of the same conversion; the array form is what
the column-array reads use, and it is the reason a large region can be read
without building one Python object per row.

Filtering
~~~~~~~~~

:meth:`~gain.genomic_resources.genomic_scores.GenomicScore.compile_filter`
turns a boolean expression over a score's own columns into a
:class:`~gain.genomic_resources.score_filter.ScoreFilter`, which the reads
accept as a ``score_filter`` argument and apply while walking:

.. code-block:: python

    keep = score.compile_filter("phastCons100way > 0.9")
    for record in score.fetch_records("chr21", 5_030_000, 5_040_000, score_filter=keep):
        print(record)

A filter is bound to the score that compiled it and refuses to run against
another — expressions name columns, and the same column name on a different
resource is a different column.

Bulk reads
~~~~~~~~~~

:meth:`~gain.genomic_resources.genomic_scores.GenomicScore.fetch_records`
yields one record per row and is the general form.
:meth:`~gain.genomic_resources.genomic_scores.GenomicScore.fetch_region_value_arrays`
is the fast path: it returns whole columns as arrays instead of a record per
row, for the scores and backends that support it. Ask
:meth:`~gain.genomic_resources.genomic_scores.GenomicScore.supports_region_value_arrays`
first — it answers for a specific list of scores, because support depends on
the value types requested and not only on the backend.

Chromosomes and their lengths
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:meth:`~gain.genomic_resources.genomic_scores.GenomicScore.get_all_chromosomes`
lists the contigs the score's table holds, in table order, and
:meth:`~gain.genomic_resources.genomic_scores.GenomicScore.has_chromosome`
answers for one. Their lengths come from several *sources*, which a
:class:`~gain.genomic_resources.genomic_position_table.ChromLengthSource`
names: the ``reference_genome`` the resource is labelled with, a bigWig's
header, the tabix index probe (an upper bound, not the length), or how far
an in-memory table's rows reach. A score answers from the best-ranked
source by default, from a named one on request, and says which one
answered:

.. code-block:: python

    with build_score_from_resource_id("hg38/scores/phastCons100way", grr).open() as score:
        score.chrom_length_sources
        # [<ChromLengthSource.REFERENCE_GENOME: 'reference_genome'>,
        #  <ChromLengthSource.TABIX_ESTIMATE: 'tabix_estimate'>]
        score.get_chrom_length("chr21")                # the genome's, exact
        score.get_chrom_length_source("chr21")         # ChromLengthSource.REFERENCE_GENOME
        score.get_chrom_length("chr21", source="tabix_estimate")   # the probe's bound
        score.get_all_chrom_lengths(source="reference_genome")     # only the contigs the genome lists

The genome's lengths reach the score through the
``statistics/chrom_lengths.json`` its last repair stored; a resource
repaired before that file existed answers from its table alone until its
next ``grr_manage repo-repair``, and asking it for ``reference_genome``
raises ``ValueError`` saying so. Every refusal — a closed score, a contig
the score does not carry, a source with no answer for it, a contig no
source can measure — is a ``ValueError``.

API
---

.. currentmodule:: gain.genomic_resources.genomic_scores

.. autofunction:: build_score_from_resource_id

.. autoclass:: GenomicScore
   :members:

.. autoclass:: PositionScore
   :members:

.. autoclass:: AlleleScore
   :members:

.. autoclass:: FragmentScore
   :members:

.. autoclass:: AlleleAggregate
   :members:

.. autoclass:: AlleleEntry
   :members:

.. autoclass:: AlleleMultiplicityError
   :members:

.. autofunction:: allele_key

.. autoclass:: FragmentAggregate
   :members:

.. currentmodule:: gain.genomic_resources.score_def

.. autoclass:: GenomicScoreDef
   :members:

.. currentmodule:: gain.genomic_resources.score_filter

.. autoclass:: ScoreFilter
   :members:
