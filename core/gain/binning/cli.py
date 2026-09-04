"""``bin_scores``: bin position scores into a fixed genome grid.

One task per (track, region) writes its column chunk as a ``.npy``
vector in the work directory; one serial writer task assembles the HDF5
file region by region.  HDF5 has a single writer, so no task other than
the writer touches the file, and a rerun with the same work directory
reuses the finished chunks and reruns only the writer.
"""
from __future__ import annotations

import argparse
import datetime
import functools
import json
import os
import sys
from contextlib import chdir
from typing import Any

import h5py
import numpy as np
import numpy.typing as npt
import yaml

from gain import __version__
from gain.annotation.annotate_utils import (
    build_cli_genomic_context,
    get_grr_from_context,
    maybe_remove_work_dir,
)
from gain.binning.binners import Binner, Track, discover_binner_kinds
from gain.binning.run_definition import (
    RunDefinition,
    RunDefinitionError,
    parse_run_definition,
)
from gain.genomic_resources.genomic_context import (
    context_providers_add_argparser_arguments,
)
from gain.genomic_resources.genomic_context_base import GenomicContext
from gain.genomic_resources.reference_genome import (
    ReferenceGenome,
    build_reference_genome_from_resource_id,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)
from gain.task_graph.cli_tools import TaskGraphCli
from gain.task_graph.graph import TaskGraph
from gain.utils.regions import BedRegion, calc_bin_index
from gain.utils.verbosity_configuration import VerbosityConfiguration

COORDINATES = "1-based-inclusive"
# Rows per HDF5 chunk of ``/values``: "every track for one chromosome" is
# then a contiguous read, and gzip collapses the NaN- and zero-heavy runs.
ROW_BLOCK = 8192
# Paths the user may have named relative to where the command was typed;
# the tasks run inside the work directory, so they are resolved first.
PATH_ARGS = (
    "run_definition", "output", "work_dir", "task_status_dir",
    "task_log_dir", "dask_cluster_config_file", "grr_filename",
    "grr_directory")


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bin position scores into a fixed genome grid, "
        "writing one HDF5 file with a bins x tracks matrix.")
    parser.add_argument(
        "run_definition",
        help="the run definition (YAML): bins and binner entries")
    parser.add_argument(
        "-o", "--output", required=True,
        help="the HDF5 file to write (conventionally .h5)")
    parser.add_argument(
        "-w", "--work-dir", default=None,
        help="directory for the per-chunk intermediate files; defaults "
        "to a sibling of the output")
    parser.add_argument(
        "--keep-work-dir", action="store_true", default=False,
        help="keep the working directory after a successful run (by "
        "default a working directory the tool created is removed)")
    parser.add_argument(
        "--dry-run", action="store_true", default=False,
        help="resolve every query, print the track list and the region "
        "and bin counts, and write nothing")
    context_providers_add_argparser_arguments(parser)
    TaskGraphCli.add_arguments(
        parser, default_task_status_dir=None, use_commands=False)
    VerbosityConfiguration.set_arguments(parser)
    return parser


def cli(argv: list[str] | None = None) -> None:
    """Entry point of the ``bin_scores`` tool."""
    if argv is None:
        argv = sys.argv[1:]
    args = vars(_build_argument_parser().parse_args(argv))
    VerbosityConfiguration.set(args)
    for key in PATH_ARGS:
        if args.get(key):
            args[key] = os.path.abspath(args[key])

    with open(args["run_definition"]) as infile:
        config = yaml.safe_load(infile)

    context = build_cli_genomic_context(args)
    grr = get_grr_from_context(context)
    genome = _resolve_genome(config, args, context, grr)
    try:
        with genome:
            run = parse_run_definition(config, grr, genome)
    except RunDefinitionError as err:
        print(f"{args['run_definition']}: {err}", file=sys.stderr)
        sys.exit(1)

    if args["dry_run"]:
        _print_plan(run)
        return

    _handle_default_args(args)
    # Inside the work dir, as the annotate tools run: an index a worker
    # fetches for a remote resource then lands there, not in the launch
    # directory.
    with chdir(args["work_dir"]):
        task_graph = _build_task_graph(run, args, grr)
        result = TaskGraphCli.process_graph(task_graph, **args)
    maybe_remove_work_dir(args, result=result)


