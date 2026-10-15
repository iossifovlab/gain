"""Read a ``binning_tool`` HDF5 file as DataFrames and a numpy matrix."""
from __future__ import annotations

import json
import os
from collections import Counter
from typing import Any

import h5py
import numpy as np
import numpy.typing as npt
import pandas as pd

_TRACKS_LAYOUT = 2

type Source = str | os.PathLike[str] | h5py.File


def _read_table(source: Source, name: str) -> pd.DataFrame:
    if isinstance(source, h5py.File):
        return _table_of(source, name)
    with h5py.File(source, "r") as h5:
        return _table_of(h5, name)


def _where(h5: h5py.File, name: str) -> str:
    return f"{h5.filename}: dataset '{name}'"


def _dataset_of(h5: h5py.File, name: str) -> h5py.Dataset:
    if name not in h5:
        raise ValueError(f"{_where(h5, name)}: no such dataset")
    dataset = h5[name]
    if not isinstance(dataset, h5py.Dataset):
        # Every unreadable dataset raises ValueError,
        # so callers catch one exception type.
        raise ValueError(  # ruff: ignore[type-check-without-type-error]
            f"{_where(h5, name)}: not a dataset")
    return dataset


def _table_of(h5: h5py.File, name: str) -> pd.DataFrame:
    dataset = _dataset_of(h5, name)
    names = dataset.dtype.names
    if names is None:
        raise ValueError(f"{_where(h5, name)}: not compound")
    array = np.asarray(dataset)
    columns: dict[str, Any] = {}
    for field in names:
        columns[field] = array[field]
        if array.dtype[field].kind in "OSU":
            columns[field] = [
                item.decode("utf-8") if isinstance(item, bytes) else item
                for item in array[field]]
    return pd.DataFrame(columns)


def _check_tracks_layout(h5: h5py.File) -> None:
    layout = h5.attrs.get("tracks_layout")
    if layout == _TRACKS_LAYOUT:
        return
    if layout is not None:
        raise ValueError(
            f"{h5.filename}: tracks_layout {layout}, "
            f"only tracks_layout {_TRACKS_LAYOUT} is read")
    fields: tuple[str, ...] = ()
    tracks = h5.get("tracks")
    if isinstance(tracks, h5py.Dataset) and tracks.dtype.names:
        fields = tracks.dtype.names
    if "resource_id" in fields:
        found = "the 2026.9 layout"
    elif "resource_ids" in fields:
        found = "the layout after 2026.9"
    else:
        found = "an older layout"
    raise ValueError(
        f"{h5.filename}: no tracks_layout attribute, this is {found}; "
        f"only tracks_layout {_TRACKS_LAYOUT} is read")


def read_tracks(source: Source) -> pd.DataFrame:
    """Read ``/tracks`` of a ``tracks_layout = 2`` file."""
    tracks = _read_table(source, "tracks")
    if isinstance(source, h5py.File):
        _check_tracks_layout(source)
    else:
        with h5py.File(source, "r") as h5:
            _check_tracks_layout(h5)
    return tracks


def read_bins(source: Source) -> pd.DataFrame:
    """Read ``/bins`` of a ``binning_tool`` file."""
    return _read_table(source, "bins")


def _values_of(h5: h5py.File) -> npt.NDArray[np.float64]:
    dataset = _dataset_of(h5, "values")
    if dataset.ndim != 2:
        raise ValueError(f"{_where(h5, 'values')}: not a 2-D matrix")
    return np.asarray(dataset, dtype=np.float64)


def read_values(source: Source) -> npt.NDArray[np.float64]:
    """Read ``/values`` of a ``binning_tool`` file as a numpy matrix.

    The matrix has the shape ``(n_bins, n_tracks)``: row *i* belongs to
    row *i* of ``/bins`` and column *j* to row *j* of ``/tracks``. A bin
    with no data is ``NaN``. The whole matrix is read into memory. For a
    large file, slice ``h5["values"][start:stop]`` with ``h5py`` instead.

    Parameters
    ----------
    source
        A path or an open ``h5py.File``. A path is opened and closed
        inside the call; an open file is only read, never closed.

    Returns
    -------
    numpy.ndarray
        A ``float64`` array of two dimensions.

    Raises
    ------
    ValueError
        If ``/values`` is missing, is not a dataset or is not
        two-dimensional. The message names the file and the dataset.
    """
    if isinstance(source, h5py.File):
        return _values_of(source)
    with h5py.File(source, "r") as h5:
        return _values_of(h5)


def _label_base(
    resource_ids: str, selector: str, binner_type: str,
    definition: dict[str, Any],
) -> str:
    name = definition.get("name")
    prefixed = f"{name}/{resource_ids}" if name else resource_ids
    if binner_type == "position_score":
        return prefixed
    if binner_type == "fragment_score":
        if definition.get("pool"):
            base = name or definition.get("resource_query") or resource_ids
        else:
            base = prefixed
        return f"{base}:{selector}"
    if selector:
        return f"{prefixed}:{selector}"
    return prefixed


def track_labels(tracks_df: pd.DataFrame) -> list[str]:
    """Return one display label for each track, in row order.

    The label is read from ``resource_ids``, ``selector`` and the JSON
    ``binner_def`` alone. Equal labels get ``#<position>`` appended.
    """
    labels: list[str] = []
    for binner_type, resource_ids, selector, binner_def in zip(
            tracks_df["binner_type"].astype(str),
            tracks_df["resource_ids"].astype(str),
            tracks_df["selector"].astype(str),
            tracks_df["binner_def"].astype(str), strict=True):
        definition = json.loads(binner_def) or {}
        label = _label_base(resource_ids, selector, binner_type, definition)
        aggregator = definition.get("aggregator")
        if aggregator:
            label = f"{label}:{aggregator}"
        labels.append(label)
    counts = Counter(labels)
    return [
        f"{label}#{position}" if counts[label] > 1 else label
        for position, label in enumerate(labels)]
