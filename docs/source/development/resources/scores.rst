Scores
======

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

Reading positions
-----------------

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

Reading alleles and fragments
-----------------------------

:class:`~gain.genomic_resources.genomic_scores.AlleleScore` adds reads that
take a reference and an alternative as well as a position —
:meth:`~gain.genomic_resources.genomic_scores.AlleleScore.fetch_allele_scores`
is the direct one. An allele score is in one of two modes,
``substitutions`` or ``alleles``, reported by
:meth:`~gain.genomic_resources.genomic_scores.AlleleScore.substitutions_mode`
and :meth:`~gain.genomic_resources.genomic_scores.AlleleScore.alleles_mode`;
the mode decides whether an indel can match at all.

:class:`~gain.genomic_resources.genomic_scores.FragmentScore` reads intervals.
Its method names say exactly which relation to the query region they use —
``overlapping_region`` for fragments that intersect it,
``starting_in_region`` for fragments that begin inside it, ``at_position``
for those covering one point. The distinction matters: summing a score over
overlapping fragments double-counts a fragment that straddles two adjacent
query windows, and the ``starting_in`` form is the one that tiles.

Both kinds have an aggregating read that answers off a single walk of the
region and returns a small record rather than a bare tuple —
:class:`~gain.genomic_resources.genomic_scores.AlleleAggregate` and
:class:`~gain.genomic_resources.genomic_scores.FragmentAggregate`. Each
carries the reduced ``values`` alongside what the walk saw (the matched
allele keys, or the fragment count), because the two halves are only
guaranteed to agree when they come off the same walk.

In every case ``values`` is parallel to the *queries* that were asked, not
keyed by score id — one score asked twice with two aggregators is two
queries and therefore two values.

Score definitions
-----------------

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
---------

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
----------

:meth:`~gain.genomic_resources.genomic_scores.GenomicScore.fetch_records`
yields one record per row and is the general form.
:meth:`~gain.genomic_resources.genomic_scores.GenomicScore.fetch_region_value_arrays`
is the fast path: it returns whole columns as arrays instead of a record per
row, for the scores and backends that support it. Ask
:meth:`~gain.genomic_resources.genomic_scores.GenomicScore.supports_region_value_arrays`
first — it answers for a specific list of scores, because support depends on
the value types requested and not only on the backend.

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

.. autoclass:: FragmentAggregate
   :members:

.. currentmodule:: gain.genomic_resources.score_def

.. autoclass:: GenomicScoreDef
   :members:

.. currentmodule:: gain.genomic_resources.score_filter

.. autoclass:: ScoreFilter
   :members:
