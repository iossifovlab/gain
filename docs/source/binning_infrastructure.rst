Binning infrastructure
======================

The GAIn binning infrastructure converts genomic data into numerical matrices by dividing the genome into fixed-size regions, called **bins**, and summarizing genomic scores within each bin. This allows users to compare genomic features across regions, identify patterns, and prepare data for statistical analysis or machine learning.

Binning supports two types of genomic resources:

- **Position scores:** Numerical scores associated with genomic positions, such as conservation scores. These can be summarized within each bin using operations such as mean, maximum, or sum.
- **Fragment scores:** Data associated with genomic intervals, such as ATAC-seq fragments or structural variants. These can be counted or summarized within bins, optionally grouped by attributes such as cell type.

The output is a matrix in which each row represents a genomic bin and each column represents a **track**, corresponding to a resource or a group of fragments.

A binning analysis is configured using a YAML **run definition**, which specifies the reference genome, bin size, genomic regions, and resources to process. The ``binning_tool`` command executes the analysis and produces an HDF5 file containing the matrix, genomic coordinates, and information about the tracks. The results can be inspected using ``binning_tool_view`` or accessed programmatically in Python.

Getting started
---------------

This example demonstrates how to use ``binning_tool`` to summarize genomic scores across a small region of the genome. We will divide a 100 kb region of chromosome 21 into ten bins and calculate the mean and maximum phyloP100way conservation scores within each bin.

**1. Create a run definition**

Create a file named ``demo.yaml`` with the following contents:

.. code-block:: yaml

    reference_genome: hg38/genomes/GRCh38-hg38

    bins:
      bin_size: 10000
      regions: ["chr21:20000001-20100000"]

    tracks:
    - position_score_tracks:
        resource_query: hg38/scores/phyloP100way
        aggregator: mean

    - position_score_tracks:
        resource_query: hg38/scores/phyloP100way
        aggregator: max

The ``reference_genome`` specifies the genome assembly, while ``bins`` defines the genomic region and bin size. The two ``position_score_tracks`` entries select the same resource but use different aggregators, producing two tracks.

**2. Run the binning tool**

First, verify the run definition without processing the data:

.. code-block:: bash

    binning_tool demo.yaml --dry-run

Then execute the analysis:

.. code-block:: bash

    binning_tool demo.yaml -j 1

The ``-j 1`` option specifies one worker. The command produces ``demo.h5`` in the same directory as the run definition.

**3. Inspect the results**

Display the genomic bins and their corresponding scores:

.. code-block:: bash

    binning_tool_view demo.h5 --bins --matrix

The output contains ten rows and two score columns, shown below with values rounded for readability:


.. code-block:: text

    chrom  start     end       hg38/scores/phyloP100way:mean  hg38/scores/phyloP100way:max
    chr21  20000001  20010000  -0.024167                      3.356
    chr21  20010001  20020000  -0.006847                      3.539
    chr21  20020001  20030000   0.004481                      3.694
    chr21  20030001  20040000  -0.013076                      2.123
    chr21  20040001  20050000   0.010425                      2.455
    chr21  20050001  20060000   0.008159                      4.166
    chr21  20060001  20070000  -0.017430                      2.081
    chr21  20070001  20080000   0.019938                      3.707
    chr21  20080001  20090000   0.105878                      6.757
    chr21  20090001  20100000  -0.017527                      2.652


Each row represents a 10 kb genomic bin. The **mean** track reports the average phyloP100way score across the bin, while the **max** track reports its highest score. The complete results are stored in ``demo.h5``, together with the genomic coordinates and track metadata.

**Note:** This example uses a resource from the public IossifovLab GRR. Remote bigWig files can be accessed efficiently when ``pyBigWig`` supports HTTP range requests. If the GRR definition includes ``cache_dir``, GAIn may instead download the entire bigWig file before processing, which can substantially increase execution time.


Configuring a binning run
-------------------------

A binning analysis is configured using a YAML run definition. Each binner selects one or more genomic resources and produces one or more tracks in the output matrix.

.. _binning-run-definition:

The run definition
^^^^^^^^^^^^^^^^^^

A run definition consists of three components:

- ``reference_genome``: The reference genome whose chromosomes define the genomic coordinates.
- ``bins``: The bin size and genomic regions to analyze.
- ``tracks``: A list of track entries specifying which resources to process and how to summarize them.

**Reference genome**

The required ``reference_genome`` field identifies a reference genome resource in a GRR. Its chromosome names and lengths determine the genomic bins. The reference genome is specified in the run definition, not through a command-line argument.

**Bins**

The ``bins`` block contains two fields:

- ``bin_size`` (required): The width of each bin in base pairs.
- ``regions`` (optional): The chromosomes or genomic intervals to analyze, using GAIn's standard notation, such as ``chr21`` or ``chr21:20000000-22000000``.

If ``regions`` is omitted, all chromosomes in the reference genome are processed. When multiple regions are specified, their order is preserved in the output.

Bin coordinates are 1-based and inclusive. Bins follow a fixed grid starting at position 1 of each chromosome. If a specified region does not align with this grid, its first and last bins are clipped to the region boundaries.

Invalid coordinates, unknown chromosomes, regions extending beyond chromosome boundaries, and overlapping regions produce errors.

**Track entries and resource selection**

The required ``tracks`` field contains a list of entries of two types:

- ``position_score_tracks``: Processes position-score resources.
- ``fragment_score_tracks``: Processes fragment-score resources.

Each entry requires a ``resource_query`` to select resources from the GRR. Queries can specify an exact resource ID or use wildcards to match multiple resources. Optional label filters can further restrict the matches.

An optional ``search_term`` can filter resources using the same full-text search functionality as ``grr_browse``. This requires a repository with a full-text index.

Each binner processes only resources of its supported type. Matching resources are ordered by resource ID, and a query that matches no resources of the appropriate type produces an error.

The GRR itself is selected through GAIn's GRR configuration or command-line options, rather than being specified in the run definition. This allows the same run definition to be used with different GRR locations.

All configuration keys are validated, and unrecognized keys produce errors rather than being silently ignored.



