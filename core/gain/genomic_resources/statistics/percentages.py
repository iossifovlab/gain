"""How a share of a whole is written, for every table that writes one.

One rule in one place (gain#1057).  The info page carries two tables
that each render a count as a percentage of a total -- Coverage, whose
denominator is a chromosome's length, and Alleles, whose denominator is
the allele total -- and before this module they formatted it two ways:
the Alleles section through :func:`percentages_over`'s floor and
ceiling, the Coverage section through a bare ``"%.2f%%"`` inline in the
template.  So one page could read ``100.00%`` in Coverage and
``>99.99%`` in Alleles of the identical shape.

The rule is deliberately SCALAR -- one (count, total) pair to one
string.  What varies between the callers is the contract around a
MISSING denominator, and that is theirs to keep.  Coverage resolves a
denominator per row and degrades only that row; so, since gain#1118,
do the Alleles table's per-class share columns, whose denominator is
each chromosome's own allele count.  The whole-map answer -- no total,
no column at all -- is still :func:`percentages_over`'s, and still what
the substitution matrix and the complex table ask.

The REFERENCE GENOME page's nucleotide distributions are a third table
of this shape and do not come through here yet: they are stored already
multiplied out, so the counts this rule needs to decide exactness on
are gone before a template sees them.  gain#1086 holds that.
"""
from __future__ import annotations


def percentage_of(count: int, total: int) -> str:
    """``count`` as a percentage of ``total``, floored and capped.

    The result is text for an HTML page and can carry markup-significant
    characters -- the floor and the ceiling begin with ``<`` and ``>``
    -- so a template rendering it must escape, which the ``.jinja`` HTML
    templates do and the Markdown ones deliberately do not.

    ``total`` must be positive; a share of nothing is not a percentage,
    and what to show instead is the caller's question rather than this
    one's (see the module docstring).

    Two answers a bare ``"%.2f%%"`` gets wrong:

    * A nonzero count too small to survive two decimals renders
      ``<0.01%``, never ``0.00%``.  On a real score ``complex`` is 881
      alleles out of 727,413,443 while ``other`` is genuinely empty --
      and telling those two apart is the whole reason a percentage is
      shown at all.
    * A count that falls SHORT of the total but rounds up to it renders
      ``>99.99%``, never ``100.00%`` -- the floor reflected (gain#990).
      On that same score the substitutions are all but 881 of the
      alleles, and a column reading ``substitution 100.00%`` beside
      ``complex <0.01%`` says the resource is entirely one class in the
      act of showing that it is not.

    Both boundaries are decided on the INTEGERS, never on the rendered
    float: a count that IS the total renders ``100.00%`` and a count of
    zero renders ``0.00%``, because only a share that is not the whole
    is written as short of it, and only a share that exists at all is
    written as too small to see.
    """
    rendered = f"{100.0 * count / total:.2f}%"
    if count and rendered == "0.00%":
        return "<0.01%"
    if count < total and rendered == "100.00%":
        return ">99.99%"
    return rendered
