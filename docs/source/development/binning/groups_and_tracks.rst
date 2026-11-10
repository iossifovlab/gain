Groups and tracks
=================

A run of ``binning_tool`` can take a long time, and a wrong grouping shows
only in its file. GAIn provides functions that resolve an entry against a
repository without a run, so a notebook sees the groups and the tracks of
the entry first. Each function raises
:class:`~gain.binning.RunDefinitionError` for an entry that ``binning_tool``
refuses, with the same message. The message names the entry as
``binners[0]``, and ``binning_tool`` names it by its position in the
``binners`` list. ``fragment_tracks`` takes a ``binner_index`` and names
the entry as ``binners[<binner_index>]``.

Previewing the groups and tracks of an entry
--------------------------------------------

:func:`~gain.binning.cell_groups` gives the group that ``binning_tool``
puts each key of an entry in. The key of a cell grouping is the barcode of
a cell, and the key of a value grouping is a value of its score. The result
is a DataFrame with the columns ``resource_id``, ``score_value`` and
``group``. A fragment with a key that no row lists reaches no track, so the
result shows which cells an entry drops. An entry with a constant group
gives no row.

:func:`~gain.binning.fragment_tracks` gives the ``/tracks`` rows that
``binning_tool`` writes for an entry, one row for each track, in output
order. The function reads the entry alone. When a sibling entry of the run
makes a track with the same name, group and aggregator, ``binning_tool``
refuses the run, and ``fragment_tracks`` still gives the rows.

The example below builds the entry of :doc:`entries` and previews it:

.. code-block:: python

    import gain.grr as grr
    from gain.binning import (
        CellGrouping,
        FragmentEntry,
        MetaFilter,
        MetaSource,
        cell_groups,
        fragment_tracks,
    )

    repo = grr.build_genomic_resource_repository()
    entry = FragmentEntry(
        "frags/*",
        group=CellGrouping(),
        meta=MetaSource(
            resource_id="meta/cells",
            filters=(MetaFilter("sample_id", label="sample_id"),),
        ),
    )

    groups = cell_groups(repo, entry)
    print(groups)  # columns: resource_id, score_value, group

    tracks = fragment_tracks(repo, entry)
    print(tracks[["resource_ids", "selector"]])

Each function also takes the mapping under the ``fragment_score_binner``
key of a run definition in place of a ``FragmentEntry``. The user page
:doc:`/binning_infrastructure` describes the keys of that mapping. A
relative local ``meta`` file is read from ``base_dir``, or from the
current directory without it.

Previewing a whole run definition
---------------------------------

:func:`~gain.binning.resolve_binners` resolves the whole ``binners`` list of
a run definition into its jobs, as ``binning_tool`` does before a run. A
notebook calls it to check a run definition with position-score entries
too, or to see the labels of the tracks of all its entries together. The list holds one mapping of a binner kind to its configuration
for each entry, as the YAML of the run definition does.

:func:`~gain.binning.tracks_frame` gives the ``/tracks`` rows of the jobs
that ``resolve_binners`` returns, one row for each track, in the order of
the file. The rows hold no name for a track.
:func:`~gain.binning.track_labels` builds the label of each row, as
:ref:`binning-track-labels` describes:

.. code-block:: python

    import gain.grr as grr
    from gain.binning import resolve_binners, track_labels, tracks_frame

    repo = grr.build_genomic_resource_repository()
    binners = [
        {"position_score_binner": {"resource_query": "hg38/scores/phyloP*"}},
        {"fragment_score_binner": {"resource_query": "frags/*"}},
    ]

    jobs = resolve_binners(binners, repo)
    tracks = tracks_frame(jobs)
    print(tracks[["binner_type", "resource_ids", "selector"]])
    print(track_labels(tracks))

API
---

The names below are exported by the package ``gain.binning``.

.. currentmodule:: gain.binning

.. autosummary::

   cell_groups
   fragment_tracks
   resolve_binners
   tracks_frame

.. autofunction:: cell_groups

.. autofunction:: fragment_tracks

.. autofunction:: resolve_binners

.. autofunction:: tracks_frame