Position-score binning
^^^^^^^^^^^^^^^^^^^^^^

A ``position_score_tracks`` summarizes numerical scores associated with genomic positions. Each matched resource produces one track in the output matrix, with one value per bin.

**Aggregators**

The ``aggregator`` field determines how scores within each bin are combined into a single value. GAIn supports seven numerical aggregators:

- ``mean``: Average of the scores.
- ``median``: Median of the scores.
- ``max`` and ``min``: Highest and lowest scores.
- ``sum``: Sum of the scores.
- ``count``: Number of observations.
- ``product``: Product of the scores.

If ``aggregator`` is omitted, GAIn uses the default aggregator specified in the resource configuration. An explicitly specified aggregator overrides this default.

For example, to calculate the median phyloP100way score within each bin:

.. code-block:: yaml

    - position_score_tracks:
        resource_query: hg38/scores/phyloP100way
        aggregator: median

The same resource can be processed using different aggregators by including multiple binner entries, as demonstrated in the Getting started example.

Only numerical scores (``int`` or ``float``) and aggregators producing numerical results are supported. Resources defining multiple scores cannot currently be processed by a position-score binner.

**Handling missing values**

By default, positions without recorded scores are excluded from aggregation. A bin containing no recorded scores produces ``NaN``, except with ``count``, which produces ``0``.

The optional ``none_value_replacement`` field assigns a value to positions without recorded scores before aggregation:

.. code-block:: yaml

    none_value_replacement: 0.0

This is useful when missing observations represent an absence of signal, such as in certain coverage tracks. It should not be used when missing values represent unknown measurements.


.. _binning-fragment-score-entries:

Fragment-score binning
^^^^^^^^^^^^^^^^^^^^^^

A ``fragment_score_tracks`` summarizes genomic intervals, such as ATAC-seq fragments or structural variants. Unlike position-score binning, a single fragment-score resource can produce multiple tracks by grouping fragments according to their attributes.

**Basic configuration**

Unlike position-score resources, which provide numerical scores at genomic positions, fragment-score resources contain genomic intervals that may have associated scores. The ``value`` setting determines what numerical value each fragment contributes to the binning calculation. This can be a constant, such as 1 for counting fragments, or a numerical score associated with each fragment.

A simple fragment-score binner can be defined as follows:

.. code-block:: yaml

    - fragment_score_tracks:
        resource_query: hg38/cnv_collections/gnomAD.v4.1_Genome_SV
        value: {value: 1}
        aggregate:
          mode: fragment_start
          aggregator: sum

Three settings determine how fragments are summarized:

- ``value``: Specifies what each fragment contributes. Here, every fragment contributes 1.
- ``aggregate.mode``: Determines how fragments contribute to genomic bins. Here, each fragment contributes to the bin containing its starting position.
- ``aggregate.aggregator``: Determines how contributions within each bin are combined. Here, they are summed.

Together, these settings count the number of fragments starting in each bin. Alternatively, if a resource contains a numerical fragment score named ``count``, specifying ``value: {score_id: count}`` uses that score instead of assigning 1 to every fragment. All three settings are optional. The defaults are ``value: {value: 1}``, ``mode: fragment_start``, and ``aggregator: sum``.

Fragment-score binning uses the aggregators introduced in the previous section, although the supported aggregators depend on the aggregation mode.

**Aggregation modes**

The ``aggregate.mode`` field determines how fragments contribute to genomic bins. Three modes are supported:

- ``fragment_start`` (default): Each fragment contributes only to the bin containing its starting position, regardless of its length.
- ``fragment_length``: Each fragment contributes to every bin it overlaps, weighted by the number of overlapping base pairs.
- ``coverage_profile``: GAIn calculates the coverage depth at each genomic position by summing the values of overlapping fragments, then aggregates these depths within each bin.

For example, to calculate mean coverage depth:

.. code-block:: yaml

    aggregate:
      mode: coverage_profile
      aggregator: mean

All three modes support ``sum``, ``mean``, ``max``, ``min``, and ``median``. The ``count`` and ``product`` aggregators are supported only in ``fragment_start`` mode.

**Comparing the three modes**

Consider a 30 bp chromosome divided into three 10 bp bins, containing two fragments at ``chr1:6-14`` and ``chr1:11-18``. With each fragment contributing a value of 1 and using the ``sum`` aggregator:

+---------+----------------+-----------------+------------------+
| Bin     | Fragment start | Fragment length | Coverage profile |
+=========+================+=================+==================+
| 1–10    | 1              | 5               | 5                |
+---------+----------------+-----------------+------------------+
| 11–20   | 1              | 12              | 12               |
+---------+----------------+-----------------+------------------+
| 21–30   | 0              | 0               | 0                |
+---------+----------------+-----------------+------------------+

``fragment_start`` counts each fragment once. ``fragment_length`` sums the overlapping base pairs, while ``coverage_profile`` sums the coverage depths across each bin.

The last two modes produce identical sums in this example, but can produce different results with other aggregators. For example, using ``mean`` in bin 11–20 gives 1.0 with ``fragment_length`` (12/12 overlapping base pairs) and 1.2 with ``coverage_profile`` (12/10 genomic positions). This difference reflects how overlapping fragments and uncovered positions are treated.

Additional options allow fragments to be pooled across resources, grouped into separate tracks, or associated with cell metadata. These features, together with the handling of uncovered bases and empty bins, are described in the next section.


Advanced fragment-score binning
-------------------------------

Pooling resources
^^^^^^^^^^^^^^^^^

A ``fragment_score_tracks`` can select multiple resources using a single ``resource_query``. The optional ``pool`` setting determines how these resources are processed. Pooling allows fragments from multiple resources to be combined into shared tracks, rather than processed separately. This is useful when combining measurements across samples, while keeping them separate allows comparisons between samples.

- ``pool: true`` (default): Combines fragments from all matched resources into shared tracks.
- ``pool: false``: Processes each matched resource separately, producing separate tracks.

For example, suppose two ATAC-seq samples are stored as resources named ``sc/atac_fragments/donor1`` and ``sc/atac_fragments/donor2``. Both can be selected using the following query:

