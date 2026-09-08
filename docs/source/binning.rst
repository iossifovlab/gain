Binning position scores with ``binning_tool``
=============================================

Many analyses that compare genome-wide tracks against each other — correlating
conservation with chromatin accessibility, clustering a set of ATAC tracks,
feeding tracks to a model — begin the same way: cut the genome into fixed-size
bins and reduce every track to one number per bin. ``binning_tool`` does that
first step in one command. It takes a **run definition** in YAML naming the
bins and the position-score resources to bin, and writes **one HDF5 file**
holding a bins × tracks matrix with the bin coordinates and the provenance of
every track stored beside it.

.. code-block:: bash

    binning_tool run.yaml

Only ``position_score`` resources can be binned in this version. Each track
is one score of one resource, reduced over each bin by an aggregator such as
``mean`` or ``max``.


The run definition
------------------

A run definition has three parts: the reference genome, the ``bins`` block,
and a list of ``binners`` entries.

.. code-block:: yaml

    input_reference_genome: hg38/genomes/GRCh38-hg38

    bins:
      bin_size: 10240
      regions: [chr1, chr2, "chrX:1-50000000"]

    binners:
    - position_score_binner:
        resource_query: "hg38/scores/*"
        search_term: conservation
    - position_score_binner:
        resource_query: hg38/scores/phyloP100way
        aggregator: max
        none_value_replacement: 0.0

Every key is checked. A mistyped key anywhere in the file — ``aggregtor``
for ``aggregator`` — is an error that names the entry, never a silently
applied default.

Reference genome
^^^^^^^^^^^^^^^^

``input_reference_genome`` (optional) names the reference genome resource
whose chromosomes define the grid. The ``-R`` command-line flag overrides it,
and when neither is given the genome comes from the genomic context (for
example a genome configured in the GRR definition), so a run definition need
not name a genome at all where one is configured. The GRR itself is never
named in the run definition; it comes from ``-g``, ``--grr-directory``, or the
default GRR definition, as for every GAIn tool (see
:doc:`grr`). One run definition therefore runs unchanged on a laptop against
a cached GRR and on a cluster node against a node-local one.

Bins
^^^^

``bin_size`` (required) is the width of every bin in base pairs.

``regions`` (optional) lists what to bin, in GAIn's region notation: ``chr21``
is the whole chromosome and ``chr21:20000000-22000000`` is a window with
inclusive bounds. Omit ``regions`` to bin every chromosome of the reference
genome in genome order. The rows of the output follow the listed order, so
listing the chromosomes you want is also how to leave out alternate and
unplaced contigs. A window that ends before it starts, a region beyond its
chromosome's length, a chromosome the genome does not have, and two regions
that overlap are all errors reported before any work runs, naming the
offending entries.

Binner entries
^^^^^^^^^^^^^^

``binners`` (required) is a list of entries. Each entry is a one-key mapping
whose key is the binner kind; the only kind in this version is
``position_score_binner``, and its value takes these keys:

