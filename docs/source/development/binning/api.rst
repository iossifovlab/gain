The binning API
===============

The package ``gain.binning`` gives three things to a script or a
notebook:

- the spec classes of a ``fragment_score_binner`` entry, with
  :class:`~gain.binning.FragmentEntry` at the top;
- :func:`~gain.binning.cell_groups` and
  :func:`~gain.binning.fragment_tracks`, which give the groups and the
  tracks of an entry without a run;
- the ``read_*`` functions and :func:`~gain.binning.track_labels`, which
  read the HDF5 file of a run back.

A refusal of a run definition, of an entry or of a spec class is a
:class:`~gain.binning.RunDefinitionError`. ``binning_tool`` reads the same
spec classes from the YAML of a run definition.

An entry without a run
----------------------

The example below builds a pooled entry over the fragment resources
``frags/*``. Each cell is grouped by the ``class`` column of the cell
metadata table ``meta/cells``, and the filter selects the rows of the sample
of each resource. ``cell_groups`` gives the group of each barcode, and
``fragment_tracks`` gives the ``/tracks`` rows that ``binning_tool`` writes
for the entry:

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
:doc:`/binning_infrastructure` describes the keys of that mapping.

API
---

The names below are exported by the package ``gain.binning``. Import them
from there, for example ``from gain.binning import FragmentEntry``. The
modules of the package that define them are not the import path.

.. currentmodule:: gain.binning

.. autosummary::

   FragmentEntry
   ScoreContribution
   ConstantContribution
   Aggregate
   Aggregate.effective_uncovered_value
   ConstantGroup
   CellGrouping
   ValueGrouping
   MetaSource
   MetaSource.effective_file_format
   MetaFilter
   MetaFilter.operand
   RunDefinitionError
   cell_groups
   fragment_tracks
   resolve_binners
   tracks_frame
   read_tracks
   read_bins
   read_values
   track_labels

The spec classes
^^^^^^^^^^^^^^^^

.. autoclass:: FragmentEntry
   :members:

.. autoclass:: ScoreContribution
   :members:

.. autoclass:: ConstantContribution
   :members:

.. autoclass:: Aggregate
   :members:

.. autoclass:: ConstantGroup
   :members:

.. autoclass:: CellGrouping
   :members:

.. autoclass:: ValueGrouping
   :members:

.. autoclass:: MetaSource
   :members:

.. autoclass:: MetaFilter
   :members:

.. autoclass:: RunDefinitionError
   :members:

The groups and the tracks of an entry
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. autofunction:: cell_groups

.. autofunction:: fragment_tracks

.. autofunction:: resolve_binners

.. autofunction:: tracks_frame

Reading the file of a run
^^^^^^^^^^^^^^^^^^^^^^^^^

.. autofunction:: read_tracks

.. autofunction:: read_bins

.. autofunction:: read_values

.. autofunction:: track_labels