.. code-block:: yaml

    - fragment_score_tracks:
        resource_query: "sc/atac_fragments/*"
        pool: true

With ``pool: true``, fragments from both resources contribute to the same tracks. Changing this to ``pool: false`` produces separate tracks for each resource. In either case, the number of tracks also depends on how fragments are grouped, as described in the next section.

When pooling resources that use cell metadata, they must have compatible grouping configurations and resolve to the same metadata table. Otherwise, GAIn reports an error.

Grouping fragments into tracks
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Grouping allows fragments within a resource to be separated into different tracks based on their attributes. For example, ATAC-seq fragments can be grouped by cell type, producing a separate track for each cell type in the output matrix.

The optional ``group`` setting supports three approaches:

- **Constant group:** Assigns all fragments to a single track with a specified group name.
- **Grouping by a fragment score:** Creates a separate track for each distinct value of a categorical fragment score.
- **Grouping by cell metadata:** Uses a metadata table to assign fragments to groups, such as cell types.

The three grouping methods are mutually exclusive. A ``group`` configuration cannot combine settings from different methods.

**Constant group**

To place all fragments into a single group named ``all``:

.. code-block:: yaml

    group: {group: all}

This produces one track per resource when ``pool: false``, or one combined track when ``pool: true``.

**Grouping by a fragment score**

The ``group_score_id`` option creates a separate track for each distinct value of a string-valued fragment score. For example:

.. code-block:: yaml

    group: {group_score_id: cell}

If the fragments contain a score named ``cell`` identifying their cell barcodes, this configuration creates one track per cell.

The distinct values must be available in the score's complete categorical histogram, stored in the resource statistics. Resources without a complete histogram cannot be grouped this way. Statistics can be generated using ``grr_manage resource-stats`` and must be available locally if managed through DVC. For scores with more than 100 distinct values, the resource configuration must specify ``histogram: {type: categorical}`` to retain the full histogram. The grouping score cannot also be used as the ``value`` score.

Without pooling, each group is the bare value. With ``pool: true``, the same barcode can recur in different samples while naming a different cell, so GAIn prefixes each value with the resource's ``sample_id`` label, as ``<sample_id>:<value>``. The rules for pooled resources are:

- If any pooled resource has a ``sample_id`` label, every pooled resource must have one. Each label must hold a single value without a colon (``:``), and no two pooled resources can share a value. Otherwise GAIn reports an error that suggests ``pool: false``.
- If no pooled resource has a ``sample_id`` label, each group is the bare value, and ``--dry-run`` warns once for each resource. This suits a merged multi-sample file whose barcodes are already unique across its samples. The same value in two resources reaches one track.
- A ``sample_id`` label that holds a list counts as present, and is an error.

**Grouping by cell metadata**

Fragments can also be grouped using an associated metadata table. For example, cell barcodes can be matched to cell annotations to create one track per cell type.

For resources following GAIn's single-cell convention, this grouping can be enabled with:

.. code-block:: yaml

    group: {}

GAIn creates one track for each distinct, non-empty group in the selected metadata rows, ordered by group name. With ``pool: true``, groups are determined across all matched resources. With ``pool: false``, each resource has its own set of groups.

The required metadata relationships and default column names are described in the next section, Working with cell metadata.

**Default behavior**

When ``group`` is omitted, GAIn automatically groups fragments by cell metadata if the resource has a ``cell_meta_resource_id`` label. Otherwise, all fragments belong to a single group named ``all``.

The number of output tracks therefore depends on the selected resources, the grouping method, and whether pooling is enabled. A per-cell analysis can produce thousands of tracks, so ``binning_tool --dry-run`` is useful for checking the expected matrix dimensions before processing.

.. _binning-local-metadata-files:

Working with cell metadata
^^^^^^^^^^^^^^^^^^^^^^^^^^

Cell metadata allows ATAC-seq fragments to be grouped by attributes such as cell type. GAIn matches each fragment's cell barcode to a metadata table and produces a separate track for each group.

**The single-cell convention**

GAIn can automatically associate fragment resources with their cell metadata when they follow a standard convention:

- ``cell_meta_resource_id``: A label on the fragment resource identifying the metadata ``data_frame`` resource.
- ``sample_id``: A label identifying the sample within the metadata table.
- ``cell``: The fragment score containing the cell barcode.

The metadata table is expected to contain three columns:

- ``sample_id``: Identifies the sample.
- ``barcode``: Identifies the cell.
- ``class``: Specifies the cell's group, such as its cell type.

When these conventions are followed, GAIn automatically selects the metadata rows belonging to each sample and groups fragments by ``class``. No explicit ``group`` or ``meta`` configuration is needed.

A merged multi-sample fragment file has no single sample, and so no ``sample_id`` label. When no resource of a job has a ``sample_id`` label, the default ``meta`` has no filter: the job reads every row of the metadata table, and ``--dry-run`` warns once for each resource. The table then needs no ``sample_id`` column, and its barcodes must be unique across the whole table. A pool in which some resources have the label and others do not is an error.

**Specifying metadata explicitly**

When resources do not follow the standard convention, the ``group`` and ``meta`` settings can specify how fragments are associated with metadata.

For example, the conventional configuration can be written explicitly as:

.. code-block:: yaml

    - fragment_score_tracks:
        resource_query: "sc/atac_fragments/*"
        group:
          cell_score_id: cell
          cell_meta_column: barcode
          group_meta_column: class
        meta:
          resource_label: cell_meta_resource_id
          filter:
          - column: sample_id
            label: sample_id

The ``group`` block identifies the fragment score containing cell barcodes and the metadata columns used to match cells and assign groups. All three fields are optional, with defaults of ``cell_score_id: cell``, ``cell_meta_column: barcode``, and ``group_meta_column: class``. Consequently, ``group: {}`` uses these defaults.

The ``meta`` block identifies the metadata table and optionally filters its rows. In this example, ``resource_label`` identifies the metadata resource through a label on each fragment resource. The filter selects rows where the metadata table's ``sample_id`` column matches the fragment resource's ``sample_id`` label.

Metadata can be supplied in three ways:

