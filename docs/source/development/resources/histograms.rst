
Histograms
==========

Histograms summarize the distribution of score values in a genomic resource. GAIn computes histograms during a resource's statistics build, stores the results with the resource, and displays them on its HTML summary page. Histograms can also be retrieved and examined through the Python interface.

Histogram types
---------------

GAIn supports three types of histograms, depending on the kind of data being summarized:

* **Numeric histograms** divide a range of numerical values into bins and count the values falling within each bin.
* **Categorical histograms** count the occurrences of individual values or categories.
* **Null histograms** represent cases where a histogram is unavailable or was intentionally not computed, together with an explanation.

Each histogram type has a corresponding Python class and configuration class:

.. list-table::
   :header-rows: 1
   :widths: 24 38 38

   * - Type
     - Histogram class
     - Configuration class
   * - Numeric
     - :class:`~gain.genomic_resources.histogram.NumberHistogram`
     - :class:`~gain.genomic_resources.histogram.NumberHistogramConfig`
   * - Categorical
     - :class:`~gain.genomic_resources.histogram.CategoricalHistogram`
     - :class:`~gain.genomic_resources.histogram.CategoricalHistogramConfig`
   * - Null
     - :class:`~gain.genomic_resources.histogram.NullHistogram`
     - :class:`~gain.genomic_resources.histogram.NullHistogramConfig`


The names ``Histogram`` and ``HistogramConfig`` are type aliases that
represent the three histogram classes and their corresponding configuration
classes:

.. code-block:: python

    Histogram = NullHistogram | CategoricalHistogram | NumberHistogram
    HistogramConfig = (
        NullHistogramConfig | CategoricalHistogramConfig | NumberHistogramConfig
    )

These aliases are used in type annotations. For example, a function
declared to return a ``Histogram`` may return any of the three histogram
types. They do not define additional Python classes.

The ``isinstance()`` function can be used to identify its class before accessing type-specific attributes.


.. _histograms-reading-number:

Reading numeric histograms
--------------------------

Numeric histograms divide a range of numerical values into bins and count the values falling within each bin. Histograms can be retrieved from genomic score and gene score resources using ``get_score_histogram()``.

For example, the following code retrieves the histogram of the ``phastCons100way`` conservation score:

.. code-block:: python

    import gain.grr as grr
    from gain.genomic_resources.histogram import NumberHistogram

    repo = grr.build_genomic_resource_repository()
    score = grr.build_score_from_resource_id(
        "hg38/scores/phastCons100way", repo
    )

    hist = score.get_score_histogram("phastCons100way")
    assert isinstance(hist, NumberHistogram)

A ``NumberHistogram`` contains two arrays describing the distribution:

* ``bins`` contains the boundaries of the histogram bins.
* ``bars`` contains the number of values falling within each bin.

For example:

.. code-block:: python

    print(len(hist.bins), len(hist.bars))
    print(hist.bins[:3], hist.bars[:3])
    print(hist.min_value, hist.max_value, hist.out_of_range_bins)

.. code-block:: text

    101 100
    [0.   0.01 0.02] [1829125896  177725064  102444378]
    0.0 1.0 [0, 0]

The ``bins`` array has one more element than ``bars`` because each bar represents the interval between two consecutive bin boundaries. For example, the first bar counts values between 0 and 0.01.

The ``min_value`` and ``max_value`` attributes report the smallest and largest values observed during histogram computation. Values outside the configured histogram range are counted separately in ``out_of_range_bins``, which contains two counts: values below and values above the range.


When a resource defines only one score, the score ID can be omitted from
``get_score_histogram()``. For example, ``phastCons100way`` defines only one
score, so the following code verifies that both calls return equivalent
histograms:

.. code-block:: python

    hist = score.get_score_histogram()
    assert hist.to_dict() == score.get_score_histogram("phastCons100way").to_dict()

For resources containing multiple scores, the score ID must be specified.
Otherwise, ``get_score_histogram()`` raises a ``ValueError`` listing the
available score IDs.


Numeric histograms also provide summary statistics through three attributes:

* ``count`` gives the total number of values, calculated as the sum of ``bars`` and both counts in ``out_of_range_bins``.
* ``mean`` gives the weighted mean of the values.
* ``std`` gives the population standard deviation, rather than the sample standard deviation.

These values can be accessed directly, or through :meth:`~gain.genomic_resources.histogram.NumberHistogram.moments_summary`, which returns formatted label-value pairs suitable for the resource summary page:

