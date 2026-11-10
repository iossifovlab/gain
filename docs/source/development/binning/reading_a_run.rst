Reading a run
=============

A run of ``binning_tool`` writes one HDF5 file. The file holds the
``/values`` matrix, the ``/bins`` table with the coordinates of each row and
the ``/tracks`` table with the provenance of each column. GAIn provides
reader functions that load the three parts as numpy and pandas objects. The
user page :doc:`/binning_infrastructure` describes the layout of the file.

Reading the tables of a run
---------------------------

:func:`~gain.binning.read_tracks` and :func:`~gain.binning.read_bins` read
``/tracks`` and ``/bins`` as pandas DataFrames. Each function takes a path
or an open ``h5py.File``. The columns come from the file, the text fields
are ``str``, and the index is the position in ``/values``.

:func:`~gain.binning.read_values` reads ``/values`` as a numpy ``float64``
matrix of shape ``(n_bins, n_tracks)``, not as a DataFrame. The function
reads the whole matrix into memory.

:func:`~gain.binning.track_labels` returns one display label for each row
of a tracks DataFrame. The example below reads one column of a run and
prints its first values on ``chr1``:

.. code-block:: python

    from gain.binning import read_bins, read_tracks, read_values, track_labels

    values = read_values("binning_run.h5")  # numpy matrix, bins x tracks
    bins = read_bins("binning_run.h5")
    labels = track_labels(read_tracks("binning_run.h5"))

    column = labels.index("hg38/scores/phyloP100way:mean")
    on_chr1 = (bins["chrom"] == "chr1").to_numpy()
    print(values.shape)
    print(values[on_chr1, column][:5])

Row *i* of ``values`` belongs to row *i* of ``bins``, and column *j*
belongs to ``labels[j]``, the label of row *j* of the tracks.

.. _binning-track-labels:

Labels of the tracks
--------------------

The file does not store a name for a track, so ``track_labels`` builds each
label from ``resource_ids``, ``selector``, ``binner_type`` and
``binner_def``:

- The label of a position-score track is its resource id and its
  aggregator, for example ``hg38/scores/phyloP100way:mean``.
- The label of a fragment track is its resource, the group and the
  aggregator, for example ``frags/s1:B:sum``.

A ``name`` of the entry prefixes the resource id of a position-score track
and of an unpooled fragment track. In the label of a pooled fragment track,
the ``name`` replaces the resource query. Tracks with equal labels get
``#<position>`` appended, where the position is the column of the track.

Errors
------

``read_tracks`` raises ``ValueError`` for a file with a missing or
unsupported ``tracks_layout``. ``read_tracks`` and ``read_bins`` raise
``ValueError`` for a table that is missing, not a dataset, or not compound.
``read_values`` raises ``ValueError`` for a ``/values`` that is missing, not
a dataset, or not two-dimensional. A path that is missing or not an HDF5
file raises the ``OSError`` of ``h5py``.

API
---

The names below are exported by the package ``gain.binning``.

.. currentmodule:: gain.binning

.. autosummary::

   read_tracks
   read_bins
   read_values
   track_labels

.. autofunction:: read_tracks

.. autofunction:: read_bins

.. autofunction:: read_values

.. autofunction:: track_labels
