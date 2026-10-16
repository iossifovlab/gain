Printing a binning file with ``binning_tool_view``
==================================================

``binning_tool_view`` prints the ``/tracks``, ``/bins`` and ``/values``
tables of a ``binning_tool`` HDF5 file as tab-separated text on stdout. It
reads only files with ``tracks_layout = 2``. For any other file it prints
the error of :func:`gain.binning.h5_reader.read_tracks` on stderr and exits
with code 2. Every error the tool detects after parsing its arguments is one
line on stderr, with exit code 2, and no traceback. A mistake in the
arguments themselves, such as an unknown option or a missing ``file``, prints
the argparse usage message and an error line, then exits with code 2.

.. code-block:: bash

    binning_tool_view run.h5 --tracks
    binning_tool_view run.h5 --bins --matrix --region chr21:1-5_000_000
    binning_tool_view run.h5 --matrix --track hg38/phyloP100way:mean --no-header

Tables
------

Choose at least one of ``--tracks``, ``--bins`` and ``--matrix``. With none
of them the tool stops with an error that names the three options.

``--tracks``
    One row per track. The first column is the **track label**, then come
    the columns of ``/tracks``. The table has its own axis, so ``--tracks``
    with ``--bins`` or ``--matrix`` is an error.

``--bins``
    One row per bin, with the columns ``chrom``, ``start`` and ``end``.

``--matrix``
    The values of ``/values``: one row per bin, one column per track.
    ``--matrix`` alone prints the values only, row for row with ``--bins``.

``--bins --matrix``
    One joined table: the bin coordinates, then one column per track.

.. code-block:: bash

    binning_tool_view run.h5 --bins --matrix

.. code-block:: text

    chrom   start   end     frags/*:B:sum   scores/one:max
    chr1    1       10      2.0             1.0
    chr1    11      20      0.0             1.0
    chr2    1       10      1.0             nan

Header
------

``--no-header``
    Do not print the header line. The header is one line and is on by
    default.

``--header label|index``
    Head the matrix columns by the track label (the default) or by the
    position of the track in the file. A ``--track`` filter does not renumber
    the positions.

.. code-block:: bash

    binning_tool_view run.h5 --matrix --header index

The track label comes from :func:`gain.binning.h5_reader.track_labels`.
See "Reading the file back" in :doc:`/binning_infrastructure` for the rule.

Selecting bins
--------------

``--region REGION``
    Print only the bins that overlap a region. The option is repeatable and
    takes ``--bins`` or ``--matrix``. The forms are ``chrom``,
    ``chrom:start`` and ``chrom:start-end``, with 1-based inclusive
    coordinates. ``chrom`` selects the whole chromosome. ``chrom:start``
    runs to the end of the chromosome, as in samtools. Underscores and
    commas in a coordinate are allowed, for example ``chr1:1-5_000_000``.
    The bins are not clipped: a region that touches part of a bin selects
    the whole bin. A bin that shares no base with the region is not selected.
    With several regions the tool prints the union, each bin once, in file
    order. A chromosome that is not in ``/bins`` is an error, and so is a
    start above the end.

.. code-block:: bash

    binning_tool_view run.h5 --bins --region chr2 --region chr1:21-30

Selecting tracks
----------------

``--track LABEL``
    Print only the track with this exact label. The option is repeatable and
    takes ``--matrix`` or ``--tracks``. The columns come out in file order,
    not in option order. A label that matches no track, or more than one
    track, is an error that names the label.

.. code-block:: bash

    binning_tool_view run.h5 --tracks --track hg38/phyloP100way:mean

Values
------

The tool prints a value as Python ``float`` text, so the text is lossless:
``0.1``, ``1.0``, ``1e-300``, ``inf``. A missing value prints as ``nan``.
``start`` and ``end`` print as integers. A tab or a newline inside a string
value becomes a space.

The tool reads ``/values`` one block of rows at a time, and reads only the
selected track columns of each block. The memory for the values stays flat
for a large file. The memory for ``/bins`` and for the selected rows grows
with the number of bins.

A closed pipe ends the tool without a message:

.. code-block:: bash

    binning_tool_view run.h5 --matrix | head

Option reference
----------------

.. argparse::
    :module: gain.binning.view_cli
    :func: _build_argument_parser
    :prog: binning_tool_view