- ``resource_id``: Identifies a specific ``data_frame`` resource.
- ``resource_label``: Uses a label on the fragment resource to identify the metadata resource.
- ``file_name``: Reads metadata from a local CSV, TSV, or Excel file.

The ``meta`` setting can only be used with metadata-based grouping, either explicitly configured through ``group`` or automatically enabled through the single-cell convention. It cannot be combined with constant grouping or ``group_score_id``.

Local file paths are interpreted relative to the directory containing the run definition. When using ``meta.file_name``, the optional ``file_format`` (``csv``, ``tsv``, or ``excel``) and ``file_separator`` settings control how the file is read. If ``file_format`` is omitted, ``.tsv`` files are read as TSV, ``.xls`` and ``.xlsx`` files as Excel, and other extensions as CSV. Modern ``.xlsx`` files are supported directly, while legacy ``.xls`` files require the additional ``xlrd`` package.

Metadata filters are specified using ``meta.filter``, with each condition matching a metadata column against either a constant value or a fragment resource label. For example:

.. code-block:: yaml

    filter:
    - column: sample_id
      label: sample_id
    - column: class
      value: ODC

The first condition selects rows matching the fragment resource's ``sample_id`` label, while the second restricts the selection to cells of class ``ODC``. All conditions must be satisfied. Resource labels containing multiple values cannot be used as filter operands.

**Validation and unmatched fragments**

Before processing, GAIn verifies that the metadata resource and required columns exist, that filter labels are valid, and that cell barcodes are unique within the selected rows of each resource. The selected rows must also contain at least one non-empty group.

Fragments whose barcodes are absent from the selected metadata rows, or whose assigned groups are empty, are excluded from the output. When grouping by a fragment score using ``group_score_id``, fragments are also excluded if their grouping value is missing or absent from the score's categorical histogram. Dropped fragments are counted separately for each resource and genomic region, and their counts are reported at INFO level using the ``-v`` option.

When multiple resources are pooled, they must use compatible grouping configurations and resolve to the same metadata table. GAIn reports an error if these requirements are not met.


Uncovered regions and empty bins
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Genomic bins may contain positions that are not covered by any fragments. The ``uncovered_value`` setting determines how these positions contribute to aggregation.

**Uncovered bases**

The optional ``aggregate.uncovered_value`` field is supported by ``fragment_length`` and ``coverage_profile`` modes. It can be set to a numerical value or ``null``:

- ``fragment_length``: The default is ``null``, meaning uncovered bases are excluded from aggregation.
- ``coverage_profile``: The default is ``0``, meaning uncovered bases contribute a coverage depth of zero.

The ``uncovered_value`` setting accepts any finite numerical value, not just zero, or ``null`` to exclude uncovered bases from aggregation. An explicit ``NaN`` value is not permitted.

For example, to exclude uncovered bases when calculating mean coverage depth:

.. code-block:: yaml

    aggregate:
      mode: coverage_profile
      aggregator: mean
      uncovered_value: null

Consider a 10 bp bin in which a single fragment covers 4 bp. With ``coverage_profile`` and ``mean``, the default ``uncovered_value: 0`` produces a mean coverage of 0.4, averaged across all 10 positions. Setting ``uncovered_value: null`` produces a mean of 1.0, calculated only across the 4 covered positions.

In ``fragment_length`` mode, overlapping base pairs are counted separately for each fragment, while uncovered bases contribute only once. This can produce different means from ``coverage_profile``. For example, with 12 overlapping fragment base pairs covering 8 positions of a 10 bp bin, ``uncovered_value: 0`` produces a mean of 12/14 (0.857) with ``fragment_length``, compared with 12/10 (1.2) with ``coverage_profile``.

The ``fragment_start`` mode does not support ``uncovered_value``, since it aggregates fragments based on their starting positions rather than coverage across individual bases.

**Empty bins**

A bin is considered empty when no fragments contribute to its aggregation. The resulting value depends on the aggregator:

- ``sum`` and ``count``: Return ``0``.
- ``mean``, ``median``, ``max``, ``min``, and ``product``: Return ``NaN``.

These rules apply to ``fragment_start`` and to the other two modes when ``uncovered_value`` is ``null``. When a numerical ``uncovered_value`` is specified, uncovered positions contribute that value, so bins with no fragments are not empty.

The same rules apply when a resource has no fragments on a particular chromosome. If ``uncovered_value`` is ``null``, the bins follow the empty-bin rules described above. If a numerical ``uncovered_value`` is specified, every base is assigned that value. For example, with ``uncovered_value: 2``, a 10 bp bin produces a ``mean`` of 2 or a ``sum`` of 20.

Exploring the output
--------------------

HDF5 file structure
^^^^^^^^^^^^^^^^^^^

The ``binning_tool`` command produces a single HDF5 file containing the numerical matrix, genomic coordinates, and information about the tracks and their source resources.

The file contains three datasets:

- ``/values``: A numerical matrix with one row per genomic bin and one column per track. Values are stored as 64-bit floating-point numbers (``float64``), with ``NaN`` representing missing values where applicable. The matrix is gzip-compressed.
- ``/bins``: A table describing the genomic coordinates of each bin, with fields ``chrom``, ``start``, and ``end``. Coordinates are 1-based and inclusive.
- ``/tracks``: A table describing each track, including its binner type, source resources, score or group identifier, and configuration used to generate it.

Row *i* of ``/values`` corresponds to row *i* of ``/bins``, while column *j* corresponds to row *j* of ``/tracks``. This allows every numerical value to be associated with a genomic region and the resource from which it was calculated.

**File metadata**

The HDF5 file also stores attributes describing the binning run:

- ``reference_genome``: The reference genome resource used.
- ``bin_size``: The bin width in base pairs.
- ``regions``: The genomic regions included in the analysis.
- ``coordinates``: The coordinate convention (``1-based-inclusive``).
- ``gain_version``: The GAIn version used to generate the file.
- ``created``: The creation time in UTC.
- ``tracks_layout``: The version of the track metadata format, currently ``2``.

Each track also retains its source resource IDs and binner configuration, allowing the calculations to be traced back to their inputs.

