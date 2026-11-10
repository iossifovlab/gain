"""The groups and the tracks of a ``fragment_score_binner`` entry.

A notebook user reads what ``binning_tool`` would bin for one entry,
without a run: :func:`cell_groups` gives the group of each cell, and
:func:`fragment_tracks` gives the ``/tracks`` rows of the entry.  Each
function resolves the entry with the code that ``binning_tool`` runs,
so the defaults and the refusals are the same.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from gain.binning.binners import BinningJob, tracks_frame
from gain.binning.fragment_binner import (
    FragmentBinningJob,
    FragmentScoreBinner,
    job_track_maps,
)
from gain.binning.fragment_grouping import FragmentEntry
from gain.binning.run_definition import name_job_tracks
from gain.genomic_resources.repository import GenomicResourceRepo

__all__ = ["CELL_GROUPS_COLUMNS", "cell_groups", "fragment_tracks"]

#: The columns of :func:`cell_groups`.
CELL_GROUPS_COLUMNS = ("resource_id", "score_value", "group")


def _entry_jobs(
    label: str, entry: FragmentEntry | dict[str, Any],
    repo: GenomicResourceRepo, base_dir: str | None,
) -> list[BinningJob]:
    """Resolve ``entry``, read from a dict when it is one, into its jobs."""
    if not isinstance(entry, FragmentEntry):
        entry = FragmentScoreBinner.entry_from_config(label, entry)
    return FragmentScoreBinner.entry_jobs(
        label, entry, repo, base_dir=base_dir)


def _job_groups(
    job: BinningJob, repo: GenomicResourceRepo,
) -> list[tuple[str, str, str]]:
    """Give the (resource id, key, group) rows of one job, key-sorted."""
    assert isinstance(job, FragmentBinningJob)
    track_maps = job_track_maps(job, repo)
    if track_maps is None:
        return []
    return [
        (resource_id, key, job.tracks[index].group)
        for resource_id, track_map in zip(
            job.resource_ids, track_maps, strict=True)
        for key, index in sorted(track_map.items())
    ]


def cell_groups(
    repo: GenomicResourceRepo, entry: FragmentEntry | dict[str, Any],
    *, base_dir: str | None = None,
) -> pd.DataFrame:
    """Give the group that ``binning_tool`` puts each key of ``entry`` in.

    The key of a cell grouping is the barcode of a cell.  The key of a
    value grouping is a value of its score.  A fragment with a key that
    no row lists reaches no track.  Each refusal names the entry as
    ``binners[0]``.

    Parameters
    ----------
    repo
        The repository that the query of the entry searches.
    entry
        The entry, or the mapping under the ``fragment_score_binner``
        key of an entry in a run definition.
    base_dir
        The directory that a relative local ``meta`` file is read from.
        ``None`` is the current directory.

    Returns
    -------
    pandas.DataFrame
        The columns of :data:`CELL_GROUPS_COLUMNS`: one row for each key
        of each resource, in the order of the resources and then of the
        keys.  An entry with a constant group gives no row.

    Raises
    ------
    RunDefinitionError
        When ``binning_tool`` refuses the entry.
    """
    jobs = _entry_jobs("binners[0]", entry, repo, base_dir)
    return pd.DataFrame(
        [row for job in jobs for row in _job_groups(job, repo)],
        columns=list(CELL_GROUPS_COLUMNS))


def fragment_tracks(
    repo: GenomicResourceRepo, entry: FragmentEntry | dict[str, Any],
    *, binner_index: int = 0, base_dir: str | None = None,
) -> pd.DataFrame:
    """Give the ``/tracks`` rows that ``binning_tool`` writes for ``entry``.

    The function reads ``entry`` alone.  A run names the tracks of all
    its entries together, but the ``/tracks`` rows hold no name, so a
    sibling entry does not change these rows.  The function does not
    see the other entries of a run: when a sibling entry produces a
    track with the same name, group and aggregator, ``binning_tool``
    refuses the run, and this function still gives the rows.

    Parameters
    ----------
    repo
        The repository that the query of the entry searches.
    entry
        The entry, or the mapping under the ``fragment_score_binner``
        key of an entry in a run definition.
    binner_index
        The position of the entry in the ``binners`` list.  It is the
        ``binner_index`` of each row, and each refusal names the entry
        as ``binners[<binner_index>]``.
    base_dir
        The directory that a relative local ``meta`` file is read from.
        ``None`` is the current directory.

    Returns
    -------
    pandas.DataFrame
        The columns of the ``/tracks`` table of the file, one row for
        each track of the entry, in output order.

    Raises
    ------
    RunDefinitionError
        When ``binning_tool`` refuses the entry.
    """
    label = f"binners[{binner_index}]"
    jobs = _entry_jobs(label, entry, repo, base_dir)
    return tracks_frame(name_job_tracks([(label, job) for job in jobs]))
