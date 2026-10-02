"""``binning_tool``: bin genomic scores into a fixed genome grid.

One task per (job, bundle of consecutive regions) writes a column chunk
per track and region as a ``.npy`` vector in the work directory; one
serial writer task assembles the HDF5 file region by region.  HDF5 has a
single writer, so no task other than the writer touches the file, and a
rerun with the same work directory reuses the finished chunks and reruns
only the writer.
"""
from __future__ import annotations

import argparse
import datetime
import functools
import hashlib
import json
import os
import sys
import urllib.parse
from contextlib import chdir
from typing import Any

import h5py
import numpy as np
import numpy.typing as npt
import yaml

from gain import __version__
from gain.binning.binners import (
    Binner,
    BinningJob,
    Track,
    discover_binner_kinds,
)
from gain.binning.fragment_binner import FragmentBinningJob
from gain.binning.run_definition import (
    RunDefinition,
    RunDefinitionError,
    parse_run_definition,
)
from gain.genomic_resources.genomic_context import (
    build_cli_genomic_context,
    context_providers_add_argparser_arguments,
    get_grr_from_context,
)
from gain.genomic_resources.genomic_context_cli import (
    GENOMIC_CONTEXT_PATH_KEYS,
)
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
from gain.task_graph.work_dir import (
    absolutize_path_args,
    apply_work_dir_defaults,
    maybe_remove_work_dir,
)
from gain.utils.regions import BedRegion, bundle_regions, calc_bin_index
from gain.utils.verbosity_configuration import VerbosityConfiguration

COORDINATES = "1-based-inclusive"
# Rows per HDF5 chunk of ``/values``: "every track for one chromosome" is
# then a contiguous read, and gzip collapses the NaN- and zero-heavy runs.
ROW_BLOCK = 8192
# Bases of consecutive regions one task bins: chromosome-sized, so the
# hundreds of small contigs of a human genome pack into a few tasks while
# a chromosome stays one (ADR 0025, the D13 amendment, has the numbers).
TASK_BUDGET = 50_000_000


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bin position and fragment scores into a fixed "
        "genome grid, writing one HDF5 file with a bins x tracks matrix.")
    parser.add_argument(
        "run_definition",
        help="the run definition (YAML): bins and binner entries")
    parser.add_argument(
        "-o", "--output", default=None,
        help="the HDF5 file to write; defaults to the run definition "
        "with an .h5 suffix, beside it")
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
        help="resolve every query, print the track list, the region, "
        "bin and task counts and, per fragment entry, its resources, "
        "metadata tables, group and track counts and warnings, and "
        "write nothing")
    parser.add_argument(
        "--task-budget", type=int, default=TASK_BUDGET, metavar="BP",
        help="how many bases of consecutive regions one task bins; a "
        "region is never split, so a chromosome longer than the budget "
        "is a task of its own; 0 or less is one task per track over the "
        "whole run, and 1 is one task per region "
        f"(default: {TASK_BUDGET:_})")
    # Only the GRR options are this tool's business.  It never builds an
    # annotation pipeline (and the provider's optional ``pipeline``
    # positional swallows a stray argument typed after the run
    # definition), never reads gene models, and takes the reference
    # genome from the run definition alone -- see ``_resolve_genome``.
    context_providers_add_argparser_arguments(
        parser, skip_cli_annotation_context=True,
        skip_cli_reference_genome=True, skip_cli_gene_models=True)
    TaskGraphCli.add_arguments(
        parser, default_task_status_dir=None, use_commands=False)
    VerbosityConfiguration.set_arguments(parser)
    return parser


