"""Genomic position table backends.

**Removed export: ``VCFLine``.**  It was in this package's ``__all__`` and so a
public name of ``gain``; #237 deleted it, because the VCF backend no longer
builds a per-line object at all -- it yields records (``record.py``), like every
other record backend.  This is a breaking export change, recorded here because
nothing else records it: an importer of ``VCFLine`` now gets an ImportError.
There is no drop-in replacement object, and none is wanted -- a VCF line is a
record tuple, and what used to be read off a ``VCFLine`` is read from the
record's slots (``CHROM`` ... ``ALT``) or, for scores, through the score layer's
``VCFScoreLine``.

**Removed exports: ``Line`` and ``BigWigLine``** (and, with them, the
``LineBase`` protocol they satisfied and the ``row()`` method all three
declared).  Both were in this package's ``__all__``, so both are public names of
``gain``, and an importer of either now gets an ImportError -- the same breaking
change as ``VCFLine``, recorded here for the same reason.  With ``VCFLine``
already gone in #237, these were the last two of the three line adapters this
package ever exported.  #239 deleted them once #238 had migrated bigWig, the
last backend that built one: every backend now yields records, so nothing
constructed a line adapter and nothing consumed one.  ``LineBase`` went with
them because a protocol with no implementors describes nothing, and ``row()``
-- which serialised an adapter back to its raw row -- went because its only
caller, ``save_as_tabix_table``, was itself dead and was deleted in #235.

The score layer's adapter-era ``ScoreLine`` was deleted by #239 too, but it was
never exported from this package and was never an adapter itself -- it *wrapped*
one, asserting its line was a ``Line`` or a ``BigWigLine``.  That assert is why
it could not outlive them.  It has no bearing on this package's exports; a score
caller goes through ``RecordScoreLine``/``VCFScoreLine`` (see below).

There is no replacement and no deprecation shim.  A shim was considered and
rejected: it costs nothing to anyone who does not call it, but hands anyone who
*does* call it back the exact per-line allocation this whole migration exists to
remove.  A caller reading coordinates off a ``Line`` reads them from the
record's slots instead (``record[CHROM]``, ``record[POS_BEGIN]``,
``record[POS_END]``, ``record[REF]``, ``record[ALT]``); a caller using
``line.get(key)`` for a column indexes the record's payload
(``record[PAYLOAD][key]``); a caller wanting the whole raw row back takes
``tuple(record[PAYLOAD])``.  For scores, none of this is the intended route at
all -- go through the score layer's ``RecordScoreLine``/``VCFScoreLine``, which
read the same slots and additionally handle NA values, parsing and aggregation.

**The ``tuple()`` around that last one is the migration, not noise.**
``row()`` returned ``tuple(self._data)`` in both adapters -- an immutable
snapshot of the row, taken there and then.  ``record[PAYLOAD]`` is not that:
the payload is the backend's row held **by reference**, deliberately neither
copied nor frozen (``record.py``), and for the tabix backend it is a
``pysam.TupleProxy`` that pysam reuses as the fetch advances and that
``LineBuffer`` may still be holding.  Retain it past the iteration and its
cells are whatever pysam has since put there; write to it and you mutate a
buffered row (see ``line.py``).  ``tuple(record[PAYLOAD])`` reproduces what
``row()`` handed back, and is what a ``row()`` caller migrates to.

**``fchrom`` has no record equivalent, and is the one ``LineBase`` attribute
with no slot to move to.**  There is no FCHROM slot, and ``record[CHROM]`` is
NOT one: ``Line`` carried the file's own contig in ``fchrom`` and the
reference contig in ``chrom``, and under a configured ``chrom_mapping`` those
hold *different* values -- the tabix backend overwrote ``chrom`` with the
mapped reference contig and left ``fchrom`` at the file's.  A record's CHROM
slot is the mapped one, so migrating ``line.fchrom`` to ``record[CHROM]`` is
not an error, it is wrong data, on exactly the tables that configure a map.

The file contig is still readable -- but from the **table**, which is why it is
not a slot.  For the tabular backends it is ``record[PAYLOAD][table.chrom_key]``
(the raw row's contig cell -- literally the expression ``Line.__init__`` read
its own ``fchrom`` from), or ``table.unmap_chromosome(record[CHROM])`` back
through the map.  Both need the table, and a caller holding records has one.
Adding a sixth decoded slot to spare it that is not on the table here: a record
is what *every* backend yields, and a file contig is not something every
backend has to give.  bigWig's payload repeats the already-mapped reference
contig (``BigWigLine.fchrom`` was set from it -- that adapter's ``fchrom`` was
never a file contig at all), and a VCF record's payload is a variant, whose
file contig is ``record[PAYLOAD][VARIANT].contig``.  The slot would mean three
different things, which is the sort of thing the five decoded slots exist to
not do.

``LineBuffer`` is NOT part of that removal and remains exported: it outlived the
adapters it used to hold and now buffers records (see its own note below).

**Removed method: ``LineBuffer.pop_first``.**  ``LineBuffer`` is in ``__all__``
below, so this too is a breaking change to a public name of ``gain``, recorded
here for the same reason.  It had no caller anywhere in the stack (gain or gpf)
and no replacement is wanted: #250 gave the buffer an invariant that a bare
``popleft`` cannot keep.  Eviction has to go through :meth:`LineBuffer.prune`,
which drops a record only when its ``pos_end`` has fallen below the query --
wherever that record sits (gain#287), not merely while it is at the head.  That
rule is what makes the buffer *complete* from the pruned-to position onwards,
and completeness is what the read path's buffer-hit answer rests on.
``pop_first``
dropped the leftmost record unconditionally, so it could evict one that still
overlapped later queries and leave the buffer answering from a hole -- silently,
and with no fall-through to the file to rescue it.  (It would also leave
``_max_end``/``_max_width`` stale, but only ever *high*, which is the harmless
direction -- see :class:`LineBuffer`.  The completeness break is the real one.)
"""
from .line import LineBuffer
from .table_bigwig import BigWigTable
from .table_tabix import TabixGenomicPositionTable
from .table_vcf import VCFGenomicPositionTable
from .utils import build_genomic_position_table

__all__ = [
    "BigWigTable",
    "LineBuffer",
    "TabixGenomicPositionTable",
    "VCFGenomicPositionTable",
    "build_genomic_position_table",
]
