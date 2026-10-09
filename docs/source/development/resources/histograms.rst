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

They are the names the type annotations use, so a function documented as
returning a ``Histogram`` returns one of the three concrete classes above.
Because they are aliases there is nothing to ``autoclass`` and no page anchor
to link to — check ``type`` on the object, or match on the class, to find out
which one you have.

.. _histograms-reading-number:

Reading a number histogram
--------------------------

``ScoreResource.get_score_histogram(score_id)`` reads the stored histogram of
one score. Genomic scores and gene scores share it. It returns the
``Histogram`` union, so the result can be any of the three kinds. Check
``isinstance(hist, NumberHistogram)`` before you use an attribute that only a
number histogram has:

.. code-block:: python

    from gain.genomic_resources.repository_factory import build_genomic_resource_repository
    from gain.genomic_resources.genomic_scores import build_score_from_resource_id
    from gain.genomic_resources.histogram import NumberHistogram

    grr = build_genomic_resource_repository()
    score = build_score_from_resource_id("hg38/scores/phastCons100way", grr)

    hist = score.get_score_histogram("phastCons100way")
    assert isinstance(hist, NumberHistogram)

    print(len(hist.bins), len(hist.bars))
    print(hist.bins[:3], hist.bars[:3])
    print(hist.min_value, hist.max_value, hist.out_of_range_bins)

.. code-block:: text

    101 100
    [0.   0.01 0.02] [1829125896  177725064  102444378]
    0.0 1.0 [0, 0]

``bins`` holds the bin edges, so it has one more item than ``bars``. Bar ``i``
counts the values from ``bins[i]`` up to ``bins[i + 1]``. ``min_value`` and
``max_value`` are the smallest and the largest value that the build saw.
``out_of_range_bins`` is a pair of counts: the values below and the values
above the configured view range.

The histogram also keeps three summary numbers:

.. code-block:: python

    print(hist.count, hist.mean, hist.std)
    print(hist.moments_summary())

.. code-block:: text

    2944992024 0.09809510937890936 0.23381027247397798
    (('n', '2,944,992,024'), ('mean', '0.0981'), ('sd', '0.234'))

``count`` is the n. It uses the same weights as the bars: it is
``bars.sum()`` plus both out-of-range counts. ``mean`` is the weighted mean.
``std`` is the population standard deviation, not the sample standard
deviation.

