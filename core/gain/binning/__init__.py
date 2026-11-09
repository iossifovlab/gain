"""Bin position scores into a fixed genome grid (``binning_tool``).

Ledger
------
The ledger notes each name that changed on the public surface of the
package.

* gain#2117: ``fragment_grouping.CellGrouping`` and
  ``fragment_grouping.ValueGrouping`` are now ``BoundCellGrouping`` and
  ``BoundValueGrouping``. The ``fragment_binner`` module re-exports the new
  names. The rename frees the old names for the public spec classes of
  gain#2118.
"""
from gain.binning.binners import tracks_frame
from gain.binning.run_definition import resolve_binners

__all__ = ["resolve_binners", "tracks_frame"]
