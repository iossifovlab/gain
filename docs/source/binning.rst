Binning genomic scores with ``binning_tool``
============================================

Many analyses that compare genome-wide tracks against each other — correlating
conservation with chromatin accessibility, clustering a set of ATAC tracks,
feeding tracks to a model — begin the same way: cut the genome into fixed-size
bins and reduce every track to one number per bin. ``binning_tool`` does that
first step in one command. It takes a **run definition** in YAML naming the
bins and the resources to bin, and writes **one HDF5 file** holding a
bins × tracks matrix with the bin coordinates and the provenance of every
track stored beside it.

.. code-block:: bash

    binning_tool run.yaml

Two kinds of resource can be binned. A ``position_score`` resource gives one
track per resource: one score, reduced over each bin by an aggregator such
as ``mean`` or ``max``. A ``fragment_score`` resource — the fragments of a
single-cell ATAC sample, or any other collection of intervals — gives one
track per *group* of fragments: every fragment, or the fragments of each
cell type, or the fragments of each cell, counted or summed over the bin.


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

``input_reference_genome`` (required) names the reference genome resource
whose chromosomes define the grid. It is the run definition's business alone:
there is no command-line flag for it, and a genome offered by the genomic
context is not consulted. The chromosome lengths decide how many bins there
are and what each one spans, so one run definition always describes one
matrix — whoever runs it, and wherever.

The GRR itself is never named in the run definition; it comes from ``-g``,
``--grr-directory``, or the default GRR definition, as for every GAIn tool
(see :doc:`grr`). One run definition therefore runs unchanged on a laptop
against a cached GRR and on a cluster node against a node-local one.

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
whose key is the binner kind, one of two: ``position_score_binner`` bins
``position_score`` resources, and ``fragment_score_binner`` bins
``fragment_score`` resources. Both kinds select their resources the same
way:

