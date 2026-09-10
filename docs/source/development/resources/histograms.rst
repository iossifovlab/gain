Histograms
==========

A histogram summarises how one score is distributed. GAIn computes histograms
as part of a resource's *statistics* build, stores them in the resource, and
renders them on the resource's HTML summary page. In Python they are the
objects you get back when you read that stored statistic.

Three kinds, and two aliases
----------------------------

There is one histogram class per kind of distribution, and one configuration
class for each:

.. list-table::
   :header-rows: 1
   :widths: 34 33 33

   * - Kind
     - Histogram
     - Configuration
   * - continuous, binned
     - :class:`~gain.genomic_resources.histogram.NumberHistogram`
     - :class:`~gain.genomic_resources.histogram.NumberHistogramConfig`
   * - discrete, counted per value
     - :class:`~gain.genomic_resources.histogram.CategoricalHistogram`
     - :class:`~gain.genomic_resources.histogram.CategoricalHistogramConfig`
   * - none — deliberately not computed
     - :class:`~gain.genomic_resources.histogram.NullHistogram`
     - :class:`~gain.genomic_resources.histogram.NullHistogramConfig`

``Histogram`` and ``HistogramConfig`` are **type aliases**, not classes:

.. code-block:: python

    Histogram = NullHistogram | CategoricalHistogram | NumberHistogram
    HistogramConfig = (
        NullHistogramConfig | CategoricalHistogramConfig | NumberHistogramConfig
    )

They are what the annotating signatures are written in terms of, so a
function documented as returning a ``Histogram`` returns one of the three
concrete classes above. Because they are aliases there is nothing to
``autoclass`` and no page anchor to link to — check ``type`` on the object,
or match on the class, to find out which one you have.

The null histogram is not an absence
------------------------------------

:class:`~gain.genomic_resources.histogram.NullHistogram` is a real object
that records *why* no histogram exists — a score whose values cannot be
binned, a statistics build that was told to skip it, or a read that failed.
The reason is a plain string — ``NullHistogramConfig.reason``, copied onto
the histogram as ``NullHistogram.reason`` — and it is required, not optional:
there is no such thing as an unexplained null histogram, though
:meth:`~gain.genomic_resources.histogram.NullHistogramConfig.default_config`
supplies ``"Unspecified reason"`` for the few callers that genuinely have
nothing better to say.

A null histogram answers the same interface as the other two, so consumers do
not branch; its
:meth:`~gain.genomic_resources.histogram.NullHistogram.plot` draws nothing
and the summary page renders the reason instead.

This is why
:func:`~gain.genomic_resources.histogram.load_histogram` never raises for a
missing or malformed histogram: it returns an appropriately-explained
``NullHistogram``.

.. code-block:: python

    from gain.genomic_resources.histogram import load_histogram

    res = grr.get_resource("hg38/scores/phastCons100way")
    hist = load_histogram(res, "statistics/histogram_phastCons100way.yaml")
    print(hist.type, hist.values_domain())

Configuration
-------------

The ``histogram:`` block of a score's ``scores:`` entry is a curator-facing
YAML question, and its keys — bin counts, log scales, view ranges, value
ordering, and the custom ``plot_function`` hook — are documented on
:ref:`grr-histogram-configuration`. That section also carries the one Python
example that belongs on the user page: how to write a ``plot_function``.

What that block parses into is a ``HistogramConfig``, and that is described
here. The chain is short and worth knowing, because it explains where a
default comes from when the YAML says nothing:

#. ``parse_scoredef_config`` reads the ``scores:`` block and produces one
   :class:`~gain.genomic_resources.score_def.GenomicScoreDef` per entry.
#. For each entry, ``build_histogram_config`` reads that entry's
   ``histogram:`` key and returns the matching ``*HistogramConfig`` —
   or, when the key is absent, ``default_config`` on the class the score's
   value type implies.
#. The statistics build uses the config to construct the histogram, fills it
   with :meth:`~gain.genomic_resources.histogram.NumberHistogram.add_value`
   or the vectorised
   :meth:`~gain.genomic_resources.histogram.NumberHistogram.add_batch`, and
   stores the serialised result in the resource.
#. :func:`~gain.genomic_resources.histogram.load_histogram` reads it back.

Merging is what makes step 3 parallelisable: each task histograms a slice of
the genome and
:meth:`~gain.genomic_resources.histogram.NumberHistogram.merge` folds the
partial results together. A merge requires compatible configurations — two
number histograms with different bin edges cannot be added — so the
configuration is fixed before the build starts, not derived from the data as
it arrives.

Truncation
----------

:class:`~gain.genomic_resources.histogram.CategoricalHistogram` counts one
bucket per distinct value, so it needs a bound:
``UNIQUE_VALUES_LIMIT`` caps how many it will track, and
:attr:`~gain.genomic_resources.histogram.CategoricalHistogram.unique_values`
and
:attr:`~gain.genomic_resources.histogram.CategoricalHistogram.total_count`
are reported *across* truncation, so a truncated histogram still tells you
how much it did not keep.
:attr:`~gain.genomic_resources.histogram.CategoricalHistogram.display_values`
is the ordered subset the summary page draws, which the configuration's
``displayed_values_count`` and ``value_order`` keys control.

API
---

.. currentmodule:: gain.genomic_resources.histogram

.. autofunction:: load_histogram

.. autoclass:: NumberHistogram
   :members:

.. autoclass:: CategoricalHistogram
   :members:

.. autoclass:: NullHistogram
   :members:

.. autoclass:: NumberHistogramConfig
   :members:

.. autoclass:: CategoricalHistogramConfig
   :members:

.. autoclass:: NullHistogramConfig
   :members:
