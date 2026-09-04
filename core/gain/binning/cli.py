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
import logging
import os
import shutil
import sys
from contextlib import chdir
from typing import Any

import h5py
import numpy as np
import numpy.typing as npt
import yaml

from gain import __version__
from gain.binning.binners import Track, discover_binner_kinds, grid_bins
from gain.binning.run_definition import (
    RunDefinition,
    RunDefinitionError,
    parse_run_definition,
)
from gain.genomic_resources.genomic_context import (
    context_providers_add_argparser_arguments,
    context_providers_init,
    get_genomic_context,
)
from gain.genomic_resources.genomic_context_base import GenomicContext
from gain.genomic_resources.reference_genome import (
    ReferenceGenome,
    build_reference_genome_from_resource,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)
from gain.task_graph.cli_tools import TaskGraphCli
from gain.task_graph.graph import TaskGraph
from gain.utils.regions import BedRegion
from gain.utils.verbosity_configuration import VerbosityConfiguration

logger = logging.getLogger(__name__)

COORDINATES = "1-based-inclusive"
# Rows per HDF5 chunk of ``/values``: "every track for one chromosome" is
# then a contiguous read, and gzip collapses the NaN- and zero-heavy runs.
ROW_BLOCK = 8192


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
    # Before the context is built and before the tasks run inside the work
    # directory: a GRR or a run definition named relative to where the
    # user typed the command must still be found from there.
    for key in ("run_definition", "output", "work_dir", "task_status_dir",
                "task_log_dir", "dask_cluster_config_file",
                "grr_filename", "grr_directory"):
        if args.get(key):
            args[key] = os.path.abspath(args[key])

    with open(args["run_definition"]) as infile:
        config = yaml.safe_load(infile)

    context_providers_init(**args)
    context = get_genomic_context()
    grr = context.get_genomic_resources_repository()
    if grr is None:
        raise ValueError("no valid GRR configured")
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
    with chdir(args["work_dir"]):
        task_graph = _build_task_graph(run, args, grr)
        result = TaskGraphCli.process_graph(task_graph, **args)
    _maybe_remove_work_dir(args, result=result)


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
        return build_reference_genome_from_resource(grr.get_resource(named))
    genome = context.get_reference_genome()
    if genome is None:
        raise ValueError(
            "no reference genome: name input_reference_genome in the run "
            "definition, pass -R, or configure one in the genomic context")
    return genome


def _print_plan(run: RunDefinition) -> None:
    n_bins = sum(len(grid_bins(region, run.bin_size)) for region in run.regions)
    print("tracks:")
    for track in run.tracks:
        print(
            f"  {track.name}\t{track.resource_id}\t{track.score_id}\t"
            f"{track.aggregator}")
    print(f"regions: {len(run.regions)}")
    print(f"bins: {n_bins}")


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


def _maybe_remove_work_dir(args: dict[str, Any], *, result: bool) -> None:
    """Remove a work directory the tool made, after a clean run."""
    if not args["work_dir_created"] or not result or args["keep_work_dir"]:
        return
    try:
        shutil.rmtree(args["work_dir"])
    except OSError as err:
        logger.warning(
            "could not remove working directory %s: %s",
            args["work_dir"], err)


def _build_task_graph(
    run: RunDefinition, args: dict[str, Any], grr: GenomicResourceRepo,
) -> TaskGraph:
    """One task per (track, region) chunk, then one serial writer."""
    assert grr.definition is not None
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
                args=[track, region, run.bin_size, grr.definition, path],
                output_files=[path],
            ))
            region_paths.append(path)
        chunk_paths.append(region_paths)

    # The chunks are the writer's inputs: a chunk computed after the
    # output was written -- another run definition sharing the work dir --
    # makes the writer run again instead of leaving a stale file behind.
    graph.create_task(
        "write_hdf5", _write_hdf5,
        args=[args["output"], run, chunk_paths],
        deps=chunk_tasks,
        input_files=[path for paths in chunk_paths for path in paths],
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


def _bin_chunk(
    track: Track, region: BedRegion, bin_size: int,
    grr_definition: dict[str, Any], path: str,
) -> None:
    grr = build_genomic_resource_repository(grr_definition)
    binner = discover_binner_kinds()[track.binner]
    np.save(path, binner.bin_track(track, region, bin_size, grr))


def _write_hdf5(
    output: str, run: RunDefinition, chunk_paths: list[list[str]],
) -> None:
    """Assemble the file: ``/values`` region by region, then the tables."""
    region_bins = [grid_bins(region, run.bin_size) for region in run.regions]
    n_bins = sum(len(bins) for bins in region_bins)
    n_tracks = len(run.tracks)
    with h5py.File(output, "w") as h5:
        values = h5.create_dataset(
            "values", shape=(n_bins, n_tracks), dtype=np.float64,
            chunks=(min(ROW_BLOCK, max(n_bins, 1)), n_tracks),
            compression="gzip", fillvalue=np.nan)
        row = 0
        for bins, paths in zip(region_bins, chunk_paths, strict=True):
            block = np.column_stack([np.load(path) for path in paths])
            values[row:row + len(bins), :] = block
            row += len(bins)
        h5.create_dataset("bins", data=_bins_table(run, region_bins))
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


def _bins_table(
    run: RunDefinition, region_bins: list[list[tuple[int, int]]],
) -> npt.NDArray[Any]:
    chrom_width = max(len(region.chrom.encode()) for region in run.regions)
    table = np.empty(
        sum(len(bins) for bins in region_bins),
        dtype=[("chrom", f"S{chrom_width}"), ("start", "<i8"), ("end", "<i8")])
    row = 0
    for region, bins in zip(run.regions, region_bins, strict=True):
        for start, end in bins:
            table[row] = (region.chrom.encode(), start, end)
            row += 1
    return table


def _tracks_table(tracks: list[Track]) -> npt.NDArray[Any]:
    text = h5py.string_dtype(encoding="utf-8")
    table = np.empty(len(tracks), dtype=[
        ("name", text), ("resource_id", text), ("score_id", text),
        ("aggregator", text), ("none_value_replacement", "<f8")])
    for row, track in enumerate(tracks):
        replacement = track.none_value_replacement
        table[row] = (
            track.name, track.resource_id, track.score_id, track.aggregator,
            np.nan if replacement is None else replacement)
    return table