def _resolve_genome(
    config: dict[str, Any], args: dict[str, Any],
    context: GenomicContext, grr: GenomicResourceRepo,
) -> ReferenceGenome:
    """The run definition names it, ``-R`` overrides it, context is last.

    ``-R`` is already folded into the context by the CLI context provider,
    so an explicit flag simply wins; without one, the run definition's
    ``input_reference_genome`` is looked up in the GRR the context
    resolved, and only then does the context's own genome stand in.
    """
    named = config.get("input_reference_genome")
    if args.get("reference_genome_resource_id") is None and named:
        return build_reference_genome_from_resource_id(named, grr)
    genome = context.get_reference_genome()
    if genome is None:
        raise ValueError(
            "no reference genome: name input_reference_genome in the run "
            "definition, pass -R, or configure one in the genomic context")
    return genome


def _print_plan(run: RunDefinition) -> None:
    print("tracks:")
    for track in run.tracks:
        print(
            f"  {track.name}\t{track.resource_id}\t{track.score_id}\t"
            f"{track.aggregator}")
    print(f"regions: {len(run.regions)}")
    print(f"bins: {sum(_bin_count(r, run.bin_size) for r in run.regions)}")


def _bin_count(region: BedRegion, bin_size: int) -> int:
    return calc_bin_index(bin_size, region.stop) \
        - calc_bin_index(bin_size, region.start) + 1


def _handle_default_args(args: dict[str, Any]) -> None:
    """Fill the work and task-status directories the annotate tools' way."""
    if args.get("work_dir") is None:
        args["work_dir"] = f"{os.path.splitext(args['output'])[0]}_work"
    args["work_dir_created"] = not os.path.exists(args["work_dir"])
    os.makedirs(args["work_dir"], exist_ok=True)
    if args.get("task_status_dir") is None:
        args["task_status_dir"] = os.path.join(
            args["work_dir"], ".task-status")
    if args.get("task_log_dir") is None:
        args["task_log_dir"] = os.path.join(args["work_dir"], ".task-log")


def _build_task_graph(
    run: RunDefinition, args: dict[str, Any], grr: GenomicResourceRepo,
) -> TaskGraph:
    """One task per (track, region) chunk, then one serial writer."""
    assert grr.definition is not None
    kinds = discover_binner_kinds()
    graph = TaskGraph()
    graph.input_files.append(args["run_definition"])
    chunk_dir = os.path.join(args["work_dir"], "chunks")
    os.makedirs(chunk_dir, exist_ok=True)

    chunk_tasks = []
    chunk_paths: list[list[str]] = []
    for region in run.regions:
        region_paths = []
        for track in run.tracks:
            stem = _chunk_stem(track, region, run.bin_size)
            path = os.path.join(chunk_dir, f"{stem}.npy")
            chunk_tasks.append(graph.create_task(
                f"bin_{stem}", _bin_chunk,
                args=[kinds[track.binner], track, region, run.bin_size,
                      grr.definition, path],
                output_files=[path],
            ))
            region_paths.append(path)
        chunk_paths.append(region_paths)

    # The chunk directory is the writer's one input: its mtime moves
    # whenever a chunk is created, so another run definition sharing the
    # work dir makes the writer run again instead of leaving a stale file
    # behind -- at the cost of one stat, where naming every chunk would
    # have the executor walk the graph once per chunk.
    graph.create_task(
        "write_hdf5", _write_hdf5,
        args=[args["output"], run, chunk_paths],
        deps=chunk_tasks,
        input_files=[chunk_dir],
        output_files=[args["output"]],
    )
    return graph