def cli(argv: list[str] | None = None) -> None:
    """Entry point of ``binning_tool``."""
    if argv is None:
        argv = sys.argv[1:]
    args = vars(_build_argument_parser().parse_args(argv))
    VerbosityConfiguration.set(args)
    if args.get("output") is None:
        args["output"] = \
            f"{os.path.splitext(args['run_definition'])[0]}.h5"
    # Paths the user may have named relative to where the command was
    # typed; the tasks run inside the work directory, so they are resolved
    # before the GRR definition the workers rebuild is derived from them.
    absolutize_path_args(
        args, input_key="run_definition",
        extra_keys=GENOMIC_CONTEXT_PATH_KEYS)

    with open(args["run_definition"]) as infile:
        config = yaml.safe_load(infile)

    context = build_cli_genomic_context(args)
    grr = get_grr_from_context(context)
    try:
        genome = _resolve_genome(config, grr)
        with genome:
            run = parse_run_definition(
                config, grr, genome,
                base_dir=os.path.dirname(args["run_definition"]))
    except RunDefinitionError as err:
        print(f"{args['run_definition']}: {err}", file=sys.stderr)
        sys.exit(1)

    if args["dry_run"]:
        _print_plan(run, args["task_budget"])
        return

    apply_work_dir_defaults(args)
    # Inside the work dir, as the annotate tools run: an index a worker
    # fetches for a remote resource then lands there, not in the launch
    # directory.
    with chdir(args["work_dir"]):
        task_graph = _build_task_graph(run, args, grr)
        result = TaskGraphCli.process_graph(task_graph, **args)
    maybe_remove_work_dir(args, result=result)


def _resolve_genome(
    config: dict[str, Any], grr: GenomicResourceRepo,
) -> ReferenceGenome:
    """The run definition names the genome; nothing else supplies it.

    The genome's chromosome lengths decide the grid, so one run
    definition must always describe one matrix.  A genome taken from the
    command line or from the ambient genomic context would let the
    invocation, rather than the file, decide what was binned -- and the
    output records only the resource id, not where it came from.
    """
    named = config.get("input_reference_genome")
    if not named:
        raise RunDefinitionError(
            "input_reference_genome is required: name the reference "
            "genome resource whose chromosomes define the grid")
    return build_reference_genome_from_resource_id(named, grr)


def _print_plan(run: RunDefinition, task_budget: int) -> None:
    print("tracks:")
    for track in run.tracks:
        print(
            f"  {track.name}\t{','.join(track.resource_ids)}\t"
            f"{track.score_id}\t{track.aggregator}")
    print(f"regions: {len(run.regions)}")
    print(f"bins: {sum(_bin_count(r, run.bin_size) for r in run.regions)}")
    bundles = bundle_regions(run.regions, task_budget)
    print(f"tasks: {len(bundles) * len(_distinct_jobs(run))}")
    _print_fragment_entries(run)


def _print_fragment_entries(run: RunDefinition) -> None:
    """Per fragment entry: what it matched, read and produces.

    The resources, the metadata tables (resource ids, or the absolute
    paths of local files), the group and track counts, and every
    warning its resolution raised.
    """
    entries: dict[str, list[FragmentBinningJob]] = {}
    for job in run.jobs:
        if isinstance(job, FragmentBinningJob):
            entries.setdefault(job.entry, []).append(job)
    for entry, jobs in entries.items():
        resources = sorted({r for job in jobs for r in job.resource_ids})
        tables = sorted({
            job.grouping.table.name for job in jobs
            if job.grouping is not None})
        groups = {track.group for job in jobs for track in job.tracks}
        print(f"{entry}: {jobs[0].binner}")
        print(f"    resources: {', '.join(resources)}")
        print(f"    tables: {', '.join(tables) or 'none'}")
        print(f"    groups: {len(groups)}")
        print(f"    tracks: {sum(len(job.tracks) for job in jobs)}")
        for warning in dict.fromkeys(
                w for job in jobs for w in job.warnings):
            print(f"    warning: {warning}")


def _local_files(run: RunDefinition) -> list[str]:
    """Every distinct local metadata file the run reads, in job order."""
    return list(dict.fromkeys(
        path for job in run.jobs if isinstance(job, FragmentBinningJob)
        for path in job.local_files))


def _bin_count(region: BedRegion, bin_size: int) -> int:
    return calc_bin_index(bin_size, region.stop) \
        - calc_bin_index(bin_size, region.start) + 1


