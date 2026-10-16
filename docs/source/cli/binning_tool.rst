binning_tool
============

``binning_tool`` bins genomic scores into a fixed genome grid. It reads a
**run definition** in YAML that names the reference genome, the bins and the
``position_score`` and ``fragment_score`` resources to bin. It writes one
HDF5 file with a bins × tracks matrix. :doc:`/binning_infrastructure`
describes the run definition and the output file.

.. code-block:: bash

    binning_tool run.yaml

Examples
--------

.. code-block:: bash

    binning_tool run.yaml --dry-run

Resolves every query of ``run.yaml`` against the GRR and prints the tracks,
regions, bins and tasks of the run. Writes nothing.

.. code-block:: bash

    binning_tool run.yaml -o run.h5 -j 8

Bins the scores of ``run.yaml`` with 8 parallel workers and writes the
result to ``run.h5``.

.. code-block:: bash

    binning_tool run.yaml --force

Computes every chunk again, also the chunks that an earlier run left in the
work directory.

Running the tool
----------------

The full list of options is in the :ref:`binning-tool-option-reference`
at the end of this page.

The only positional argument is the run definition. ``-o`` names the HDF5
file to write; by default it is the run definition's path with an ``.h5``
suffix, beside it, so ``binning_tool run.yaml`` writes ``run.h5``.

What the run describes — the genome, the grid and the tracks — is the run
definition's business; the command line only says where to find the GRR, where
to put the output, and how to run the work.

Dry run
^^^^^^^

``--dry-run`` reads the run definition, resolves every query against the
GRR, checks every rule of :ref:`binning-run-definition`, and exits without writing an
output file or a work directory. Resolving a raw-value grouping
(``group: {group_score_id: ...}``) still reads each resource's full
histogram, and a caching GRR stores those files in its cache.
It prints the list of tracks the run would produce — name, resource ids
(comma-separated), score id (empty for a fragment count) and aggregator —
together with the number of regions, bins and tasks. For every
``fragment_score_binner`` entry it then reports the resources the entry
matched, the metadata tables it reads (resource ids, or the absolute paths
of local files), its group and track counts, every warning its
resolution raised, and the mode and uncovered value of its tracks. For the
two single-cell entries of :ref:`binning-fragment-score-entries`, the convention entry and the explicit
unpooled one, in one run definition binning a small two-chromosome genome
at 100 bp:

.. code-block:: text

    tracks:
      sc/atac_fragments/*:ODC	sc/atac_fragments/donor1,sc/atac_fragments/donor2		sum
      sc/atac_fragments/*:PVALB	sc/atac_fragments/donor1,sc/atac_fragments/donor2		sum
      sc/atac_fragments/donor1:ODC	sc/atac_fragments/donor1		sum
      sc/atac_fragments/donor1:PVALB	sc/atac_fragments/donor1		sum
      sc/atac_fragments/donor2:ODC	sc/atac_fragments/donor2		sum
      sc/atac_fragments/donor2:PVALB	sc/atac_fragments/donor2		sum
    regions: 2
    bins: 14
    tasks: 3
    binners[0]: fragment_score_binner
        resources: sc/atac_fragments/donor1, sc/atac_fragments/donor2
        tables: sc/cell_meta_data
        groups: 2
        tracks: 2
        mode: fragment_start
        uncovered_value: null
    binners[1]: fragment_score_binner
        resources: sc/atac_fragments/donor1, sc/atac_fragments/donor2
        tables: sc/cell_meta_data
        groups: 2
        tracks: 4
        mode: fragment_start
        uncovered_value: null

and for the local-file entry, the report carries the warning:

.. code-block:: text

    binners[0]: fragment_score_binner
        resources: sc/atac_fragments/donor1, sc/atac_fragments/donor2
        tables: /tmp/binning-demo/cells.csv
        groups: 2
        tracks: 2
        warning: the cell metadata table '/tmp/binning-demo/cells.csv' is a local file; the run is not reproducible elsewhere
        mode: fragment_start
        uncovered_value: null

Use it to see what a query matched, how large the matrix will be and how
the work will be cut before committing cluster time.

.. _binning-tool-parallelism:

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
aggregator, replacement, group, the fragment entry's grouping, metadata
settings, mode and uncovered value, bin size and region — so two run definitions sharing a work
directory share exactly the chunks they compute identically. A local
metadata file (``meta: {file_name: ...}``) is an input of every task, so
editing it recomputes the chunks on the next run. A rerun does not notice
that a resource, or a metadata table read from a ``data_frame`` resource,
has changed underneath it; pass ``--force`` to recompute every chunk, for
example after a resource was updated in the GRR.


See also
--------

* :doc:`/binning_infrastructure`: the run definition, the output file and a
  worked example.
* :doc:`binning_tool_view`: print the tables of a ``binning_tool`` file as
  tab-separated text.


.. _binning-tool-option-reference:

Option reference
----------------

.. argparse::
    :module: gain.binning.cli
    :func: _build_argument_parser
    :prog: binning_tool
