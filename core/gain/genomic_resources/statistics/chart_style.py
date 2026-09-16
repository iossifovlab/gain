"""Where the typography shared by the statistics charts is set.

Three modules draw charts of a resource's statistics: ``histogram`` (the
number and categorical histograms) and, in this package, ``alleles`` (the
complex-allele grid) and ``length_histogram`` (the length ladder).  They
agree on a label font size so the charts read as one set on the resource
page, and this module is where that agreement lives.

It is a leaf on purpose.  ``histogram`` builds on this package -- it
imports the base class, the min/max statistic and this module -- so the
constant it used to define was a statistics-chart contract kept in the
package's client, and the two charts here read it back out of that client
with function-local imports (gain#1486).  A leaf that imports nothing from
``gain`` is a place both tiers may reach with a plain module-level import,
and the architecture tests keep it one: a module ``histogram`` imports
that imported ``histogram`` back would be a genuine cycle.
"""

#: The label font size the statistics charts share.  Each chart applies
#: it to its axis labels, and to whichever tick or colour-bar labels it
#: sizes explicitly.
HISTOGRAM_LABELS_FONT_SIZE = 20