def _build_task_graph(
    run: RunDefinition, args: dict[str, Any], grr: GenomicResourceRepo,
) -> TaskGraph:
    """One task per (job, bundle of regions), then one serial writer.

    A task writes one chunk per track of its job and region of its
    bundle, so the chunks -- and the writer that assembles them region by
    region -- are the same whatever the budget; only how many tasks there
    are changes.
    """
    assert grr.definition is not None
    kinds = discover_binner_kinds()
    graph = TaskGraph()
    graph.input_files.append(args["run_definition"])
    # A local metadata table decides which track each barcode reaches,
    # yet its path alone names the chunks: as an input of every task, an
    # edit to it recomputes them instead of reusing the old grouping.
    graph.input_files.extend(_local_files(run))
    chunk_dir = os.path.join(args["work_dir"], "chunks")
    os.makedirs(chunk_dir, exist_ok=True)

    chunk_tasks = []
    jobs = _distinct_jobs(run)
    for bundle in bundle_regions(run.regions, args["task_budget"]):
        for job in jobs:
            paths = [
                _chunk_path(chunk_dir, track, region, run.bin_size)
                for region in bundle
                for track in job.tracks
            ]
            chunk_tasks.append(graph.create_task(
                _task_id(job, bundle, run.bin_size), _bin_chunks,
                args=[kinds[job.binner], job, bundle, run.bin_size,
                      grr.definition, chunk_dir],
                output_files=paths,
            ))
    # chunk_paths[region][track], the writer's map of the work directory.
    chunk_paths = [
        [
            _chunk_path(chunk_dir, track, region, run.bin_size)
            for track in run.tracks
        ]
        for region in run.regions
    ]

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


def _distinct_jobs(run: RunDefinition) -> list[BinningJob]:
    """The jobs that compute something, each once.

    Two entries can name tracks apart yet compute them identically -- an
    unpooled entry over a resource beside a pooled one matching only it,
    or two pooled queries matching the same set.  Such jobs have the same
    chunks, so the first one's task computes them for both, and the
    writer reads each under every name it was given.
    """
    seen: set[tuple[str, ...]] = set()
    jobs = []
    for job in run.jobs:
        stems = tuple(_track_stem(track, run.bin_size) for track in job.tracks)
        if stems not in seen:
            seen.add(stems)
            jobs.append(job)
    return jobs


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _track_stem(track: Track, bin_size: int) -> str:
    """Everything but the region that decides a track's chunk values.

    One resource is named by its id; a pooled track's resources, which
    may number hundreds, by their count and a digest of the list.  A
    group is folded in URL-quoted, so any value is a safe file name and
    the groups of one job never share a chunk; the kind's ``parameters``
    by a digest.  A position-score track has neither, and its stem is
    what it always was.
    """
    if len(track.resource_ids) == 1:
        resource = track.resource_ids[0].replace("/", "_")
    else:
        resource = (
            f"pool{len(track.resource_ids)}"
            f"-{_digest(','.join(track.resource_ids))}")
    replacement = track.none_value_replacement
    stem = (
        f"{resource}_{track.score_id}_{track.aggregator}"
        f"_{'none' if replacement is None else replacement}")
    if track.group:
        stem += f"_g{urllib.parse.quote(track.group, safe='')}"
    if track.parameters:
        stem += f"_p{_digest(track.parameters)}"
    return f"{stem}_bs{bin_size}"


def _chunk_path(
    chunk_dir: str, track: Track, region: BedRegion, bin_size: int,
) -> str:
    """Name a chunk by everything that decides its values.

    Two run definitions sharing a work directory then share exactly the
    chunks they compute identically, and nothing else: a different bin
    size, aggregator or replacement is a different chunk, not a stale one.
    """
    return os.path.join(
        chunk_dir,
        f"{_track_stem(track, bin_size)}"
        f"_{region.chrom}_{region.start}_{region.stop}.npy")


def _task_id(
    job: BinningJob, bundle: list[BedRegion], bin_size: int,
) -> str:
    """Name a task by its job's first track and the span of its bundle.

    A track belongs to one job, and its chunks to one task per bundle, so
    the first track names the job within a bundle.

    A bundle is a run of the definition's regions, which never overlap,
    so its first and last region name it; a rerun with the same
    definition and budget finds its tasks, and the id stays one line
    however many regions the bundle holds -- it names a file in the
    task-status directory.  Whether a task's chunks are all present is
    the executor's check of its output files, not the id's business.
    """
    first, last = bundle[0], bundle[-1]
    return (
        f"bin_{_track_stem(job.tracks[0], bin_size)}"
        f"_{first.chrom}_{first.start}_{last.chrom}_{last.stop}")