When local metadata files are used, their paths, sizes, and modification times are recorded in additional file attributes.

The HDF5 format can be accessed using standard tools such as ``h5py``, as well as the dedicated GAIn functions described in the next section.

Reading the output with Python
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

GAIn provides functions for reading the three datasets in a binning output file:

- ``read_values``: Returns the numerical matrix as a NumPy array.
- ``read_bins``: Returns the genomic bin coordinates as a pandas DataFrame.
- ``read_tracks``: Returns the track metadata as a pandas DataFrame.
- ``track_labels``: Generates readable labels for the tracks based on their metadata.

The ``read_bins`` and ``read_tracks`` functions accept either a file path or an open ``h5py.File`` object. For example, the ``demo.h5`` file created in Getting started can be read using:

.. code-block:: python

    import pandas as pd
    from gain.binning.h5_reader import (
        read_values, read_bins, read_tracks, track_labels
    )

    values = read_values("demo.h5")
    bins = read_bins("demo.h5")
    tracks = read_tracks("demo.h5")

    labels = track_labels(tracks)
    result = bins.join(pd.DataFrame(values, columns=labels))

    print(result.round(3).to_string(index=False))

This produces a table containing the genomic coordinates and both phyloP100way tracks, equivalent to the output of ``binning_tool_view demo.h5 --bins --matrix``.

The ``values`` array has shape ``(n_bins, n_tracks)``. The rows correspond to ``bins``, while the columns correspond to the labels generated from ``tracks``.

**Direct HDF5 access**

The output can also be accessed using ``h5py`` without GAIn's reader functions:

.. code-block:: python

    import h5py

    with h5py.File("demo.h5", "r") as h5:
        print(h5["values"].shape)
        print(h5["bins"][:3])
        print(h5["tracks"][:])

Unlike ``read_values``, which loads the entire numerical matrix into memory, ``h5py`` allows individual rows, columns, or subsets of the matrix to be read without loading the complete dataset. This is particularly useful for large binning results.

Track names and genomic coordinates
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Track names and labels**

Each track is identified by its source resource and, where applicable, its group and aggregator. Track names depend on the binner type and pooling configuration:

