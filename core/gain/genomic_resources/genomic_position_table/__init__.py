"""Genomic position table backends.

**Removed export: ``VCFLine``.**  It was in this package's ``__all__`` and so a
public name of ``gain``; #237 deleted it, because the VCF backend no longer
builds a per-line object at all -- it yields records (``record.py``), like every
other record backend.  This is a breaking export change, recorded here because
nothing else records it: an importer of ``VCFLine`` now gets an ImportError.
There is no drop-in replacement object, and none is wanted -- a VCF line is a
record tuple, and what used to be read off a ``VCFLine`` is read from the
record's slots (``CHROM`` ... ``ALT``) or, for scores, through the score layer's
``VCFScoreLine``.  ``Line``/``BigWigLine`` remain, now unused by any backend --
#238 migrated bigWig, the last one, to records -- and only because removing the
adapter machinery entirely is #239.

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
from .line import BigWigLine, Line, LineBuffer
from .table_bigwig import BigWigTable
from .table_tabix import TabixGenomicPositionTable
from .table_vcf import VCFGenomicPositionTable
from .utils import build_genomic_position_table

__all__ = [
    "BigWigLine",
    "BigWigTable",
    "Line",
    "LineBuffer",
    "TabixGenomicPositionTable",
    "VCFGenomicPositionTable",
    "build_genomic_position_table",
]
