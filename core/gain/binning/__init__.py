"""Bin position scores into a fixed genome grid (``binning_tool``).

A notebook user builds a ``fragment_score_tracks`` entry from the spec
classes, :class:`FragmentEntry` and its blocks.  :func:`cell_groups`
and :func:`fragment_tracks` give the groups and the tracks of an entry
without a run, and the ``read_*`` functions read the file of a run.
``binning_tool`` reads the same objects from a run definition.  A
refusal of the run definition or of a spec class is a
:class:`RunDefinitionError`.

Ledger
------
The ledger notes each name that changed on the public surface of the
package.

* gain#2117: ``fragment_grouping.CellGrouping`` and
  ``fragment_grouping.ValueGrouping`` are now ``BoundCellGrouping`` and
  ``BoundValueGrouping``. The ``fragment_binner`` module re-exports the new
  names. The rename frees the old names for the public spec classes of
  gain#2118.
* gain#2118: the package exports the spec classes ``FragmentEntry``,
  ``ConstantGroup``, ``CellGrouping``, ``ValueGrouping``, ``MetaSource``,
  ``MetaFilter``, ``ScoreContribution``, ``ConstantContribution`` and
  ``Aggregate``, and the error ``RunDefinitionError``.
  ``CellGrouping`` and ``ValueGrouping`` now name the spec classes of a
  grouping.  The resolved groupings are ``BoundCellGrouping`` and
  ``BoundValueGrouping``.  ``FragmentScoreBinner`` gains
  ``entry_from_config`` and ``entry_jobs``, and ``parse_entry`` calls
  the two.
* gain#2119: the package exports ``cell_groups`` and ``fragment_tracks``
  of the new ``fragment_api`` module, and ``read_tracks``,
  ``read_bins``, ``read_values`` and ``track_labels`` of ``h5_reader``.
* gain#2494: ``resolve_binners`` is now ``resolve_tracks``, see ADR 0044.
* gain#2495: the track kinds ``position_score_binner`` and
  ``fragment_score_binner`` are now ``position_score_tracks`` and
  ``fragment_score_tracks``: the ``kind`` of ``PositionScoreTracks`` and
  ``FragmentScoreTracks`` and the names in the ``gain.binning.track_kinds``
  group.  ``TRACK_KIND_SUFFIX`` is ``"_tracks"``.  The ``binners`` key of
  a run definition is now ``tracks``, and an entry label is
  ``tracks[<i>]``.
* gain#2497: the keyword ``binner_index`` of ``fragment_tracks`` is now
  ``entry_index``.  The ``/tracks`` columns ``binner_type``,
  ``binner_index`` and ``binner_def`` are now ``entry_type``,
  ``entry_index`` and ``entry_def``, and so are the fields of ``Track``
  and the columns of ``tracks_frame``.  ``binning_tool`` writes
  ``tracks_layout`` 3, and the readers refuse ``tracks_layout`` 2.
"""
from gain.binning.fragment_api import cell_groups, fragment_tracks
from gain.binning.fragment_grouping import (
    Aggregate,
    CellGrouping,
    ConstantContribution,
    ConstantGroup,
    FragmentEntry,
    MetaFilter,
    MetaSource,
    ScoreContribution,
    ValueGrouping,
)
from gain.binning.h5_reader import (
    read_bins,
    read_tracks,
    read_values,
    track_labels,
)
from gain.binning.run_definition import resolve_tracks
from gain.binning.tracks import RunDefinitionError, tracks_frame

__all__ = [
    "Aggregate",
    "CellGrouping",
    "ConstantContribution",
    "ConstantGroup",
    "FragmentEntry",
    "MetaFilter",
    "MetaSource",
    "RunDefinitionError",
    "ScoreContribution",
    "ValueGrouping",
    "cell_groups",
    "fragment_tracks",
    "read_bins",
    "read_tracks",
    "read_values",
    "resolve_tracks",
    "track_labels",
    "tracks_frame",
]