@functools.lru_cache(maxsize=4)
def _repository(definition: str) -> GenomicResourceRepo:
    """The GRR a worker binds to, built once per process, not per task."""
    return build_genomic_resource_repository(json.loads(definition))


def _bin_chunks(
    binner: type[Binner], job: BinningJob, regions: list[BedRegion],
    bin_size: int, grr_definition: dict[str, Any], chunk_dir: str,
) -> None:
    """Write one chunk per track of a job and region of a bundle.

    The job is bound once for the bundle, inside this function's ``with``,
    so its resources are released when the task ends, failed or not.
    Each region's block is saved as soon as it is returned and then
    dropped, so a bundle costs one region of memory however many regions
    it holds -- which is what makes a whole run in one task
    (``--task-budget 0``) affordable.

    Column *i* of a block is track *i*'s chunk, named through the same
    :func:`_chunk_path` the graph declared its outputs with.  A block of
    any other shape than ``(bins of the region, tracks of the job)``, or
    of any dtype but float64, fails the task before any of that region's
    chunks is written.
    """
    grr = _repository(json.dumps(grr_definition, sort_keys=True))
    with binner.bind(job, grr) as bound:
        for region in regions:
            block = bound.bin_region(region, bin_size)
            expected = (_bin_count(region, bin_size), len(job.tracks))
            if block.shape != expected:
                raise ValueError(
                    f"{binner.kind} returned a block of shape "
                    f"{block.shape} for {region.chrom}:{region.start}-"
                    f"{region.stop} at bin size {bin_size}; expected "
                    f"{expected}, one row per bin and one column per track")
            if block.dtype != np.float64:
                raise ValueError(
                    f"{binner.kind} returned a {block.dtype} block for "
                    f"{region.chrom}:{region.start}-{region.stop}; "
                    f"expected float64")
            for column, track in enumerate(job.tracks):
                np.save(
                    _chunk_path(chunk_dir, track, region, bin_size),
                    block[:, column])


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
        _record_local_files(h5, _local_files(run))


def _record_local_files(h5: h5py.File, paths: list[str]) -> None:
    """Root attributes naming each local file the run read (F7).

    A local file is not in the repository, so the output is reproducible
    only where the same file is: its absolute path, size in bytes and
    modification time (UTC, ISO 8601) say which file that was.
    """
    if not paths:
        return
    stats = [os.stat(path) for path in paths]
    h5.attrs["metadata_files"] = paths
    h5.attrs["metadata_file_sizes"] = [stat.st_size for stat in stats]
    h5.attrs["metadata_file_mtimes"] = [
        datetime.datetime.fromtimestamp(stat.st_mtime, datetime.UTC)
        .isoformat()
        for stat in stats]


def _bins_table(run: RunDefinition, counts: list[int]) -> npt.NDArray[Any]:
    """The ``/bins`` table, one row per grid bin, region by region.

    Vectorised per region: the bounds are ``calc_bin_begin`` and
    ``calc_bin_end`` over a ``calc_bin_index`` range, with the edge bins
    clipped to the region -- the same global grid
    :meth:`PositionScore.get_scores_in_bins` yields one bin at a time, so
    a row here names the bin whose value that read produced.
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
    """The ``/tracks`` table, one row per column of ``/values``.

    ``resource_ids`` is the comma-separated list of the resources a
    track was computed from, in resource-id order -- one id for every
    track that is not pooled -- and ``group`` the track's group, empty
    for a position-score track.  A resource id never holds a comma.
    """
    text = h5py.string_dtype(encoding="utf-8")
    return np.array(
        [
            (t.name, ",".join(t.resource_ids), t.group, t.score_id,
             t.aggregator,
             np.nan if t.none_value_replacement is None
             else t.none_value_replacement)
            for t in tracks
        ],
        dtype=[
            ("name", text), ("resource_ids", text), ("group", text),
            ("score_id", text),
            ("aggregator", text), ("none_value_replacement", "<f8")])
