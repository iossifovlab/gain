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
**Changed extension point: a backend now implements
``_load_file_chromosomes``, not ``get_file_chromosomes``.**  Neither name is in
``__all__`` below, so this breaks no public name of ``gain`` -- it is recorded
here because ``get_file_chromosomes`` was an *abstract* method whose docstring
named it the thing "to be overwritten by the subclass", which makes it the
documented way to write a backend, and an out-of-tree backend that overrides
the old name now fails to instantiate (the new abstract hook is unimplemented).
No such backend exists anywhere in the stack; every in-tree one was migrated
with the change.

``get_file_chromosomes`` still exists, unchanged in name, signature and
meaning.  What changed is that it is now CONCRETE on ``GenomicPositionTable``,
memoising per instance over the new hook.  It carried ``functools.cache``
before, which keyed a class-level memo by ``self`` and so pinned every table
that was ever opened for the life of the process -- unbounded growth under
``grr_manage resource-repair``, which builds one table per region task
(gain#345).  A backend migrates by renaming its override and deleting the
decorator; it must not memoise on its own, since the base class now does.

**Changed contract: a CLOSED table refuses reads.**
``TabixGenomicPositionTable``, ``BigWigTable`` and ``VCFGenomicPositionTable``
are in ``__all__`` below, so this is a change to public names of ``gain`` --
recorded here for the same reason as the removals above, because nothing else
records it.  #350 made
``close()`` release everything a table read out of its file, the contig order,
the chromosome map and the ``get_file_chromosomes`` memo included, and the
reads that used to be answered out of that retained state now fail instead.
Three of them fail in one stated way, ``ValueError``, on all four backends:
``get_chromosomes()``, ``get_file_chromosomes()`` and
``get_chromosome_length()``.  Those three are what an out-of-tree caller may
write an ``except ValueError`` around.  **The record reads
(``get_all_records()``, ``get_records_in_region()``) refuse as well, but their
exception type is NOT part of the contract**: neither carries a not-open guard
of its own, so measured on a closed table some backend/method pairs raise the
same ``ValueError`` on their way through ``get_chromosomes()`` while the rest
run into a pre-existing ``assert`` in the fetch path and raise a message-less
``AssertionError`` -- or, under ``python -O``, which strips asserts, whatever
the next line makes of the released state (``AttributeError`` on ``None``,
``KeyError`` off an emptied contig dict).  Those asserts are older than this
contract and were left alone; do not catch on them, and do not read the
uniformity of the first three as covering the record reads.  For an
out-of-tree caller all of it is the difference between an answer and an
exception; the migration is to
read inside the open table's lifetime, or to reopen -- ``open()``
re-establishes all of it, and a reopened table answers exactly as one that was
never closed.  Nothing in-tree was affected, which is why the ledger entry is
the whole mitigation and there is no shim: every in-repo read sits behind
``GenomicScore.is_open()``, and ``gpf`` has no non-test caller of
``get_chromosomes()``/``get_file_chromosomes()`` at all.

#358 then made that contract UNIFORM rather than changing it again, in the two
places the backends disagreed.  ``InmemoryGenomicPositionTable`` answered a
closed ``get_file_chromosomes()`` with ``[]`` -- the scanned-contig list its
``close()`` empties -- and the base class's memo cached that empty answer for
the rest of the table's life; ``BigWigTable`` refused with a bare ``assert``,
which ``python -O`` strips, leaving it answering ``[]`` from an emptied contig
dict.  Both now raise the ``ValueError`` the tabix and VCF backends already
raised, so a caller catches one thing from all four (and a closed VCF table
refuses for a never-opened one's reason too: its guard is the absent handle,
which covers both states).  ``get_chromosome_length`` was brought into line the
same way in the two backends that were not: the in-memory one used to raise out
of the middle of a message it could not finish building -- every contig takes
the no-records branch on a closed table, and that branch interpolates
``get_chromosomes()`` -- and the bigWig one guarded with the same bare
``assert``, which under ``python -O`` let a closed table fall through into that
very branch.  Both now say the table is not open, as tabix and VCF already did.

**Deliberately NOT changed: ``map_chromosome``/``unmap_chromosome`` pass
through on a closed table.**  Both return their argument unchanged when the
chromosome map is ``None``, and ``close()`` sets it to ``None`` -- so a closed
*mapped* table hands reference-space names back as if they were the file's,
which is the one closed-table read that answers instead of refusing.  It stays
that way because the state cannot tell the two cases apart:
``_build_chrom_mapping`` sets ``chrom_map = None`` on an OPEN table that
configures no mapping, so the field does not distinguish closed from
mapping-free, and a table carries no open/closed flag.  Introducing one would
put a new invariant on every backend and a new failure on the read path, which
is exactly what #350 avoided.  Named here so the ambiguity is a decision on
record rather than something a caller has to rediscover; the same note is on
``GenomicPositionTable.close``.

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