``resource_query`` (required)
    A repository search that selects the resources to bin, resolved through
    the same search that ``grr_browse`` uses: an exact resource id, or a glob
    over ids such as ``hg38/scores/*``, optionally followed by a bracketed
    label filter. Only ``position_score`` resources are taken from the
    matches, so a glob that also matches allele scores or other resource
    types is safe. Matches are ordered by resource id. A query that matches
    no position score is an error naming the entry, so a typo cannot silently
    produce a file with fewer tracks.

``search_term`` (optional)
    A full-text filter conjoined with ``resource_query``, in the syntax of
    ``grr_browse -s`` (see :doc:`grr`). It needs a repository that carries a
    full-text index; the public IossifovLab GRR does, and a directory GRR
    has one once ``grr_manage repo-index`` has been run on it. A
    ``search_term`` on a repository without an index is an error naming the
    entry.

``aggregator`` (optional)
    How the positions inside a bin are reduced to one number. The default is
    the aggregator the score itself declares as its ``position_aggregator``
    in its resource configuration, so a conservation score and a coverage
    track are each reduced the way their authors intended. Setting
    ``aggregator`` on an entry overrides that for every resource the entry
    matches. Only aggregators that produce a number are accepted, and only
    numeric (``int`` or ``float``) scores can be binned; a string-typed
    score, or an aggregator such as ``join`` or ``list`` that builds a
    string or a list, is refused at parse time. The numeric aggregators are
    ``max``, ``min``, ``mean``, ``median`` and ``count``. To bin one resource under two aggregators,
    list it in two entries.

``none_value_replacement`` (optional)
    A value fed to the aggregator for every position no record covers. By
    default there is no replacement: a bin that no record covers is ``NaN``
    in the output, so missing data cannot masquerade as a measurement in a
    correlation. Set a replacement, typically ``0.0``, when "no signal" is
    genuinely zero for the track, as it is for a coverage track.

A resource that defines more than one score is refused, with its scores
listed: a track is exactly one score, and this version offers no key to pick
among several.

Track names
^^^^^^^^^^^

Every entry expands into one track per matched resource, and a track is
named by its resource id. Tracks follow entry order, and within an entry the
order of resource ids. When the same resource occurs more than once in the
expanded list — typically the same resource matched by a glob in one entry
and named again with another aggregator in a second — every track of that
resource gets ``:<aggregator>`` appended, whichever entry came first, so
``hg38/scores/phyloP100way:mean`` and ``hg38/scores/phyloP100way:max`` sit
side by side. Two entries that would still produce one and the same track
(same resource, same aggregator) are refused, naming both entries: nothing in
the file could tell the two columns apart. Names in the output are therefore
always unique. There is no key to rename a track; renaming is a one-line
change on the tracks table after the fact.

Coordinates and the grid
^^^^^^^^^^^^^^^^^^^^^^^^

Bin coordinates are 1-based and inclusive, the convention of every GAIn
reader and of the region notation, so a bin's ``chrom:start-end`` can be
pasted into any other GAIn tool unchanged.

Bins follow a global grid anchored at position 1 of every chromosome: with a
bin size of 10240 the bins are ``1-10240``, ``10241-20480`` and so on,
regardless of where a region starts. A window that does not start on a grid
boundary therefore has its first and last bins clipped to the window — the
bin bounds always name exactly what was aggregated — and bins from different
runs, regions, or bin sizes that divide each other line up and can be joined
by coordinate.


Running the tool
----------------

.. code-block:: bash

    binning_tool RUN_DEFINITION [-o OUTPUT] [-w WORK_DIR] [--keep-work-dir] [--dry-run]
                 [-R GENOME] [-g GRR] [-j N] [--force] ...

The only positional argument is the run definition. ``-o`` names the HDF5
file to write; by default it is the run definition's path with an ``.h5``
suffix, beside it, so ``binning_tool run.yaml`` writes ``run.h5``.

Dry run
^^^^^^^

``--dry-run`` reads the run definition, resolves every query against the
GRR, checks every rule described above, prints the list of tracks the run
would produce — name, resource id, score id and aggregator — together with
the number of regions and bins, and exits without writing anything. Use it
to see what a query matched and how large the matrix will be before
committing cluster time.

Parallelism
^^^^^^^^^^^

The work is split into one task per (track, region), followed by one task
that assembles the HDF5 file. The tasks run through the same task graph as
the annotation tools, so the same flags apply: ``-j N`` sets the number of
workers, ``-N`` names a configured dask cluster, and ``--task-log-dir``
keeps a log per task. See :doc:`annotation_infrastructure` for the shared
options.

Work directory and reruns
^^^^^^^^^^^^^^^^^^^^^^^^^

Each task writes its column chunk into a work directory; the final task
assembles the file from those chunks, region by region, so at no point is
the whole matrix in memory — a genome-wide run at a small bin size is
possible on an ordinary node. The work directory is ``-w``; by default it
is a sibling of the output named after it (``run_work`` next to ``run.h5``).
A work directory the tool created is removed after a successful run;
``--keep-work-dir`` keeps it.

A run that was interrupted resumes from the chunks it had finished when it
is started again with the same work directory: only the missing chunks and
the final assembly are recomputed. A rerun of a finished run redoes only the
assembly. The chunks are keyed by everything that decides their values —
resource, score, aggregator, replacement, bin size and region — so two run
definitions sharing a work directory share exactly the chunks they compute
identically. A rerun does not notice that a resource has changed underneath
it; pass ``--force`` to recompute every chunk, for example after a resource
was updated in the GRR.


The output file
---------------

The output is one HDF5 file in a plain layout that any HDF5 reader
understands without a library beyond ``h5py``:

.. list-table::
   :header-rows: 1
   :widths: 18 82

   * - Dataset
     - Content
   * - ``/values``
     - ``float64``, shape ``(n_bins, n_tracks)``. ``NaN`` where a bin has no
       data. Stored in row blocks and gzip-compressed, so reading every
       track for one chromosome is one contiguous read and the ``NaN``- and
       zero-heavy tracks compress well.
   * - ``/bins``
     - A compound (structured) dataset of shape ``(n_bins,)`` with fields
       ``chrom`` (fixed-length bytes, sized to the longest chromosome name in
       the run), ``start`` and ``end`` (``int64``, 1-based inclusive).
   * - ``/tracks``
     - A compound dataset of shape ``(n_tracks,)`` with fields ``name``,
       ``resource_id``, ``score_id`` and ``aggregator`` (variable-length
       UTF-8 strings) and ``none_value_replacement`` (``float64``, ``NaN``
       when the entry set none).

Row *i* of ``/bins`` describes row *i* of ``/values``, and row *j* of
``/tracks`` describes column *j*. The root attributes record what the run
was, so that two files can be checked for comparability before they are
joined:

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Attribute
     - Content
   * - ``input_reference_genome``
     - The resource id of the reference genome the grid was built on.
   * - ``bin_size``
     - The bin width in base pairs.
   * - ``regions``
     - The binned regions, in row order, each as ``chrom:start-end``.
   * - ``coordinates``
     - ``"1-based-inclusive"``.
   * - ``gain_version``
     - The GAIn version that wrote the file.
   * - ``created``
     - The UTC time the file was written, in ISO 8601.

Every value in ``/values`` is a ``float64``, including the result of an
integer-valued aggregator such as ``count`` or the ``max`` of an integer
score: the matrix has one dtype, and HDF5 has no null for integers. A bin no
record covers is ``NaN`` unless the entry set ``none_value_replacement``, in
which case the replacement was fed to the aggregator for every uncovered
position and the bin holds the aggregate of that.

Two notes on scores stored as bigWig. First, a bigWig stores ``float32``, so
binning the bigWig form and the text (tabix) form of the same score does not
give bit-identical matrices; the two agree to roughly ``1e-8`` through a
``mean`` over 10 kb bins. Second, for a binning run the storage format of a
score dominates its cost: binning the same per-base conservation score over
chromosome 21 took about three times longer from its tabix form than from
its bigWig form, while the scope of the tabix index (whole genome or one
chromosome) made no difference. When a score is published in both forms,
prefer the bigWig for binning.


Reading the file back
---------------------

With ``h5py`` alone:

.. code-block:: python

    import h5py

    with h5py.File("binning_run.h5", "r") as h5:
        values = h5["values"][()]       # numpy float64 array, bins x tracks
        bins = h5["bins"][()]           # structured array: chrom, start, end
        tracks = h5["tracks"][()]       # structured array: name, resource_id, ...
        attrs = dict(h5.attrs)

    print(values.shape, attrs["bin_size"], attrs["coordinates"])
    print(tracks["name"][:3])
    print(bins[:3])

With ``pandas``, decoding the fixed-length chromosome names once:

.. code-block:: python

    import h5py
    import pandas as pd

    with h5py.File("binning_run.h5", "r") as h5:
        tracks = pd.DataFrame(h5["tracks"][()])
        for column in ("name", "resource_id", "score_id", "aggregator"):
            tracks[column] = tracks[column].str.decode("utf-8")
        bins = pd.DataFrame(h5["bins"][()])
        bins["chrom"] = bins["chrom"].str.decode("utf-8")
        values = pd.DataFrame(
            h5["values"][()], columns=tracks["name"],
            index=pd.MultiIndex.from_frame(bins))

    print(values[["hg38/scores/phyloP100way:mean",
                  "hg38/scores/phyloP100way:max"]].head())

``values`` is then a DataFrame indexed by ``(chrom, start, end)`` with one
column per track, and ``tracks`` explains each column.


A worked example
----------------

The run definition below bins the conservation scores of the public
IossifovLab GRR over two megabases of chromosome 21
(:download:`binning_run.yaml <files/binning_run.yaml>`):

.. literalinclude:: files/binning_run.yaml
    :language: yaml

The first entry is a glob over every resource under ``hg38/scores/``
narrowed by the full-text term ``conservation``; of the fourteen resources
there, eight are position scores — the phastCons and phyloP scores computed
over 7, 20, 30 and 100 species — and those are what the entry expands to.
The second entry names ``hg38/scores/phyloP100way`` again under ``max``, so
that resource is binned twice and both of its tracks carry their aggregator
in their name.

Check what the run would produce first:

.. code-block:: bash

    binning_tool binning_run.yaml --dry-run

.. code-block:: text

    tracks:
      hg38/scores/phastCons100way	hg38/scores/phastCons100way	phastCons100way	mean
      hg38/scores/phastCons20way	hg38/scores/phastCons20way	phastCons20way	mean
      hg38/scores/phastCons30way	hg38/scores/phastCons30way	phastCons30way	mean
      hg38/scores/phastCons7way	hg38/scores/phastCons7way	phastCons7way	mean
      hg38/scores/phyloP100way:mean	hg38/scores/phyloP100way	phyloP100way	mean
      hg38/scores/phyloP20way	hg38/scores/phyloP20way	phyloP20way	mean
      hg38/scores/phyloP30way	hg38/scores/phyloP30way	phyloP30way	mean
      hg38/scores/phyloP7way	hg38/scores/phyloP7way	phyloP7way	mean
      hg38/scores/phyloP100way:max	hg38/scores/phyloP100way	phyloP100way	max
    regions: 1
    bins: 196

Then run it:

.. code-block:: bash

    binning_tool binning_run.yaml -j 4

This writes ``binning_run.h5`` beside the run definition: a 196 × 9 matrix.
The window ``chr21:20000000-22000000`` does not start on a grid boundary, so
the first bin is clipped to ``chr21:20000000-20008960`` and the last to
``chr21:21995521-22000000``, while every bin in between is a full 10240 bp.

The eight scores are per-base tracks stored as bigWig files of roughly
10 GB each, read over HTTP by range, so this two-megabase run takes well
under a minute. A whole-genome run over the same eight tracks would fold
about three billion positions per track — on the order of forty minutes of
CPU per track — which is why the example restricts ``regions``: try a
window first, then widen it.

.. note::

    Reading a bigWig directly from a remote GRR needs a ``pyBigWig`` built
    with remote (libcurl) support. The conda package of GAIn ships one; the
    ``pyBigWig`` wheel on PyPI does not. If a run fails with
    ``Couldn't open https://... for reading``, cache the resources first
    (see :doc:`gain_getting_started_cli`) or point the tool at a local GRR
    with ``--grr-directory``.

Reading the result back with the ``pandas`` snippet above gives:

.. code-block:: text

    name                     hg38/scores/phyloP100way:mean  hg38/scores/phyloP100way:max
    chrom start    end
    chr21 20000000 20008960                      -0.014157                         3.356
          20008961 20019200                      -0.006858                         3.539
          20019201 20029440                       0.001797                         3.694
          20029441 20039680                      -0.017875                         2.491
          20039681 20049920                       0.006743                         2.455
