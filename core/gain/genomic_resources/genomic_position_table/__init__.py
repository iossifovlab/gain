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