- **Position-score tracks:** Named using the resource ID, such as ``hg38/scores/phyloP100way``.
- **Unpooled fragment-score tracks:** Named using the resource ID and group, such as ``sc/atac_fragments/donor1:ODC``.
- **Pooled fragment-score tracks:** Named using the resource query and group, such as ``sc/atac_fragments/*:ODC``.

Tracks appear in the order of the ``tracks`` entries in the run definition. Within each entry, tracks are ordered first by resource ID and then by group name.

The optional ``name`` field allows users to customize track names. For position-score and unpooled fragment-score tracks, it prefixes the resource ID with ``<name>/``. For pooled fragment-score tracks, it replaces the resource query. For example, specifying ``name: depth`` produces ``depth/sc/atac_fragments/donor1:ODC`` for an unpooled track or ``depth:ODC`` for a pooled track.

When the same resource and group are processed with different aggregators, the aggregator is appended to distinguish the tracks. For example, our Getting started example produces:

- ``hg38/scores/phyloP100way:mean``
- ``hg38/scores/phyloP100way:max``

GAIn ensures that track names are unique during processing. If two binner entries would produce identical names even after adding the aggregator, the run is rejected and the entries must be distinguished using ``name``.

The HDF5 file stores track metadata rather than the original track names. The ``track_labels`` function generates readable labels from this metadata, including the aggregator. If generated labels are identical, a column-position suffix is added to distinguish them.

**Genomic coordinates**

Bin coordinates follow GAIn's standard 1-based, inclusive convention. Each bin is identified by its chromosome, start, and end positions.

Bins follow a fixed grid beginning at position 1 of each chromosome. When a specified genomic region does not align with the grid, its first and last bins are clipped to the region boundaries.

Because the same coordinate convention is used throughout GAIn, bin coordinates can be used directly with other GAIn tools. Bins from different analyses can also be aligned by their genomic coordinates, provided their reference genomes and binning grids are compatible.

Output compatibility
^^^^^^^^^^^^^^^^^^^^

The HDF5 output format changed after the GAIn 2026.9 releases. Files produced by newer versions use an updated ``/tracks`` dataset layout, identified by the root attribute ``tracks_layout`` with a value of ``2``.

Older files do not contain this attribute and use a different set of track metadata fields. The reference genome attribute was also renamed from ``input_reference_genome`` to ``reference_genome``.

The output format can be identified using ``h5py``:

.. code-block:: python

    import h5py

    with h5py.File("demo.h5", "r") as h5:
        print(h5.attrs.get("tracks_layout"))

A value of ``2`` indicates the current format, while a missing attribute indicates an older format. The ``gain_version`` attribute should not be used to identify the layout, since development builds may use newer formats while reporting an older version.

GAIn's ``read_tracks`` function supports the current layout and raises an error for older or unsupported layouts. Files written by earlier releases are not automatically converted.




Reference and technical details
-------------------------------

This section provides a reference for binning configurations, validation rules,
and output files.

Configuration reference
^^^^^^^^^^^^^^^^^^^^^^^

A run definition contains a reference genome, a ``bins`` block, and a list of
``tracks`` entries. Every entry has exactly one type key:
``position_score_tracks`` or ``fragment_score_tracks``.

**Shared settings**

.. list-table::
   :header-rows: 1
   :widths: 27 19 54

   * - Setting
     - Required or default
     - Description
   * - ``reference_genome``
     - Required
     - Reference genome resource defining chromosome names and lengths.
   * - ``bins.bin_size``
     - Required
     - Width of each genomic bin in base pairs.
   * - ``bins.regions``
     - All chromosomes
     - Chromosomes or intervals in GAIn's 1-based, inclusive notation. Their
       order determines the output row order.
   * - ``tracks``
     - Required
     - List of track entries, processed in the specified order.
   * - ``resource_query``
     - Required per entry
     - Exact resource ID or glob over resource IDs, optionally followed by a
       bracketed label filter.
   * - ``search_term``
     - Optional
     - Full-text filter combined with ``resource_query``; uses the same
       syntax as ``grr_browse -s``.

Only resources matching the entry's binner type are selected, in resource-ID
order. A query matching no resources of the appropriate type is an error.
Full-text searches require a repository index. The public IossifovLab GRR
has one; for a directory GRR, create it using ``grr_manage repo-index``.
The GRR location comes from the default GRR definition or GAIn's ``-g`` /
``--grr-directory`` options, not the YAML file (see :doc:`/grr`).

**Position-score binner settings**

.. list-table::
   :header-rows: 1
   :widths: 32 22 46

   * - Setting
     - Default
     - Description
   * - ``aggregator``
     - Resource's aggregator
     - Numerical reduction: ``max``, ``min``, ``mean``, ``median``, ``sum``,
       ``product``, or ``count``. An override applies to every matched resource.
   * - ``none_value_replacement``
     - No replacement
     - Value supplied for each genomic position without a score before
       aggregation. Without it, a completely uncovered bin is ``NaN`` except
       under ``count``, which returns ``0``.
   * - ``name``
     - Resource ID
     - Optional prefix used to distinguish tracks, especially when a resource
       is included more than once.

Position-score resources must contain one numerical (``int`` or ``float``)
score. Resources with multiple scores and non-numerical aggregators such as
``join`` or ``list`` are not supported.

**Fragment-score binner settings**

.. list-table::
   :header-rows: 1
   :widths: 29 28 43

   * - Setting
     - Default
     - Description
   * - ``pool``
     - ``true``
     - Combines matched resources into shared tracks; ``false`` keeps their
       tracks separate.
   * - ``group``
     - Automatic
     - Determines groups using the single-cell convention when the resource
       has a ``cell_meta_resource_id`` label; otherwise uses group ``all``.
   * - ``value``
     - ``{value: 1}``
     - Each fragment contributes a constant (``{value: V}``) or the numerical
       ``int``/``float`` score named by ``{score_id: S}``.
   * - ``aggregate.mode``
     - ``fragment_start``
     - How fragments contribute: ``fragment_start``, ``fragment_length``, or
       ``coverage_profile``.
   * - ``aggregate.aggregator``
     - ``sum``
     - Numerical reduction. Its supported values depend on the mode.
   * - ``aggregate.uncovered_value``
     - Mode-dependent
     - ``null`` in ``fragment_length``; ``0`` in ``coverage_profile``. Not
       supported in ``fragment_start``.
   * - ``name``
     - Resource/query name
     - Optional track-name base; also distinguishes otherwise identical
       entries with different modes.
   * - ``meta``
     - Convention's table
     - Metadata source and optional row filters, for metadata-based grouping.

The ``group`` setting accepts exactly one form:

- ``{group: NAME}``: One constant group.
- ``{group_score_id: S}``: One group per distinct value of a string-valued
  fragment score, read from its complete categorical histogram.
- ``{cell_score_id, cell_meta_column, group_meta_column}``: Groups fragments
  through a metadata table. These fields default to ``cell``, ``barcode``,
  and ``class``, respectively, so ``group: {}`` uses the convention.

The ``meta`` setting applies only to metadata-based grouping. It identifies
the table through exactly one of ``resource_id``, ``resource_label``, or
``file_name``. A local file may additionally specify ``file_format``
(``csv``, ``tsv``, or ``excel``) and ``file_separator``. The optional
``meta.filter`` is a list of conditions, each ``{column: C, value: V}``
or ``{column: C, label: L}``, all of which must match. Without a filter,
all rows are selected. See *Working with cell metadata* for examples.

The ``value`` setting and the ``group`` setting each accept only one of
their supported forms. A ``{score_id: S}`` value requires a numerical score
named ``S`` in every matched resource. A ``group_score_id`` score cannot
also supply ``value``.

Validation and limitations
^^^^^^^^^^^^^^^^^^^^^^^^^^

GAIn validates the run definition and its selected resources before
processing. The checks prevent silently producing an incomplete or
ambiguous matrix.

- **Regions:** Chromosomes must occur in the reference genome, regions must
  have valid coordinates and remain within chromosome lengths, and selected
  regions must not overlap. The reference genome is read from the YAML file,
  not from the genomic context or a command-line flag.
- **Resource selection:** Unknown keys, unmatched queries, missing full-text
  indexes, and unsupported resource or score types are reported as errors
  identifying the relevant entry.
- **Aggregation:** ``fragment_start`` accepts all seven numerical
  aggregators. ``fragment_length`` and ``coverage_profile`` reject
  ``count`` and ``product``: with overlap weighting, ``count`` would count
  base pairs rather than fragments, while ``product`` would raise each
  value to a power determined by overlap length. To total overlapping base
  pairs, use ``sum`` with ``value: {value: 1}``.
- **Missing coverage:** Fragment-score binners do not accept
  ``none_value_replacement``. Use ``aggregate.uncovered_value`` in the two
  coverage-based modes instead. It must be ``null`` or a finite number;
  explicit ``NaN`` is invalid. ``fragment_start`` does not support it.
  Numeric uncovered values are applied only within the selected region,
  including any clipped boundary bins.
- **Metadata:** A referenced ``data_frame`` resource and its required
  columns and labels must exist. Selected metadata rows cannot repeat a
  barcode for one resource and must identify at least one non-empty group.
  Labels holding lists cannot be used as filter values. ``file_format`` and
  ``file_separator`` are valid only when ``meta.file_name`` is specified.
- **Pooling and grouping:** Pooled metadata-based resources must resolve to
  the same metadata table and compatible grouping conventions. Raw-value
  grouping requires complete categorical histograms. When some pooled
  resources have a ``sample_id`` label, all of them must have one, each a
  single value without colons and distinct across the pool. Pooling
  labelled and unlabelled resources is invalid. Inconsistent or incomplete configurations are rejected.
- **Track names:** If the same track name is produced more than once, GAIn
  appends the aggregator where that resolves the ambiguity. Identical
  name/group/aggregator combinations are rejected. For example, two entries
  using different modes but the same aggregator may need distinct ``name``
  settings, because a mode is not appended to the track name.

In metadata-based grouping, fragments whose barcodes are absent from the
selected metadata rows, or whose group is empty, are dropped. Raw-value
grouping also drops fragments with missing values or values absent from the
recorded histogram. Counts of dropped fragments are logged per resource
and region at INFO level with ``-v``.

A ``--dry-run`` reports the planned resources, groups, tracks, bins, and
tasks before processing, with the group and track counts, so the matrix
width is known before the run. It can also warn when a resource appears to
have cell-related scores but lacks the ``cell_meta_resource_id`` label, when
local metadata files are used, or when a resource has no ``sample_id``
label, so that its groups are bare values or every metadata row is read.
A local metadata file makes a run dependent on that file's availability.

HDF5 format reference
^^^^^^^^^^^^^^^^^^^^^

The output contains three datasets. All three can be inspected with
``h5py``; GAIn's readers provide pandas or NumPy representations (see
*Reading the output with Python*).

.. list-table::
   :header-rows: 1
   :widths: 19 25 56

   * - Dataset
     - Shape and type
     - Contents
   * - ``/values``
     - ``(n_bins, n_tracks)``, ``float64``
     - Numerical matrix. Missing values are ``NaN``. Stored in row blocks
       with gzip compression.
   * - ``/bins``
     - ``(n_bins,)``, compound
     - ``chrom`` (fixed-length bytes, sized to the longest chromosome
       name), ``start`` and ``end`` (``int64``, 1-based inclusive).
   * - ``/tracks``
     - ``(n_tracks,)``, compound
     - Metadata for each output column, with the same fields for both
       binner types.

Each row of ``/bins`` describes the corresponding row of ``/values``.
Each row of ``/tracks`` describes the corresponding column of ``/values``.
The ``/tracks`` dataset uses the following fields:

.. list-table::
   :header-rows: 1
   :widths: 25 19 56

   * - Field
     - Type
     - Meaning
   * - ``binner_type``
     - UTF-8 string
     - ``position_score`` or ``fragment_score``.
   * - ``binner_index``
     - ``int64``
     - Zero-based index of the entry in the run definition's ``tracks`` list.
   * - ``track_index``
     - ``int64``
     - Zero-based index of the track within that entry. Together with
       ``binner_index``, this uniquely identifies a track in the file.
   * - ``resource_ids``
     - UTF-8 string
     - Comma-separated IDs of the source resources, in ID order. One ID for
       a position or unpooled fragment track; multiple for a pooled track.
   * - ``selector``
     - UTF-8 string
     - Position-score ID or fragment group identifier.
   * - ``binner_def``
     - UTF-8 string
     - Canonical JSON definition for the track, using sorted keys and no
       extra whitespace. Missing values appear as JSON ``null``.

The four text fields are variable-length UTF-8 strings. The output does not
store a track's display name: GAIn constructs labels from the metadata,
using ``track_labels``. The ``binner_def`` JSON contains:

- For position-score tracks: ``aggregator``, ``none_value_replacement``,
  and ``name``.
- For fragment-score tracks: ``aggregator``, ``mode``, ``uncovered_value``,
  ``score_id`` (``null`` when counting fragments), ``resource_query``,
  ``pool``, and ``name``.

The root attributes record the run configuration and provenance:

.. list-table::
   :header-rows: 1
   :widths: 29 71

   * - Attribute
     - Meaning
   * - ``tracks_layout``
     - ``2`` for the current ``/tracks`` layout; absent in older files.
   * - ``reference_genome``
     - Reference genome ID (older files use ``input_reference_genome``).
   * - ``bin_size``
     - Bin size in base pairs.
   * - ``regions``
     - Genomic regions in output order, as ``chrom:start-end``.
   * - ``coordinates``
     - ``1-based-inclusive``.
   * - ``gain_version``
     - GAIn version that wrote the file.
   * - ``created``
     - UTC creation time, in ISO 8601.
   * - ``metadata_files``
     - Absolute paths of any local metadata files used.
   * - ``metadata_file_sizes``
     - Their sizes in bytes, in the same order.
   * - ``metadata_file_mtimes``
     - Their modification times in UTC, in ISO 8601, in the same order.

The three ``metadata_file_*`` attributes appear only for runs that read
local metadata files. All values in ``/values`` are ``float64``, including
results of integer-valued aggregators such as ``count``. Missing values
are represented by ``NaN``, not integer nulls. Older ``/tracks`` layouts
are distinguished by the absence of ``tracks_layout``, not by
``gain_version`` (see *Output compatibility*). The preceding older layout
had fields including ``name``, ``group``, ``score_id``, and ``aggregator``;
the earliest layouts used a singular ``resource_id`` field. Files are not
automatically converted between layouts.

**Reader errors**

``read_bins`` and ``read_tracks`` raise ``ValueError`` if the corresponding
table is missing, is not a dataset, or is not a compound dataset.
``read_tracks`` also rejects absent or unsupported ``tracks_layout`` values.
``read_values`` raises ``ValueError`` if ``/values`` is missing, is not a
dataset, or is not two-dimensional. An invalid path or a non-HDF5 file
raises the ``OSError`` from ``h5py``. ``read_values`` loads the entire matrix
into memory; direct ``h5py`` access allows partial reads. The table readers
return DataFrames indexed by the corresponding positions in ``/values``
and decode text fields as Python strings.

Performance and execution
^^^^^^^^^^^^^^^^^^^^^^^^^

**Planning and parallel execution**

Run ``binning_tool RUN.yaml --dry-run`` to inspect the number of regions,
bins, tracks, and tasks before generating the matrix. One *job* is the
unit of work for one track or a group of related tracks; a *task*
is one job applied to one bundle of genomic regions. Parallel execution
is controlled using ``-j``. For the advanced example below, nine
position-score tracks form nine jobs and the two fragment tracks share
one job. With a single region bundle, this produces ten tasks (see
:ref:`binning-tool-parallelism`).

Per-cell grouping can produce thousands of tracks. The full categorical
histogram for a cell-barcode score may also require tens of megabytes per
resource. Review the group and track counts in ``--dry-run`` before
running such analyses.

**Remote resources and caching**

The public GRR's conservation bigWig files are several gigabytes each,
but a remote-capable ``pyBigWig`` can read only the requested genomic
intervals using HTTP range requests. This requires ``pyBigWig`` built
with remote (libcurl) support; the GAIn conda package supplies one, while
the PyPI ``pyBigWig`` wheel does not. If you
encounter ``this pyBigWig build has no remote-file support``, use the
conda installation or a local repository via ``--grr-directory``.

A GRR definition with ``cache_dir`` may instead download each complete
bigWig before opening it, regardless of the requested region. For the
eight conservation tracks in the example below, this can mean about
60 GB of downloads. See :doc:`/gain_getting_started_cli` for GRR caching.

**Storage formats and numerical precision**

Binning a per-base conservation score on chromosome 21 was approximately
three times slower from tabix than from bigWig. The scope of the tabix
index (whole genome or one chromosome) had no effect on that difference. Prefer bigWig when
both representations are available.

A bigWig stores scores as ``float32``. Its binned results can therefore
differ slightly from those computed from a text/tabix version of the
same scores, even though the HDF5 output uses ``float64``. A comparison
of the two formats found agreement of roughly ``1e-8`` for a ``mean`` over 10 kb
bins. These files should not be expected to be bit-identical.

Track kinds
^^^^^^^^^^^

A plugin adds a track kind with an entry point in the
``gain.binning.track_kinds`` group. The class of a kind provides ``kind``,
``parse_entry`` and ``bind``. The signature of ``bind`` is
``bind(job, grr, *, quiet=False)``, and ``bind`` returns a context manager
whose value bins one region at a time.

``binning_tool`` calls ``bind(job, grr)``. ``binning_tracks_annotator``
calls ``bind(job, grr, quiet=True)``, so a kind that does not accept the
keyword ``quiet`` fails in the annotator. With ``quiet=True``, a binding
keeps no record and logs no line for each region that it bins.

Advanced worked example
^^^^^^^^^^^^^^^^^^^^^^^

This example combines position-score and fragment-score binners in one run.
It summarizes conservation scores and gnomAD v4.1 structural variants over
two megabases of chromosome 21. The run definition is available as
:download:`binning_run.yaml <files/binning_run.yaml>`:

.. literalinclude:: files/binning_run.yaml
    :language: yaml

The first binner selects the conservation resources under ``hg38/scores/*``
using ``search_term: conservation``. In the GRR of this example, fourteen
resources were found under ``hg38/scores/``, of which
eight matched as position scores: phastCons and phyloP,
each for 7, 20, 30, and 100 species. The second binner processes
``hg38/scores/phyloP100way`` again using ``max`` in addition to its default
``mean``, yielding nine position-score tracks in total.

The third binner selects the gnomAD structural-variant resource and groups
variants using its categorical ``deletion_duplication`` score. This adds
one deletion track and one duplication track. The resource has no
``sample_id`` label, so its groups are the bare values. Its ``pool: false``
setting makes the track names start with the resource ID and keeps
``--dry-run`` from warning about the missing label. Without explicit
``value`` or ``aggregate`` options, each track counts variants starting in
each bin.

First check the planned run:

.. code-block:: bash

    binning_tool binning_run.yaml --dry-run

The example's dry run reports 11 tracks, 196 bins, one region, and ten
tasks. Its track list is:

.. code-block:: text

    hg38/scores/phastCons100way
    hg38/scores/phastCons20way
    hg38/scores/phastCons30way
    hg38/scores/phastCons7way
    hg38/scores/phyloP100way:mean
    hg38/scores/phyloP20way
    hg38/scores/phyloP30way
    hg38/scores/phyloP7way
    hg38/scores/phyloP100way:max
    hg38/cnv_collections/gnomAD.v4.1_Genome_SV:deletion
    hg38/cnv_collections/gnomAD.v4.1_Genome_SV:duplication

The same dry run summarizes the planned work:

.. code-block:: text

    regions: 1
    bins: 196
    tasks: 10
    tracks[2]: fragment_score_tracks
        resources: hg38/cnv_collections/gnomAD.v4.1_Genome_SV
        tables: none
        groups: 2
        tracks: 2
        mode: fragment_start
        uncovered_value: null

Then run:

.. code-block:: bash

    binning_tool binning_run.yaml -j 4

This writes ``binning_run.h5`` beside the YAML file. Its ``/values`` dataset
has 196 rows and 11 columns. Because the selected region does not start
on a bin boundary, the first bin spans ``chr21:20000000-20008960`` and
the last ``chr21:21995521-22000000``, with full 10,240 bp bins between.

Selected results of this run are shown below (rounded). The
column headings are abbreviated for readability; the HDF5 file retains
the source resource IDs in ``/tracks``.

.. list-table::
   :header-rows: 1
   :widths: 33 17 17 16 17

   * - Bin (chr21)
     - phyloP100way mean
     - phyloP100way max
     - Deletions
     - Duplications
   * - 20000000-20008960
     - -0.014157
     - 3.356
     - 2
     - 0
   * - 20008961-20019200
     - -0.006858
     - 3.539
     - 2
     - 3
   * - 20019201-20029440
     - 0.001797
     - 3.694
     - 4
     - 0
   * - 20029441-20039680
     - -0.017875
     - 2.491
     - 1
     - 0
   * - 20039681-20049920
     - 0.006743
     - 2.455
     - 4
     - 0

Across the full two-megabase window, the example counts 826 deletions and
179 duplications. Its eight conservation tracks are stored as remote
bigWig files of roughly 6-10 GB each. This window ran in well under a
minute when accessed through HTTP ranges;
whole-genome processing would require roughly 40 minutes of CPU per
per-base conservation track. Starting with a restricted region is therefore
recommended.

Command-line references
^^^^^^^^^^^^^^^^^^^^^^^

- :doc:`binning_tool <cli/binning_tool>`: Create an HDF5 matrix from a run definition.
- :doc:`binning_tool_view <cli/binning_tool_view>`: Print the file's tables as tab-separated text.
