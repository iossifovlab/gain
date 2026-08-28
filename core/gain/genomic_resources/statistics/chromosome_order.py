"""Natural ordering of contig names, for the info page's tables.

A genomic score's statistics are keyed by whatever the scanned table
called its contigs, and a plain string sort puts ``chr10`` twenty rows
above ``chr2``.  This module turns a contig name into a sort key that
orders its digit runs numerically, so the per-chromosome tables read the
way a human expects without any interaction.

The key is deliberately genome-agnostic: no hardcoded human contig
table, nothing read from a reference genome.  It is therefore NOT
karyotypic -- ``chrM`` sorts before ``chrX``, and alt/random contigs
interleave with the primary ones by their embedded numbers rather than
grouping at the end.  See iossifovlab/gain#982 for why that is the
accepted trade.
"""
from __future__ import annotations

import re

#: A maximal run of digits, captured so that :meth:`re.Pattern.split`
#: yields text and digit runs alternately, text first.
_DIGIT_RUN = re.compile(r"([0-9]+)")

#: Width of the digit-count prefix each digit run carries.  Two digits
#: order every run up to 99 digits long, which no contig name reaches.
_LENGTH_WIDTH = 2


def _numeric_run_key(run: str) -> str:
    """One digit run as its value behind that value's digit count."""
    digits = str(int(run))
    return f"{len(digits):0{_LENGTH_WIDTH}d}{digits}"


def natural_chromosome_key(chrom: str) -> str:
    """Return a plain string ordering ``chrom`` by its digit runs.

    Orderable by ``<`` alone -- there is no tuple and no comparator, so
    the same key can be handed to a template as a sort attribute.

    Each digit run is normalised to its numeric value prefixed by that
    value's digit count, which is what makes a shorter number sort
    first: ``2`` becomes ``012`` and ``10`` becomes ``0210``.  A run's
    leading zeros are dropped, so ``chr01`` and ``chr1`` share a key.
    Everything else is lowercased, so case never decides an order.

    A digit-count prefix rather than the zero-padding to a fixed width
    the issue sketched: padding silently mis-orders any run wider than
    the width it was given, and there is no width a contig name is
    guaranteed to fit.
    """
    return "".join(
        _numeric_run_key(part) if index % 2 else part.lower()
        for index, part in enumerate(_DIGIT_RUN.split(chrom))
    )