def _chunk_stem(track: Track, region: BedRegion, bin_size: int) -> str:
    """Name a chunk by everything that decides its values.

    Two run definitions sharing a work directory then share exactly the
    chunks they compute identically, and nothing else: a different bin
    size, aggregator or replacement is a different chunk, not a stale one.
    """
    resource = track.resource_id.replace("/", "_")
    replacement = track.none_value_replacement
    return (
        f"{resource}_{track.score_id}_{track.aggregator}"
        f"_{'none' if replacement is None else replacement!r}"
        f"_bs{bin_size}_{region.chrom}_{region.start}_{region.stop}")


@functools.lru_cache(maxsize=4)
def _repository(definition: str) -> GenomicResourceRepo:
    """The GRR a worker binds to, built once per process, not per task."""
    return build_genomic_resource_repository(json.loads(definition))


def _bin_chunk(
    binner: type[Binner], track: Track, region: BedRegion, bin_size: int,
    grr_definition: dict[str, Any], path: str,
) -> None:
    grr = _repository(json.dumps(grr_definition, sort_keys=True))
    np.save(path, binner.bin_track(track, region, bin_size, grr))


def _write_hdf5(
    output: str, run: RunDefinition, chunk_paths: list[list[str]],
) -> None:
    """Assemble the file: ``/values`` region by region, then the tables."""
    counts = [_bin_count(region, run.bin_size) for region in run.regions]
    n_bins = sum(counts)
    n_tracks = len(run.tracks)
    row_block = min(ROW_BLOCK, n_bins)
    # A chunk cache of a few row blocks, so that the region slabs, which
    # start and end mid-block, are recompressed once each, not per slab.
    with h5py.File(
            output, "w",
            rdcc_nbytes=4 * row_block * n_tracks * 8) as h5:
        values = h5.create_dataset(
            "values", shape=(n_bins, n_tracks), dtype=np.float64,
            chunks=(row_block, n_tracks),
            compression="gzip", fillvalue=np.nan)
        row = 0
        for count, paths in zip(counts, chunk_paths, strict=True):
            values[row:row + count, :] = np.column_stack(
                [np.load(path) for path in paths])
            row += count
        h5.create_dataset("bins", data=_bins_table(run, counts))
        h5.create_dataset("tracks", data=_tracks_table(run.tracks))
        h5.attrs["input_reference_genome"] = run.input_reference_genome
        h5.attrs["bin_size"] = run.bin_size
        h5.attrs["regions"] = [
            f"{region.chrom}:{region.start}-{region.stop}"
            for region in run.regions]
        h5.attrs["coordinates"] = COORDINATES
        h5.attrs["gain_version"] = __version__
        h5.attrs["created"] = datetime.datetime.now(
            datetime.UTC).isoformat(timespec="seconds")


def _bins_table(run: RunDefinition, counts: list[int]) -> npt.NDArray[Any]:
    """The ``/bins`` table, one row per grid bin, region by region.

    Vectorised per region: the bounds are ``calc_bin_begin`` and
    ``calc_bin_end`` over an index range, with the edge bins clipped to
    the region, exactly as :func:`~gain.binning.binners.grid_bins` has
    them one bin at a time.
    """
    chrom_width = max(len(region.chrom.encode()) for region in run.regions)
    table = np.empty(sum(counts), dtype=[
        ("chrom", f"S{chrom_width}"), ("start", "<i8"), ("end", "<i8")])
    row = 0
    for region, count in zip(run.regions, counts, strict=True):
        first = calc_bin_index(run.bin_size, region.start)
        indexes = np.arange(first, first + count, dtype=np.int64)
        block = table[row:row + count]
        block["chrom"] = region.chrom.encode()
        block["start"] = np.maximum(
            indexes * run.bin_size + 1, region.start)
        block["end"] = np.minimum(
            (indexes + 1) * run.bin_size, region.stop)
        row += count
    return table


def _tracks_table(tracks: list[Track]) -> npt.NDArray[Any]:
    text = h5py.string_dtype(encoding="utf-8")
    return np.array(
        [
            (t.name, t.resource_id, t.score_id, t.aggregator,
             np.nan if t.none_value_replacement is None
             else t.none_value_replacement)
            for t in tracks
        ],
        dtype=[
            ("name", text), ("resource_id", text), ("score_id", text),
            ("aggregator", text), ("none_value_replacement", "<f8")])