.. code-block:: python

    print(hist.count, hist.mean, hist.std)
    print(hist.moments_summary())

.. code-block:: text

    2944992024 0.09809510937890936 0.23381027247397798
    (('n', '2,944,992,024'), ('mean', '0.0981'), ('sd', '0.234'))

Older histogram files may lack the stored information needed to calculate these statistics. In that case, ``count``, ``mean``, and ``std`` are ``None``. When no values were included during computation, ``count`` is zero, while ``mean`` and ``std`` are ``None``.

The ``moments_summary()`` method returns ``None`` when the summary statistics are unavailable or no values were counted. Code that uses ``mean`` or ``std`` in calculations should therefore check that they are not ``None``.



.. _histograms-reading-categorical:

Reading categorical histograms
------------------------------

Categorical histograms count the occurrences of individual values rather than dividing a numerical range into bins. They are useful for scores representing categories, such as variant classifications.

For example, the ``CLNVC`` score in the ClinVar resource describes the type of sequence variant. Its histogram can be retrieved as follows:

.. code-block:: python

    from gain.genomic_resources.histogram import CategoricalHistogram

    score = grr.build_score_from_resource_id(
        "hg38/scores/ClinVar_20240730", repo
    )

    hist = score.get_score_histogram("CLNVC")
    assert isinstance(hist, CategoricalHistogram)

    print(hist.truncated)
    print(hist.unique_values, hist.total_count)
    print(hist.raw_values)
    print(hist.display_values)

.. code-block:: text

    False
    8 2906433
    {'single_nucleotide_variant': 2667265, 'Microsatellite': 32873, 'Indel': 14277, 'Deletion': 123084, 'Duplication': 55642, 'Insertion': 11669, 'Variation': 366, 'Inversion': 1257}
    {'single_nucleotide_variant': 2667265, 'Deletion': 123084, 'Duplication': 55642, 'Microsatellite': 32873, 'Indel': 14277, 'Insertion': 11669, 'Inversion': 1257, 'Variation': 366}

A ``CategoricalHistogram`` provides several attributes describing the distribution:

* ``raw_values`` contains the counts for each category stored in the histogram.
* ``display_values`` contains the categories selected and ordered for display on the resource summary page.
* ``unique_values`` gives the total number of distinct values.
* ``total_count`` gives the total number of values counted.

The distinction between ``raw_values`` and ``display_values`` is important. While ``raw_values`` contains the stored category counts, ``display_values`` follows the histogram configuration, which determines which categories are displayed and in what order. In the ``CLNVC`` example, all eight categories are displayed, ordered by their counts from highest to lowest.

Some categorical scores have very large numbers of distinct values. For these scores, GAIn can store a smaller, truncated version of the histogram alongside the complete one. This additional file, called a truncated sidecar, contains only the categories selected for display.

The ``CLNDN`` score in ClinVar, which describes conditions associated with variants, provides an example. The complete histogram and its truncated version can be retrieved separately:

.. code-block:: python

    full = score.get_score_histogram("CLNDN")
    small = score.get_score_histogram("CLNDN", truncated=True)

    for hist in (full, small):
        print(
            hist.truncated,
            hist.unique_values,
            hist.total_count,
            len(hist.raw_values),
            len(hist.display_values),
        )

.. code-block:: text

    False 166334 2905738 166334 11
    True 166334 2905738 10 10

The complete histogram contains counts for all 166,334 distinct values, although only 11 are selected for display. The truncated histogram retains just 10 categories in ``raw_values`` and ``display_values``.

Importantly, ``unique_values`` and ``total_count`` still describe the complete distribution, even when the histogram is truncated. The smaller histogram therefore preserves the overall statistics without requiring every category count to be loaded.

Using ``truncated=True`` is useful when only the displayed categories and overall statistics are needed. If no truncated sidecar exists, GAIn returns the complete histogram instead.

The configuration options controlling the number and ordering of displayed categories, together with the limits on categorical histograms, are described in :ref:`histogram-cardinality-limits`.




.. _histograms-reading-null:

Reading null histograms
-----------------------

A null histogram represents a score for which a histogram was not computed or is unavailable. Unlike numeric and categorical histograms, it contains no counts. Instead, a :class:`~gain.genomic_resources.histogram.NullHistogram` object provides a ``reason`` explaining why the histogram is unavailable.

For example, the ``RS`` score in the ClinVar resource has a null histogram:

.. code-block:: python

    from gain.genomic_resources.histogram import NullHistogram

    score = grr.build_score_from_resource_id(
        "hg38/scores/ClinVar_20251019", repo
    )

    hist = score.get_score_histogram("RS")
    assert isinstance(hist, NullHistogram)

    print(hist.reason)