``resource_query`` (required)
    A repository search that selects the resources to bin, resolved through
    the same search that ``grr_browse`` uses: an exact resource id, or a glob
    over ids such as ``hg38/scores/*``, optionally followed by a bracketed
    label filter. Only resources of the entry's kind are taken from the
    matches — ``position_score`` resources for a ``position_score_binner``,
    ``fragment_score`` resources for a ``fragment_score_binner`` — so a glob
    that also matches other resource types is safe. Matches are ordered by
    resource id. A query that matches no resource of the kind is an error
    naming the entry, so a typo cannot silently produce a file with fewer
    tracks.

``search_term`` (optional)
    A full-text filter conjoined with ``resource_query``, in the syntax of
    ``grr_browse -s`` (see :doc:`grr`). It needs a repository that carries a
    full-text index; the public IossifovLab GRR does, and a directory GRR
    has one once ``grr_manage repo-index`` has been run on it. A
    ``search_term`` on a repository without an index is an error naming the
    entry.

The other keys depend on the kind.

Position-score entries
^^^^^^^^^^^^^^^^^^^^^^

A ``position_score_binner`` entry makes one track per matched resource and
takes, besides ``resource_query`` and ``search_term``, these keys:

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
    ``max``, ``min``, ``mean``, ``median``, ``sum``, ``product`` and
    ``count``. To bin one
    resource under two aggregators, list it in two entries.

``none_value_replacement`` (optional)
    A value fed to the aggregator for every position no record covers. By
    default there is no replacement: a bin that no record covers is ``NaN``
    in the output, so missing data cannot masquerade as a measurement in a
    correlation. Set a replacement, typically ``0.0``, when "no signal" is
    genuinely zero for the track, as it is for a coverage track.

A resource that defines more than one score is refused, with its scores
listed: a track is exactly one score, and this version offers no key to pick
among several.

Fragment-score entries
^^^^^^^^^^^^^^^^^^^^^^

A ``fragment_score_binner`` entry reduces, per bin, the fragments of the
matched resources that **start** in that bin. A fragment belongs to the bin
containing its start position and to no other bin, however far it extends:
adjacent bins and adjacent regions see each fragment exactly once, so the
column of a fragment count sums to the number of fragments, and pooling
several resources is exact.

For a resource that follows the single-cell convention described below,
the query is all an entry needs:

.. code-block:: yaml

    - fragment_score_binner:
        resource_query: "sc/atac_fragments/*"

Here ``sc/atac_fragments/donor1`` and ``sc/atac_fragments/donor2`` are two
samples of a single-cell ATAC study, each labelled with its cell metadata
table, ``sc/cell_meta_data``, and with its sample. The entry pools both
samples and sums the read pairs of every fragment per cell type, one track
per cell type. The same entry with every default spelled out, and with the
two samples kept apart, is:

.. code-block:: yaml

    - fragment_score_binner:
        resource_query: "sc/atac_fragments/*"
        pool: false
        group:
          cell_score_id: cell
          cell_meta_column: barcode
          group_meta_column: class
        aggregate:
          score: count
          aggregator: sum
        meta:
          resource_label: cell_meta_resource_id
          filter:
          - column: sample_id
            label: sample_id

Besides ``resource_query`` and ``search_term``, an entry takes these keys:

``pool`` (optional, default ``true``)
    With ``true``, every matched resource is merged by position into one
    set of tracks: a pooled track counts the fragments of all the samples
    together. With ``false``, each resource is binned on its own and gets
    its own tracks.

``group`` (optional)
    How fragments are split into tracks, in exactly one of three forms;
    giving keys of two forms at once is an error.

    * ``{group: NAME}`` — one track, holding every fragment, whose group is
      the constant ``NAME``.
    * ``{cell_score_id, cell_meta_column, group_meta_column}`` — one track
      per group of a metadata table, which ``meta`` names.
      ``cell_score_id`` is the fragment score carrying a fragment's cell
      barcode, ``cell_meta_column`` the table column holding the barcode
      and ``group_meta_column`` the column holding the cell's group. Each
      key is optional and defaults to the convention's — ``cell``,
      ``barcode`` and ``class`` — so ``group: {}`` groups by the
      convention throughout. The tracks are the distinct non-empty groups
      of the rows each job selects, in sorted order: the rows of every
      resource of a pooled entry together, or, with ``pool: false``, of
      each resource on its own, so two samples may get different sets of
      tracks.
    * ``{group_score_id: S}`` — one track per distinct value of the
      ``str`` score ``S``; see `Grouping by a raw value`_. ``meta`` does
      not go with this form.

    Without ``group``, the resource's labels decide (see `Defaults`_).

``aggregate`` (optional)
    What is reduced per fragment, and how. ``{score: S, aggregator: A}``
    reduces the value of the numeric (``int`` or ``float``) fragment score
    ``S``; ``{value: V, aggregator: A}`` reduces the constant number ``V``
    for every fragment, so ``{value: 1, aggregator: sum}`` counts
    fragments. ``aggregator`` is one of ``sum``, ``count``, ``mean``,
    ``max``, ``min``, ``median`` and ``product``, and defaults to ``sum``.
    One aggregator applies to every resource of the entry. Without
    ``aggregate``, see `Defaults`_.

``meta`` (optional)
    Where the table mapping barcodes to groups comes from: exactly one of

    * ``resource_id: ID`` — the ``data_frame`` resource ``ID``;
    * ``resource_label: LABEL`` — the ``data_frame`` resource that the
      fragment resource's label ``LABEL`` names;
    * ``file_name: PATH`` — a local file; see `Local metadata files`_.
      ``file_format`` (``csv``, ``tsv`` or ``excel``) and
      ``file_separator`` go with it and only with it. Without
      ``file_format`` the format follows the file's suffix: ``.tsv`` is
      read as ``tsv``, ``.xls`` and ``.xlsx`` as ``excel``, and anything
      else as ``csv``. Only ``.xlsx`` is readable out of the box: a legacy
      ``.xls`` file also needs the ``xlrd`` package, which GAIn does not
      install, and without it the run is refused when it resolves.

    and ``filter``, a list of conjuncts selecting the rows of one sample.
    Each is ``{column: C, value: V}``, the column ``C`` equal to the
    literal ``V``, or ``{column: C, label: L}``, the column ``C`` equal to
    the fragment resource's label ``L`` — so one filter selects each
    sample's own rows. A row must satisfy every conjunct; no ``filter``
    selects every row. A label whose value is a list cannot be a filter
    operand. ``meta`` goes only with the metadata form of ``group`` (or
    with no ``group`` on a resource the convention groups). Without it,
    the convention's table is read.

There is no ``none_value_replacement`` key on this kind: what an empty bin
holds is decided by the aggregator (see `Empty bins`_).

The single-cell convention
""""""""""""""""""""""""""

A single-cell fragment resource that names its metadata the conventional
way needs no ``group`` and no ``meta``. The convention is:

.. list-table::
   :header-rows: 1
   :widths: 30 40 30

   * - Where
     - What
     - Name
   * - fragment resource label
     - the id of the ``data_frame`` resource holding the cell metadata
     - ``cell_meta_resource_id``
   * - fragment resource label
     - this sample's key in that table
     - ``sample_id``
   * - metadata table column
     - the sample key
     - ``sample_id``
   * - metadata table column
     - the cell barcode
     - ``barcode``
   * - metadata table column
     - the cell's group
     - ``class``

and the barcode is the fragment score ``cell``. The default ``meta`` is
therefore the table that ``cell_meta_resource_id`` names, restricted to the
rows whose ``sample_id`` column equals the resource's ``sample_id`` label —
spelled out, ``{resource_label: cell_meta_resource_id, filter: [{column:
sample_id, label: sample_id}]}``.

All of it is checked before any work runs, with an error naming the entry
and the resource: the label exists and names a ``data_frame`` resource the
repository has; the table has the columns the entry uses; every filter
label exists on the resource; no barcode occurs on two of one resource's
selected rows; and the selected rows name at least one group. A table that
predates the convention is used through the explicit ``group`` and
``meta`` keys.

Defaults
""""""""

Without ``group``, grouping is on if and only if the resource carries the
``cell_meta_resource_id`` label: the label is the declaration, and nothing
is guessed from the names of a resource's scores. A labelled resource is
grouped as with ``group: {}``; any other resource is the one group ``all``,
so its track is named ``<resource id>:all``. A resource that has ``cell``
and ``count`` scores but no label is binned as the single ``all`` track,
and ``--dry-run`` warns about it.

Without ``aggregate``, an entry sums the ``int`` score named ``count`` when
the resource has one — the read pairs behind each fragment — and otherwise
counts fragments, as ``{value: 1, aggregator: sum}``.

A pooled entry is one job reading one table, so its resources must agree:
on whether they carry ``cell_meta_resource_id`` (when ``group`` is
omitted), on the one metadata table their ``meta`` names, and (when
``aggregate`` is omitted) on whether they have an ``int`` ``count`` score.
Resources that disagree are an error listing both sides; give the setting
explicitly, or ``pool: false``.

Empty bins
""""""""""

A bin in which no fragment of a group starts holds the aggregator's empty
value: ``0`` for ``count`` and ``sum``, which have an answer for no
fragments, and ``NaN`` for ``mean``, ``max``, ``min``, ``median`` and
``product``, which have none. A chromosome a resource has no fragments on
is treated exactly like empty bins, by the same rule, so one sample
lacking a contig neither fails the run nor turns a pooled column into
``NaN``.

Dropped fragments
"""""""""""""""""

With a grouping, a fragment reaches no track when its barcode is not among
its resource's selected rows, when its row's group is empty, or — when
grouping by a raw value — when its value is missing or is not one the
score's histogram lists. Such fragments are dropped, counted per resource
and region, and the count is logged at ``INFO`` level (``-v``):

.. code-block:: text

    INFO:gain.binning.fragment_binner:sc/atac_fragments/donor1 chr1:1-1000: 2 fragments dropped, their barcode absent from the cell metadata rows or of no group

Grouping by a raw value
"""""""""""""""""""""""

``group: {group_score_id: S}`` makes one track per distinct value of the
``str`` score ``S``. Grouped by the ``cell`` score, that is one track per
cell: a per-cell matrix.

.. code-block:: yaml

    - fragment_score_binner:
        resource_query: "sc/atac_fragments/*"
        group: {group_score_id: cell}

The values are not discovered by reading the data: they are read, when the
entry is resolved, from the score's **full** categorical histogram in the
resource's statistics. The statistics must therefore be built
(``grr_manage resource-stats``) and, in a repository that keeps them in
DVC, pulled; a resource without the full histogram, or with a truncated
one, is an error naming it. A score whose values pass the default
categorical histogram's limit of 100 values keeps the full histogram only
when its configuration declares ``histogram: {type: categorical}``. The
grouping score may not be the aggregated score.

Unpooled, a track's group is the bare value. Pooled, the same barcode
recurs in every 10x sample while naming a different cell, so the group is
prefixed with the resource's ``sample_id`` label, ``<sample_id>:<value>``.
Every pooled resource must therefore carry a ``sample_id`` label of one
value, without a ``:``, and no two pooled resources may share one; any of
these is an error naming the resources, which suggests ``pool: false``.

Mind the size before running: a per-cell matrix has one column per cell,
which is thousands of columns per sample, and the full histogram of a
``cell`` score is itself tens of megabytes per resource. ``--dry-run``
reports the group count read from the histograms, so the width of the
matrix is known before it is built.

Local metadata files
""""""""""""""""""""

``meta: {file_name: PATH}`` reads the metadata table from a local file
instead of a ``data_frame`` resource, with the same column conventions. A
relative ``PATH`` is read from the directory of the run definition, not
from the current directory:

.. code-block:: yaml

    - fragment_score_binner:
        resource_query: "sc/atac_fragments/*"
        meta:
          file_name: cells.csv
          filter:
          - {column: sample_id, label: sample_id}

A run that reads a local file can be repeated only where that file is, so
``--dry-run`` warns about it, and the output file records which file it
was: its absolute path, size and modification time, in the
``metadata_files``, ``metadata_file_sizes`` and ``metadata_file_mtimes``
root attributes (see `The output file`_).

Track names
^^^^^^^^^^^

Every track has a name, and names in the output are always unique.

* A position-score track is named by its resource id.
* A fragment track of an unpooled entry is named
  ``<resource id>:<group>``; in the plain case, with no grouping,
  ``<resource id>:all``.
* A fragment track of a pooled entry is named
  ``<resource_query>:<group>``, the query string as written in the entry,
  which is the one name every pooled entry has — for example
  ``sc/atac_fragments/*:ODC``. Grouped by a raw value, the group carries
  its sample, so a pooled track is
  ``<resource_query>:<sample_id>:<value>``.

Tracks follow entry order, and within an entry the order of resource ids
and then of groups. When the same name and group occur more than once in
the expanded list — typically the same resource matched by a glob in one
entry and named again with another aggregator in a second — every track of
that set gets ``:<aggregator>`` appended, whichever entry came first, so
``hg38/scores/phyloP100way:mean`` and ``hg38/scores/phyloP100way:max`` sit
side by side, as do ``sc/atac_fragments/*:ODC:sum`` and
``sc/atac_fragments/*:ODC:mean``. Two entries that would still produce one
and the same track (same name, group and aggregator) are refused, naming
both entries: nothing in the file could tell the two columns apart. There
is no key to rename a track; renaming is a one-line change on the tracks
table after the fact.

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
                 [--task-budget BP] [-g GRR] [-j N] [--force] ...

The only positional argument is the run definition. ``-o`` names the HDF5
file to write; by default it is the run definition's path with an ``.h5``
suffix, beside it, so ``binning_tool run.yaml`` writes ``run.h5``.

What the run describes — the genome, the grid and the tracks — is the run
definition's business; the command line only says where to find the GRR, where
to put the output, and how to run the work.

Dry run
^^^^^^^

``--dry-run`` reads the run definition, resolves every query against the
GRR, checks every rule described above, and exits without writing an
output file or a work directory. Resolving a raw-value grouping
(``group: {group_score_id: ...}``) still reads each resource's full
histogram, and a caching GRR stores those files in its cache.
It prints the list of tracks the run would produce — name, resource ids
(comma-separated), score id (empty for a fragment count) and aggregator —
together with the number of regions, bins and tasks. For every
``fragment_score_binner`` entry it then reports the resources the entry
matched, the metadata tables it reads (resource ids, or the absolute paths
of local files), its group and track counts, and every warning its
resolution raised. For the two single-cell entries above, the convention
entry and the explicit unpooled one, in one run definition binning a small
two-chromosome genome at 100 bp:

.. code-block:: text

    tracks:
      sc/atac_fragments/*:ODC	sc/atac_fragments/donor1,sc/atac_fragments/donor2	count	sum
      sc/atac_fragments/*:PVALB	sc/atac_fragments/donor1,sc/atac_fragments/donor2	count	sum
      sc/atac_fragments/donor1:ODC	sc/atac_fragments/donor1	count	sum
      sc/atac_fragments/donor1:PVALB	sc/atac_fragments/donor1	count	sum
      sc/atac_fragments/donor2:ODC	sc/atac_fragments/donor2	count	sum
      sc/atac_fragments/donor2:PVALB	sc/atac_fragments/donor2	count	sum
    regions: 2
    bins: 14
    tasks: 3
    binners[0]: fragment_score_binner
        resources: sc/atac_fragments/donor1, sc/atac_fragments/donor2
        tables: sc/cell_meta_data
        groups: 2
        tracks: 2
    binners[1]: fragment_score_binner
        resources: sc/atac_fragments/donor1, sc/atac_fragments/donor2
        tables: sc/cell_meta_data
        groups: 2
        tracks: 4

and for the local-file entry, the report carries the warning:

.. code-block:: text

    binners[0]: fragment_score_binner
        resources: sc/atac_fragments/donor1, sc/atac_fragments/donor2
        tables: /tmp/binning-demo/cells.csv
        groups: 2
        tracks: 2
        warning: the cell metadata table '/tmp/binning-demo/cells.csv' is a local file; the run is not reproducible elsewhere

Use it to see what a query matched, how large the matrix will be and how
the work will be cut before committing cluster time.

Parallelism
^^^^^^^^^^^

The work is split into one task per *job* and *bundle* of regions,
followed by one task that assembles the HDF5 file. A job is what one task
binds to: one track of a position-score entry, one resource of an unpooled
fragment entry with all its group tracks, or the whole of a pooled
fragment entry, however many resources it pools. Consecutive regions of
the run definition are packed, in order, into bundles of at most
``--task-budget`` bases (default 50,000,000); a region is never split, so a
chromosome longer than the budget is a task of its own, while the hundreds
of alternate and unplaced contigs of a human genome — under two percent of
its bases — pack into a handful of tasks instead of one each. The budget
only decides how many tasks there are: the file is the same whatever its
value. The budget counts bases, not reads: a pooled job reads every one of
its resources over each base, so its tasks are that many times heavier,
and a pooled job is never split across its samples.

Both ends of the dial are reachable. A budget of **1** is one task per
region — a region is never split, so the smallest budget that cuts at all
cuts everywhere. A budget of **0 or less** is no budget at all: the whole
run goes into one task per job. A task opens its job's resources once,
whatever the budget, so the whole-run task opens them once for the run.

Which end helps depends on how many jobs a run has, because a job's
work is never split across tasks at budget 0: the achievable parallelism
is then exactly the number of jobs. A run of hundreds of tracks on a few
workers loses nothing and sheds tasks; a run of two or three tracks with
``-j 8`` leaves most of the workers idle and should keep the default.
Note too that a task is the unit a rerun repeats: at budget 0 a task that
fails part-way recomputes its whole job next time, where at the default
a rerun keeps every bundle that finished.

The tasks run through the same task graph as the annotation tools, so the
same flags apply: ``-j N`` sets the number of
workers, ``-N`` names a configured dask cluster, and ``--task-log-dir``
keeps a log per task. The genomic-context flags (``-g``,
``--grr-directory``, ``-R``) and the verbosity flags (``--verbose``,
``--logfile``) are likewise the shared ones. Run ``binning_tool --help``
for the full list.

Work directory and reruns
^^^^^^^^^^^^^^^^^^^^^^^^^

Each task writes one column chunk per track and region of its bundle into
a work directory; the final task assembles the file from those chunks,
region by region, so at no point is the whole matrix in memory — a
genome-wide run at a small bin size is possible on an ordinary node. The
work directory is ``-w``; by default it is a sibling of the output named
after it (``run_work`` next to ``run.h5``), and the task-status directory
lives inside it. A work directory the tool created is removed after a
successful run; ``--keep-work-dir`` keeps it.

A run that was interrupted resumes from the tasks it had finished when it
is started again with the same work directory and budget: a task whose
chunks are all present is skipped, a task missing any of its chunks is
computed again in full, and the file is assembled if it is missing. A run
whose chunks
and output are all present does nothing. The chunks are keyed by everything
in the run definition that decides their values — resources, score,
aggregator, replacement, group, the fragment entry's grouping and metadata
settings, bin size and region — so two run definitions sharing a work
directory share exactly the chunks they compute identically. A local
metadata file (``meta: {file_name: ...}``) is an input of every task, so
editing it recomputes the chunks on the next run. A rerun does not notice
that a resource, or a metadata table read from a ``data_frame`` resource,
has changed underneath it; pass ``--force`` to recompute every chunk, for
example after a resource was updated in the GRR.


The output file
---------------

The output is one HDF5 file in a plain layout that any HDF5 reader
understands without a library beyond ``h5py``. It holds three datasets:

``/values``
    ``float64``, shape ``(n_bins, n_tracks)``. ``NaN`` where a bin has no
    data. Stored in row blocks and gzip-compressed, so reading every track
    for one chromosome is one contiguous read and the ``NaN``- and
    zero-heavy tracks compress well.

``/bins``
    A compound (structured) dataset of shape ``(n_bins,)`` with fields
    ``chrom`` (fixed-length bytes, sized to the longest chromosome name in
    the run), ``start`` and ``end`` (``int64``, 1-based inclusive).

``/tracks``
    A compound dataset of shape ``(n_tracks,)`` with fields ``name``,
    ``resource_ids``, ``group``, ``score_id`` and ``aggregator``
    (variable-length UTF-8 strings) and ``none_value_replacement``
    (``float64``, ``NaN`` when the entry set none). ``resource_ids`` lists
    the resources a track was computed from, comma-separated in
    resource-id order: one id for a position-score track and for an
    unpooled fragment track, every matched id for a pooled one. ``group``
    is the track's group, empty for a position-score track. ``score_id``
    is empty for a fragment track that counts fragments.

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
   * - ``metadata_files``
     - Only when the run read a local metadata file: the absolute path of
       each local file the run read.
   * - ``metadata_file_sizes``
     - Only with ``metadata_files``: each file's size in bytes, in the
       same order.
   * - ``metadata_file_mtimes``
     - Only with ``metadata_files``: each file's modification time, UTC,
       in ISO 8601, in the same order.

.. note::

    **The** ``/tracks`` **layout changed after the 2026.9 releases.** A
    file written by a 2026.9 release of GAIn has a ``resource_id`` column
    holding one id and no ``group`` column; a later file has
    ``resource_ids`` and ``group`` instead, for every track, position
    scores included. Code reading ``resource_id`` must switch to
    ``resource_ids``. Tell the two layouts apart by the field itself —
    ``'resource_ids' in h5['tracks'].dtype.names`` — not by the
    ``gain_version`` root attribute: a development build made after the
    2026.9.7 release reports a 2026.9 version but already writes the new
    layout.

Every value in ``/values`` is a ``float64``, including the result of an
integer-valued aggregator such as ``count`` or the ``max`` of an integer
score: the matrix has one dtype, and HDF5 has no null for integers. For a
position-score track, a bin no record covers is ``NaN`` unless the entry
set ``none_value_replacement``, in which case the replacement was fed to
the aggregator for every uncovered position and the bin holds the
aggregate of that. For a fragment track, an empty bin holds the
aggregator's empty value (see `Empty bins`_).

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
        tracks = h5["tracks"][()]       # structured array: name, resource_ids, group, ...
        attrs = dict(h5.attrs)

    print(values.shape, attrs["bin_size"], attrs["coordinates"])
    print(tracks["name"][:3])
    print(tracks["resource_ids"][-1], tracks["group"][-1])
    print(bins[:3])

With ``pandas``, decoding the fixed-length chromosome names once:

.. code-block:: python

    import h5py
    import pandas as pd

    with h5py.File("binning_run.h5", "r") as h5:
        tracks = pd.DataFrame(h5["tracks"][()])
        for column in ("name", "resource_ids", "group", "score_id",
                       "aggregator"):
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
IossifovLab GRR over two megabases of chromosome 21, together with the
structural variants of gnomAD v4.1 starting in each bin
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

The third entry bins a ``fragment_score`` resource: the gnomAD v4.1 genome
structural-variant collection, whose records are intervals. It is grouped by
the raw value of its ``deletion_duplication`` score, whose statistics list
two values, ``deletion`` and ``duplication``, so it makes two tracks. The
resource has no ``int`` score named ``count``, so each track counts the
variants that start in each bin. A single resource carries no
``sample_id`` label, so the entry sets ``pool: false`` and the groups are
the bare values.

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
      hg38/cnv_collections/gnomAD.v4.1_Genome_SV:deletion	hg38/cnv_collections/gnomAD.v4.1_Genome_SV		sum
      hg38/cnv_collections/gnomAD.v4.1_Genome_SV:duplication	hg38/cnv_collections/gnomAD.v4.1_Genome_SV		sum
    regions: 1
    bins: 196
    tasks: 10
    binners[2]: fragment_score_binner
        resources: hg38/cnv_collections/gnomAD.v4.1_Genome_SV
        tables: none
        groups: 2
        tracks: 2

Nine position-score tracks are nine jobs, and the fragment entry's two
tracks are one job, so the single region makes ten tasks.

Then run it:

.. code-block:: bash

    binning_tool binning_run.yaml -j 4

This writes ``binning_run.h5`` beside the run definition: a 196 × 11 matrix.
The window ``chr21:20000000-22000000`` does not start on a grid boundary, so
the first bin is clipped to ``chr21:20000000-20008960`` and the last to
``chr21:21995521-22000000``, while every bin in between is a full 10240 bp.

The eight scores are per-base tracks stored as bigWig files of 6 to 10 GB
each, read over HTTP by range, so this two-megabase run takes well under a
minute. A whole-genome run over the same eight tracks would fold
about three billion positions per track — on the order of forty minutes of
CPU per track — which is why the example restricts ``regions``: try a
window first, then widen it.

.. note::

    Reading a bigWig directly from a remote GRR needs a ``pyBigWig`` built
    with remote (libcurl) support. The conda package of GAIn ships one; the
    ``pyBigWig`` wheel on PyPI does not. If a run fails with
    ``this pyBigWig build has no remote-file support``, install GAIn from
    conda, or point the tool at a local copy of the repository with
    ``--grr-directory``. A GRR definition with a ``cache_dir`` (see
    :doc:`gain_getting_started_cli`) also works, but it downloads each
    bigWig in full before opening it — about 60 GB for the eight scores of
    this example — whatever the size of the window.

Reading the result back with the ``pandas`` snippet above gives:

.. code-block:: text

    name                     hg38/scores/phyloP100way:mean  hg38/scores/phyloP100way:max
    chrom start    end
    chr21 20000000 20008960                      -0.014157                         3.356
          20008961 20019200                      -0.006858                         3.539
          20019201 20029440                       0.001797                         3.694
          20029441 20039680                      -0.017875                         2.491
          20039681 20049920                       0.006743                         2.455

and the two fragment tracks, selected the same way, count the deletions
and duplications that start in each bin — 826 and 179 over the whole
window:

.. code-block:: text

    name                     hg38/cnv_collections/gnomAD.v4.1_Genome_SV:deletion  hg38/cnv_collections/gnomAD.v4.1_Genome_SV:duplication
    chrom start    end
    chr21 20000000 20008960                                                2.0                                                  0.0
          20008961 20019200                                                2.0                                                  3.0
          20019201 20029440                                                4.0                                                  0.0
          20029441 20039680                                                1.0                                                  0.0
          20039681 20049920                                                4.0                                                  0.0
