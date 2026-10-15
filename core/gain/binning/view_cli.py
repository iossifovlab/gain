"""``binning_tool_view``: print the tables of a ``binning_tool`` file.

The tables are ``/tracks``, ``/bins`` and ``/values``, printed as
tab-separated text on stdout.  ``/values`` is read one block of rows at a
time, so the memory for the values stays flat for a file of any size.
The memory for ``/bins`` and for the selected rows grows with the number
of bins.
"""
from __future__ import annotations

import argparse
import io
import os
import sys
from collections.abc import Iterator, Sequence
from typing import NamedTuple, TextIO

import h5py
import numpy as np
import numpy.typing as npt
import pandas as pd

from gain.binning.h5_reader import read_bins, read_tracks, track_labels

# Rows of ``/values`` read and printed at a time.
ROW_BLOCK = 8192
_AXES_DIFFER = "--tracks cannot go with --bins or --matrix: the axes differ"


class ViewError(Exception):
    """A user error: the message is the whole report."""


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="binning_tool_view",
        description="Print the /tracks, /bins and /values tables of a "
        "binning_tool HDF5 file as tab-separated text.")
    parser.add_argument("file", help="the HDF5 file written by binning_tool")
    parser.add_argument(
        "--tracks", action="store_true",
        help="print the /tracks table, one row per track")
    parser.add_argument(
        "--bins", action="store_true",
        help="print the /bins table: chrom, start and end of each bin")
    parser.add_argument(
        "--matrix", action="store_true",
        help="print the /values matrix, one column per track; with "
        "--bins, one joined table")
    parser.add_argument(
        "--region", action="append", default=[], metavar="REGION",
        help="print only the bins that overlap REGION: chrom, chrom:start "
        "or chrom:start-end, 1-based inclusive. Repeatable")
    parser.add_argument(
        "--track", action="append", default=[], metavar="LABEL",
        help="print only the track with this exact label. Repeatable")
    parser.add_argument(
        "--header", choices=["label", "index"], default="label",
        help="head the matrix columns by track label (the default) or "
        "by track position")
    parser.add_argument(
        "--no-header", action="store_true",
        help="do not print the header line")
    return parser


def _check_options(args: argparse.Namespace) -> None:
    if not (args.tracks or args.bins or args.matrix):
        raise ViewError("choose at least one of --tracks, --bins, --matrix")
    if args.tracks and (args.bins or args.matrix):
        raise ViewError(_AXES_DIFFER)
    if args.region and args.tracks:
        raise ViewError("--region needs --bins or --matrix")
    if args.track and not (args.tracks or args.matrix):
        raise ViewError("--track needs --matrix or --tracks")


def _clean(text: str) -> str:
    return text.translate({9: " ", 10: " ", 13: " "})


def _cell(value: object) -> str:
    if isinstance(value, str):
        return _clean(value)
    if isinstance(value, (float, np.floating)):
        return repr(float(value))
    return str(int(value)) if isinstance(value, (int, np.integer)) \
        else _clean(str(value))


def _select_tracks(
    labels: list[str], wanted: list[str],
) -> list[int]:
    """Positions of the wanted labels, in file order."""
    if not wanted:
        return list(range(len(labels)))
    chosen: set[int] = set()
    for label in wanted:
        found = [i for i, name in enumerate(labels) if name == label]
        if not found:
            raise ViewError(f"--track '{label}': no track has this label")
        if len(found) > 1:
            raise ViewError(
                f"--track '{label}': {len(found)} tracks have this label")
        chosen.update(found)
    return sorted(chosen)


def _write(out: TextIO, cells: Sequence[str]) -> None:
    out.write("\t".join(cells) + "\n")


def _print_tracks(
    out: TextIO, tracks: pd.DataFrame, labels: list[str],
    positions: list[int], *, header: bool,
) -> None:
    if header:
        _write(out, ["label", *map(str, tracks.columns)])
    for position in positions:
        row = tracks.iloc[position]
        _write(out, [_clean(labels[position]), *map(_cell, row)])


def _coordinate(text: str, region: str, spelled: str) -> int:
    try:
        return int(text.replace(",", ""))
    except ValueError:
        raise ViewError(
            f"--region '{region}': cannot read the coordinates "
            f"'{spelled}'") from None


def _parse_region(
    region: str, chroms: set[str],
) -> tuple[str, int, int | None]:
    """Read ``chrom``, ``chrom:start`` or ``chrom:start-end``.

    The coordinates are 1-based and inclusive.  ``chrom:start`` runs to
    the end of the chromosome, as in samtools; ``None`` is that end.
    """
    chrom, start, end = region, 1, None
    if region not in chroms and ":" in region:
        chrom, spelled = region.rsplit(":", 1)
        first, dash, last = spelled.partition("-")
        start = _coordinate(first, region, spelled)
        if dash:
            end = _coordinate(last, region, spelled)
    if chrom not in chroms:
        raise ViewError(
            f"--region '{region}': chromosome '{chrom}' is not in /bins")
    if start < 1:
        raise ViewError(f"--region '{region}': the start must be 1 or more")
    if end is not None and start > end:
        raise ViewError(
            f"--region '{region}': the start {start} is above the end {end}")
    return chrom, start, end


