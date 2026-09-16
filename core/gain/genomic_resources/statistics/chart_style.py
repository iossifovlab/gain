"""Where the style shared by the statistics charts is set.

``histogram`` draws the number and categorical histograms; in this
package, ``alleles`` draws the complex-allele grid and ``length_histogram``
the length ladder.  The charts agree on their label font so they read as
one set on the resource page, and this module is where that agreement
lives -- a leaf that imports nothing from ``gain``, so both tiers reach it
with a module-level import.  ``test_architecture`` keeps it one and says
why (gain#1486).
"""

#: Label font size shared by the statistics charts.
CHART_LABEL_FONT_SIZE = 20
