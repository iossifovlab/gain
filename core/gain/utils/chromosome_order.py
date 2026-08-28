"""Natural ordering of contig names, for the info pages' tables.

A genomic score's statistics are keyed by whatever the scanned table
called its contigs, and a plain string sort puts ``chr10`` twenty rows
above ``chr2``.  This module turns a contig name into a sort key that
orders its digit runs numerically, so a per-chromosome table reads the
way a human expects with no interaction.

The key is deliberately genome-agnostic: no hardcoded human contig
table, nothing read from a reference genome.  It is therefore NOT
karyotypic -- ``chrM`` sorts before ``chrX``, and alt/random contigs
interleave with the primary ones by their embedded numbers rather than
grouping at the end.  See iossifovlab/gain#982 for why that is the
accepted trade.

It lives beside the other contig helpers rather than under
``genomic_resources/statistics/`` because it is genome-naming
knowledge, not statistics: the reference genome and gene models pages
carry per-chromosome tables of their own, and iossifovlab/gain#984
wants this key at the template layer, which imports nothing from
``genomic_resources`` today.

Unrelated to ``CategoricalHistogramConfig.natural_order``, which orders
a histogram's categories -- ints before strings, then lexicographic --
and knows nothing of digit runs.
"""
from __future__ import annotations

import re

#: A maximal run of digits.
_DIGIT_RUN = re.compile(r"[0-9]+")


def _numeric_run_key(run: re.Match[str]) -> str:
    """One digit run as its digits behind a count of them.

    The count is what makes a shorter number sort first -- ``2`` becomes
    ``012`` and ``10`` becomes ``0210`` -- and two digits of count order
    every run up to 99 digits long, past anything a contig name reaches.

    Leading zeros are dropped by string surgery rather than a round trip
    through :class:`int`, which keeps the run's own width irrelevant.
    """
    digits = run.group().lstrip("0") or "0"
    return f"{len(digits):02d}{digits}"


def natural_chromosome_key(chrom: str) -> str:
    """Return a plain string ordering ``chrom`` by its digit runs.

    Orderable by ``<`` alone -- no tuple and no comparator -- so the
    same key can be emitted as a template sort attribute, which is what
    iossifovlab/gain#984 will want it for.

    Digit runs are keyed by :func:`_numeric_run_key` and everything else
    is lowercased, so case never decides an order.  A digit-count prefix
    rather than the fixed-width zero padding the issue sketched, for two
    reasons: padding silently mis-orders any run wider than the width it
    was given, and the separator that sketch used would sort ``chrM``
    ahead of ``chr22``, failing the issue's own criterion.

    Dropping leading zeros leaves the key non-injective.  ``chr01`` and
    ``chr1`` share one deliberately; ``chrUn_GL000195v1`` would share
    one with a hypothetical ``chrUn_GL195v1`` less deliberately.  Ties
    fall through to the sorter, which is stable.
    """
    return _DIGIT_RUN.sub(_numeric_run_key, chrom.lower())