.. code-block:: text

    Histogram is not available for this score.


The reason may come from the score's histogram configuration or from the statistics build. For example, a histogram may be intentionally disabled, or a categorical score may contain too many distinct values to be counted under the configured limits.

Importantly, a null histogram is an actual Python object, not ``None``. It provides the same general interface as the other histogram types, allowing GAIn to handle different histogram types consistently. Its :meth:`~gain.genomic_resources.histogram.NullHistogram.plot` method produces no plot, and the resource summary page displays the reason instead.

The ``reason`` attribute is required and originates from the corresponding ``NullHistogramConfig``. When no specific explanation is available, :meth:`~gain.genomic_resources.histogram.NullHistogramConfig.default_config` provides the default reason ``"Unspecified reason"``.

A null histogram can also result from an unsuccessful attempt to load a stored histogram. These cases are described in the following section.


Loading histogram files
-----------------------

Histograms are stored as part of a resource's statistics, using ``.yaml``
files in older resources and ``.json`` files in newer ones. The
:func:`~gain.genomic_resources.histogram.load_histogram` function can be
used to load a histogram directly from a resource file.

For example:

.. code-block:: python

    from gain.genomic_resources.histogram import load_histogram

    score = grr.build_score_from_resource_id(
        "hg38/scores/phastCons100way", repo
    )
    resource = repo.get_resource("hg38/scores/phastCons100way")

    filename = score.get_histogram_filename("phastCons100way")
    hist = load_histogram(resource, filename)

    print(hist.type, hist.values_domain())

.. code-block:: text

    number_histogram [0, 1]

The ``get_histogram_filename()`` method determines the appropriate
histogram filename. It returns the ``.yaml`` filename when one is listed
in the resource manifest, or the ``.json`` filename otherwise. This is
preferable to specifying the filename manually.

When ``load_histogram()`` encounters a missing file, an unrecognized
file extension, or a histogram that cannot be deserialized, it returns
a ``NullHistogram`` containing an explanation of the problem.

However, not all loading errors are handled this way. For example,
malformed YAML or JSON and missing required fields such as ``config``
may raise exceptions rather than returning a ``NullHistogram``.



Histogram configuration
-----------------------

Histogram settings are defined in the ``histogram:`` block of a score's ``scores:`` entry in the resource definition. These settings control properties such as bin counts, logarithmic scales, view ranges, value ordering, and custom plotting functions. The available options, including an example of a custom ``plot_function``, are described in :ref:`grr-histogram-configuration`.

In Python, these settings are represented by ``HistogramConfig`` objects. GAIn processes the configuration and computes the histogram through the following steps:

#. **Reading score definitions.** The ``parse_scoredef_config()`` function reads the ``scores:`` block of a resource definition and creates a :class:`~gain.genomic_resources.score_def.GenomicScoreDef` object for each score.

#. **Reading histogram configurations.** For each score, ``build_histogram_config()`` reads its ``histogram:`` block and creates the corresponding configuration object, such as ``NumberHistogramConfig``, ``CategoricalHistogramConfig``, or ``NullHistogramConfig``. If no ``histogram:`` block is present, the function returns ``None`` rather than creating a default configuration.

#. **Applying default configurations.** When no histogram configuration is explicitly provided, GAIn uses ``build_default_histogram_conf()`` to select one based on the score's declared ``value_type``. This happens when statistics are computed and again when the configuration is retrieved through ``ScoreResource.get_histogram_config()``. As a result, a score can have a histogram even without an explicit ``histogram:`` block. The default histogram type depends on the declared value type, not on the values observed in the data.

#. **Computing and storing the histogram.** During the statistics build, GAIn creates a histogram using the selected configuration and populates it with score values. Methods such as :meth:`~gain.genomic_resources.histogram.NumberHistogram.add_value` and the vectorized :meth:`~gain.genomic_resources.histogram.NumberHistogram.add_batch` are used to accumulate values. The completed histogram is serialized and stored with the resource.

#. **Retrieving the histogram.** The ``ScoreResource.get_score_histogram()`` method retrieves the stored histogram through :func:`~gain.genomic_resources.histogram.load_histogram`. An exception is a score configured to use a null histogram, either explicitly or by default. In this case, no histogram file is stored. Instead, ``get_score_histogram()`` constructs a ``NullHistogram`` using the reason specified by the configuration. Calling ``load_histogram()`` directly for the nonexistent file would instead return a ``NullHistogram`` indicating that the file is missing.