def _bin_rows(
    bins: pd.DataFrame, regions: list[str],
) -> npt.NDArray[np.int64]:
    """Rows of the bins that overlap any region, unclipped, in file order."""
    if not regions:
        return np.arange(len(bins), dtype=np.int64)
    chroms = bins["chrom"].to_numpy()
    starts = bins["start"].to_numpy()
    ends = bins["end"].to_numpy()
    selected = np.zeros(len(bins), dtype=bool)
    known = set(np.unique(chroms).tolist())
    for region in regions:
        chrom, start, end = _parse_region(region, known)
        hit = (chroms == chrom) & (ends >= start)
        if end is not None:
            hit &= starts <= end
        selected |= hit
    return np.flatnonzero(selected).astype(np.int64)


def _runs(rows: npt.NDArray[np.int64]) -> Iterator[tuple[int, int]]:
    """Half-open runs of consecutive rows, at most ROW_BLOCK long."""
    if len(rows) == 0:
        return
    breaks = np.flatnonzero(np.diff(rows) != 1) + 1
    for run in np.split(rows, breaks):
        for start in range(0, len(run), ROW_BLOCK):
            piece = run[start:start + ROW_BLOCK]
            yield int(piece[0]), int(piece[-1]) + 1


class _Table(NamedTuple):
    """What to print: the bins, the matrix columns, and the rows of both."""

    bins: pd.DataFrame | None
    labels: list[str]
    positions: list[int]
    rows: npt.NDArray[np.int64]
    matrix: bool
    header: bool
    header_kind: str


def _header_cells(table: _Table) -> list[str]:
    cells: list[str] = []
    if table.bins is not None:
        cells.extend(map(str, table.bins.columns))
    if table.matrix:
        cells.extend(
            _clean(table.labels[i]) if table.header_kind == "label"
            else str(i) for i in table.positions)
    return cells


def _open_values(
    h5: h5py.File, track_count: int, bin_count: int,
) -> h5py.Dataset:
    """The ``/values`` dataset, checked against ``/tracks`` and ``/bins``."""
    if "values" not in h5:
        raise ViewError("/values is missing")
    values = h5["values"]
    if not isinstance(values, h5py.Dataset):
        raise ViewError("/values is not a dataset")
    if values.ndim != 2:
        raise ViewError("/values is not a 2-D matrix")
    rows, columns = values.shape
    if columns < track_count:
        raise ViewError(
            f"/values has {columns} columns but /tracks has "
            f"{track_count} tracks")
    if rows != bin_count:
        raise ViewError(
            f"/values has {rows} rows but /bins has {bin_count} bins")
    return values


def _print_table(
    out: TextIO, values: h5py.Dataset | None, table: _Table,
) -> None:
    if table.header:
        _write(out, _header_cells(table))
    for first, stop in _runs(table.rows):
        parts: list[list[list[str]]] = []
        if table.bins is not None:
            parts.append([
                [_cell(v) for v in row] for row in
                table.bins.iloc[first:stop].itertuples(index=False)])
        if values is not None:
            block = values[first:stop, list(table.positions)]
            parts.append([[repr(v) for v in row] for row in block.tolist()])
        for pieces in zip(*parts, strict=True):
            _write(out, [cell for piece in pieces for cell in piece])


def _run(args: argparse.Namespace, out: TextIO) -> None:
    header = not args.no_header
    with h5py.File(args.file, "r") as h5:
        tracks = read_tracks(h5)
        labels = track_labels(tracks)
        positions = _select_tracks(labels, args.track)
        if args.tracks:
            _print_tracks(out, tracks, labels, positions, header=header)
            return
        bins = read_bins(h5)
        rows = _bin_rows(bins, args.region)
        values = _open_values(h5, len(labels), len(bins)) \
            if args.matrix else None
        _print_table(out, values, _Table(
            bins if args.bins else None, labels, positions, rows,
            args.matrix, header, args.header))


def _silence_stdout() -> None:
    """Point the stdout descriptor at /dev/null, if stdout has one."""
    try:
        target = sys.stdout.fileno()
    except (io.UnsupportedOperation, ValueError):
        return
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, target)
    finally:
        os.close(devnull)


def cli(argv: Sequence[str] | None = None) -> int:
    """Run ``binning_tool_view``; return the exit code."""
    args = _build_argument_parser().parse_args(argv)
    try:
        _check_options(args)
        _run(args, sys.stdout)
        sys.stdout.flush()
    except BrokenPipeError:
        # The reader closed the pipe (``| head``).  Point stdout at
        # /dev/null, or Python reports the failed flush again at exit.
        _silence_stdout()
        return 0
    except (ViewError, ValueError, OSError) as error:
        sys.stderr.write(f"binning_tool_view: error: {error}\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(cli())