A histogram file that a build wrote before the accumulators existed has no
summary numbers (gain#1589). For such a file, ``count``, ``mean`` and ``std``
are ``None``. When the build folded no value, ``count`` is ``0``, and only
``mean`` and ``std`` are ``None``. Test ``mean`` and ``std`` for ``None``
before you do arithmetic with them.

``moments_summary()`` returns the same numbers as ``(label, text)`` pairs,
formatted for the resource summary page: ``n``, ``mean`` and ``sd``. It
returns ``None`` when the summary numbers are unknown or the build folded
no value.

.. _histograms-reading-categorical:

Reading a categorical histogram
-------------------------------

A categorical histogram counts each distinct value of the score. The
``CLNVC`` score of the ClinVar resource has a few values:

.. code-block:: python

    from gain.genomic_resources.histogram import CategoricalHistogram

    score = build_score_from_resource_id("hg38/scores/ClinVar_20240730", grr)

    hist = score.get_score_histogram("CLNVC")
    assert isinstance(hist, CategoricalHistogram)

    print(hist.truncated)
    print(hist.unique_values, hist.total_count)
    print(hist.raw_values)
    print(hist.display_values)

.. code-block:: text

    False
    8 2906434
    {'single_nucleotide_variant': 2667265, 'Microsatellite': 32873, 'Indel': 14277, 'Deletion': 123085, 'Duplication': 55642, 'Insertion': 11669, 'Variation': 366, 'Inversion': 1257}
    {'single_nucleotide_variant': 2667265, 'Deletion': 123085, 'Duplication': 55642, 'Microsatellite': 32873, 'Indel': 14277, 'Insertion': 11669, 'Inversion': 1257, 'Variation': 366}

The four attributes answer different questions:

``raw_values``
    The count of every value that the histogram holds.

``display_values``
    The counts that the summary page shows, selected and ordered by the
    configuration of the histogram. For ``CLNVC`` the order is by count, from
    the largest to the smallest.

``total_count``
    The sum of all counts.

``unique_values``
    The number of distinct values.

Some scores have many distinct values. For these scores the build writes a
second, small file next to the full histogram, called the truncated sidecar.
Pass ``truncated=True`` to ``get_score_histogram`` to read the sidecar and to
skip the full file:

.. code-block:: python

    full = score.get_score_histogram("CLNDN")
    small = score.get_score_histogram("CLNDN", truncated=True)
    for hist in (full, small):
        print(hist.truncated, hist.unique_values, hist.total_count,
              len(hist.raw_values), len(hist.display_values))

.. code-block:: text

    False 166335 2905739 166335 11
    True 166335 2905739 10 10

``unique_values`` and ``total_count`` describe the full histogram in both
cases. On the truncated histogram, ``raw_values`` holds only the values that
the sidecar kept. Use ``truncated=True`` when you need the summary and not
every value. When a score has no sidecar, the call returns the full histogram
with or without the argument.

.. _histograms-reading-null:

Reading a null histogram
------------------------

A null histogram has no counts. It has the ``reason`` that explains why. In
ClinVar 20251019, the ``RS`` score has a null histogram:

.. code-block:: python

    from gain.genomic_resources.histogram import NullHistogram

    score = build_score_from_resource_id("hg38/scores/ClinVar_20251019", grr)

    hist = score.get_score_histogram("RS")
    assert isinstance(hist, NullHistogram)

    print(hist.reason)

.. code-block:: text

    Histogram is not available for this score.

The reason comes from the configuration of the score, or from the statistics
build that refused to make the histogram. For example, the build gives the
reason ``Too many unique values 101 for categorical histogram.`` to a score
that is over the cardinality limit described below.

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

It is also what
:func:`~gain.genomic_resources.histogram.load_histogram` returns instead of
failing, for the cases it handles: a missing file, an unrecognised file
extension, and a body that fails to deserialise each come back as an
explained ``NullHistogram``. It is *not* a blanket guarantee — the YAML or
JSON parse and the ``config``/``type`` lookups sit outside that handling, so
a syntactically broken file or one missing its ``config`` key raises.

Histograms are stored as ``.yaml`` on older resources and ``.json`` on newer
ones, so prefer to let the score resolve the name rather than spelling it
yourself:

.. code-block:: python

    from gain.genomic_resources.histogram import load_histogram

    res = grr.get_resource("hg38/scores/phastCons100way")
    hist = load_histogram(res, "statistics/histogram_phastCons100way.json")
    print(hist.type, hist.values_domain())     # number_histogram [0, 1]

``ScoreResource.get_histogram_filename(score_id)`` is that resolver: it
returns the ``.yaml`` name when the resource's manifest carries one and the
``.json`` name otherwise. Passing a name the resource does not have is not an
error — it is exactly the "file not found" case above, so the mistake shows
up as a ``NullHistogram`` rather than an exception.

Configuration
-------------

The ``histogram:`` block of a score's ``scores:`` entry is written on the
curator-facing YAML side: its keys — bin counts, log scales, view ranges,
value ordering, and the custom ``plot_function`` hook — are documented on
:ref:`grr-histogram-configuration`. That section also carries the one Python
example that belongs on the user page: how to write a ``plot_function``.

What that block parses into is a ``HistogramConfig``, and that is described
here. The chain is short and worth knowing, because it explains where a
default comes from when the YAML says nothing:

#. ``parse_scoredef_config`` reads the ``scores:`` block and produces one
   :class:`~gain.genomic_resources.score_def.GenomicScoreDef` per entry.
#. For each entry, ``build_histogram_config`` reads that entry's
   ``histogram:`` key and returns the matching ``*HistogramConfig``. When
   the key is absent it returns ``None`` — it does *not* substitute a
   default.
#. The default is chosen later, by ``build_default_histogram_conf`` from
   the score's value type — at statistics-build time, and again when the
   histogram is read (``ScoreResource.get_histogram_config``). That is why
   an unconfigured score still gets a histogram, and why which kind it gets
   depends on the declared ``value_type`` rather than on the data.
#. The statistics build uses the config to construct the histogram, fills it
   with :meth:`~gain.genomic_resources.histogram.NumberHistogram.add_value`
   or the vectorised
   :meth:`~gain.genomic_resources.histogram.NumberHistogram.add_batch`, and
   stores the serialised result in the resource.
#. ``ScoreResource.get_score_histogram`` reads it back through
   :func:`~gain.genomic_resources.histogram.load_histogram`. A histogram
   the definition annuls (a ``null`` config, stated or defaulted) has no
   stored file; ``get_score_histogram`` answers it from the definition,
   with the configured reason, where a direct ``load_histogram`` call
   would report the file as missing.

Merging is what makes step 3 parallelisable: each task histograms a slice of
the genome and
:meth:`~gain.genomic_resources.histogram.NumberHistogram.merge` folds the
partial results together. A merge requires compatible configurations — two
number histograms with different bin edges cannot be added — so the
configuration is fixed before the build starts, not derived from the data as
it arrives.

Cardinality, and truncation
---------------------------

:class:`~gain.genomic_resources.histogram.CategoricalHistogram` counts one
bucket per distinct value, and two separate mechanisms keep that from
getting out of hand. They are easy to confuse, so it is worth separating
them.

**The cardinality limit is a refusal, not a cap.** Past
``UNIQUE_VALUES_LIMIT`` (100), ``add_value`` raises ``HistogramError`` and
the statistics build replaces the whole histogram with a ``NullHistogram``
carrying that message — nothing is kept and nothing is truncated. The limit
applies only when the score was *not* explicitly configured as categorical:
see :meth:`~gain.genomic_resources.histogram.CategoricalHistogramConfig.default_config`
for why the flag that gates it, ``enforce_type``, reads backwards from its
name.

**Truncation is about what gets written and drawn**, and applies to a
histogram that was built successfully.
:attr:`~gain.genomic_resources.histogram.CategoricalHistogram.display_values`
is the ordered subset the summary page draws, controlled by the
configuration's ``displayed_values_count``, ``displayed_values_percent`` and
``value_order`` keys, and
:meth:`~gain.genomic_resources.histogram.CategoricalHistogram.serialize_truncated`
writes that subset as a small sidecar alongside the full histogram. Because
the sidecar also carries
:attr:`~gain.genomic_resources.histogram.CategoricalHistogram.unique_values`
and
:attr:`~gain.genomic_resources.histogram.CategoricalHistogram.total_count`,
a histogram loaded from one still reports the totals of the full data even
though its own counts are the truncated set.

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
