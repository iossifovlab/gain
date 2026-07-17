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
(``record[PAYLOAD][key]``); a caller wanting the whole raw row back takes the
payload itself (``record[PAYLOAD]``), which is what ``row()`` reconstructed a
copy of.  For scores, none of this is the intended route at all -- go through
the score layer's ``RecordScoreLine``/``VCFScoreLine``, which read the same
slots and additionally handle NA values, parsing and aggregation.

``LineBuffer`` is NOT part of that removal and remains exported: it outlived the
adapters it used to hold and now buffers records (see its own note below).

**Removed method: ``LineBuffer.pop_first``.**  ``LineBuffer`` is in ``__all__``
below, so this too is a breaking change to a public name of ``gain``, recorded
here for the same reason.  It had no caller anywhere in the stack (gain or gpf)
and no replacement is wanted: #250 gave the buffer an invariant that a bare
``popleft`` cannot keep.  Eviction has to go through :meth:`LineBuffer.prune`,
which stops at the first record whose ``pos_end`` reaches the query -- that gate
is what makes the buffer *complete* from the pruned-to position onwards, and
completeness is what the read path's buffer-hit answer rests on.  ``pop_first``
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