Merging allows histogram statistics to be computed in parallel. Different tasks calculate histograms for separate genomic regions, and their results are combined using methods such as :meth:`~gain.genomic_resources.histogram.NumberHistogram.merge`.

Merging requires compatible histogram configurations. For example, numeric histograms with different bin boundaries cannot be combined. The histogram configuration must therefore be established before computation begins, rather than determined from the data as values are processed.



.. _histogram-cardinality-limits:

Categorical limits and truncation
---------------------------------

Categorical histograms maintain a count for each distinct value. GAIn uses two separate mechanisms to manage scores with many distinct values: cardinality limits and truncation.

**Cardinality limits** prevent a histogram from being computed when the number of distinct values becomes too large. For scores that are not explicitly configured as categorical, GAIn applies a limit of 100 unique values (``UNIQUE_VALUES_LIMIT``). If this limit is exceeded, ``add_value()`` raises a ``HistogramError``, and the statistics build replaces the histogram with a ``NullHistogram`` explaining the problem. No partial categorical histogram is retained.

This limit does not apply when a score is explicitly configured as categorical. The distinction is controlled by the ``enforce_type`` setting in :meth:`~gain.genomic_resources.histogram.CategoricalHistogramConfig.default_config`, whose name can be counterintuitive in this context.

**Truncation**, by contrast, applies to categorical histograms that have been computed successfully. It limits the categories stored for display without discarding the complete histogram.

The ``display_values`` attribute contains the categories selected and ordered for the resource summary page. This selection is controlled by three configuration options:

* ``displayed_values_count`` limits the number of displayed categories.
* ``displayed_values_percent`` specifies the percentage of total counts represented by the displayed categories.
* ``value_order`` determines their ordering.

The :meth:`~gain.genomic_resources.histogram.CategoricalHistogram.serialize_truncated` method writes the selected categories to a smaller file alongside the complete histogram. This truncated sidecar retains the ``unique_values`` and ``total_count`` of the full histogram, even though it stores counts for only a subset of categories.

Thus, exceeding the cardinality limit prevents a categorical histogram from being created, while truncation provides a smaller representation of a successfully computed histogram.


API
---

The functions and classes below are defined in the module
``gain.genomic_resources.histogram``. ``gain.grr`` does not export any of
them.

.. currentmodule:: gain.genomic_resources.histogram

.. autosummary::

   load_histogram
   NumberHistogram
   NumberHistogram.add_batch
   NumberHistogram.add_value
   NumberHistogram.count
   NumberHistogram.deserialize
   NumberHistogram.from_dict
   NumberHistogram.mean
   NumberHistogram.merge
   NumberHistogram.moments
   NumberHistogram.moments_summary
   NumberHistogram.plot
   NumberHistogram.serialize
   NumberHistogram.std
   NumberHistogram.to_dict
   NumberHistogram.values_domain
   NumberHistogram.view_max
   NumberHistogram.view_min
   CategoricalHistogram
   CategoricalHistogram.add_batch
   CategoricalHistogram.add_value
   CategoricalHistogram.deserialize
   CategoricalHistogram.display_values
   CategoricalHistogram.from_dict
   CategoricalHistogram.merge
   CategoricalHistogram.moments_summary
   CategoricalHistogram.plot
   CategoricalHistogram.raw_values
   CategoricalHistogram.serialize
   CategoricalHistogram.serialize_gzipped
   CategoricalHistogram.serialize_truncated
   CategoricalHistogram.to_dict
   CategoricalHistogram.total_count
   CategoricalHistogram.unique_values
   CategoricalHistogram.values_domain
   NullHistogram
   NullHistogram.add_value
   NullHistogram.deserialize
   NullHistogram.from_dict
   NullHistogram.merge
   NullHistogram.moments_summary
   NullHistogram.plot
   NullHistogram.serialize
   NullHistogram.to_dict
   NullHistogram.values_domain
   NumberHistogramConfig
   NumberHistogramConfig.default_config
   NumberHistogramConfig.from_dict
   NumberHistogramConfig.has_view_range
   NumberHistogramConfig.to_dict
   CategoricalHistogramConfig
   CategoricalHistogramConfig.default_config
   CategoricalHistogramConfig.from_dict
   CategoricalHistogramConfig.to_dict
   NullHistogramConfig
   NullHistogramConfig.default_config
   NullHistogramConfig.from_dict
   NullHistogramConfig.to_dict

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
