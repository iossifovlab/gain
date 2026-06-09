import argparse
import logging
import os
import shutil
import urllib.parse
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import numpy as np
from pysam import TabixFile

from gain.annotation.annotation_factory import (
    load_pipeline_from_file_or_resource,
)
from gain.annotation.annotation_genomic_context_cli import (
    get_context_pipeline,
)
from gain.annotation.annotation_pipeline import (
    AnnotationPipeline,
    ReannotationPipeline,
    print_annotation_plan,
)
from gain.genomic_resources.cached_repository import (
    CachingProtocol,
    cache_resources,
)
from gain.genomic_resources.genomic_context import (
    context_providers_add_argparser_arguments,
    context_providers_init,
    get_genomic_context,
)
from gain.genomic_resources.genomic_context_base import (
    GenomicContext,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.task_graph import TaskGraphCli
from gain.task_graph.graph import TaskGraph
from gain.utils.fs_utils import (
    compression_suffix,
    strip_compression_suffix,
)
from gain.utils.regions import (
    Region,
    get_chromosome_length_tabix,
    split_into_regions,
)
from gain.utils.verbosity_configuration import VerbosityConfiguration

PART_FILENAME = "{in_file}_annotation_{chrom}_{pos_beg}_{pos_end}"

logger = logging.getLogger("annotate_utils")


def produce_regions(
    pysam_file: TabixFile, region_size: int,
) -> list[Region]:
    """Given a region size, produce contig regions to annotate by."""
    contig_lengths: dict[str, int] = {}
    for contig in map(str, pysam_file.contigs):
        length = get_chromosome_length_tabix(pysam_file, contig)
        if length is None:
            raise ValueError(f"unable to find length of contig '{contig}'")
        contig_lengths[contig] = length

    regions: list[Region] = []
    for contig, length in contig_lengths.items():
        regions.extend(split_into_regions(contig, length, region_size))
    return regions


def produce_partfile_paths(
    input_file_path: str, regions: list[Region], work_dir: str,
) -> list[str]:
    """Produce a list of file paths for output region part files."""
    filenames = []
    for region in regions:
        pos_beg = region.start if region.start is not None else "_"
        pos_end = region.stop if region.stop is not None else "_"
        filenames.append(os.path.join(work_dir, PART_FILENAME.format(
            in_file=os.path.basename(input_file_path),
            chrom=region.chrom, pos_beg=pos_beg, pos_end=pos_end,
        )))
    return filenames


def stringify(value: Any, *, vcf: bool = False) -> str:
    """Format the value to a string for human-readable output."""
    if value is None:
        return "." if vcf else ""
    if isinstance(value, (float, np.floating)):
        if 100 <= value < 100_000:
            return f"{value:.6g}"
        return f"{value:.3g}"
    if isinstance(value, bool):
        return "yes" if value else ("." if vcf else "")
    if vcf is True and value == "":
        return "."
    if isinstance(value, (list, tuple)):
        s = str(list(value))
        return urllib.parse.quote(s, safe="") if vcf else s
    if isinstance(value, dict):
        if vcf:
            return urllib.parse.quote(str(value), safe="")
        return ";".join(
            f"{stringify(k, vcf=vcf)}:{stringify(v, vcf=vcf)}"
            for k, v in value.items()
        )
    return str(value)


def build_cli_genomic_context(
    cli_args: dict[str, Any],
) -> GenomicContext:
    """Helper method to collect necessary objects from the genomic context."""
    context_providers_init(**cli_args)
    return get_genomic_context()


def get_pipeline_from_context(context: GenomicContext) -> AnnotationPipeline:
    """Get the annotation pipeline from the genomic context."""
    pipeline = get_context_pipeline(context)
    if pipeline is None:
        raise ValueError("no valid annotation pipeline configured")
    return pipeline


def get_grr_from_context(context: GenomicContext) -> GenomicResourceRepo:
    """Get the genomic resource repository from the genomic context."""
    grr = context.get_genomic_resources_repository()
    if grr is None:
        raise ValueError("no valid GRR configured")
    return grr


def add_input_files_to_task_graph(args: dict, task_graph: TaskGraph) -> None:
    if "input" in args:
        task_graph.input_files.append(args["input"])
    if "pipeline" in args:
        task_graph.input_files.append(args["pipeline"])
    if args.get("reannotate") and os.path.exists(args["reannotate"]):
        task_graph.input_files.append(args["reannotate"])


LOCALITY_WARNING_THRESHOLD = 1000
LOCALITY_ERROR_THRESHOLD = 5000
LOCAL_RESOURCE_SCHEMES = frozenset({"file", "memory"})


def find_nonlocal_resources(
    pipeline: AnnotationPipeline,
) -> list[tuple[str, str]]:
    """Return ``(resource_id, scheme)`` for each non-local pipeline resource.

    A resource is *local* when it is served by a caching protocol (its files
    are mirrored to disk) or by an fsspec protocol with a ``file`` or
    ``memory`` scheme. Everything else (``http``/``https``/``s3``) is
    non-local and would be queried over the network per variant.
    """
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for annotator in pipeline.annotators:
        for resource in annotator.resources:
            if resource.resource_id in seen:
                continue
            seen.add(resource.resource_id)
            proto = resource.proto
            if isinstance(proto, CachingProtocol):
                continue
            scheme = getattr(proto, "scheme", None)
            if scheme in LOCAL_RESOURCE_SCHEMES:
                continue
            result.append((resource.resource_id, scheme or "unknown"))
    return result


def check_resource_locality(
    pipeline: AnnotationPipeline,
    count_rows: Callable[[int], int],
    *,
    allow_remote: bool = False,
) -> None:
    """Guard against annotating many variants over non-local resources.

    ``count_rows(limit)`` returns the number of input rows, capped at
    ``limit`` (short-circuiting so a huge input is never read in full).

    Below ``LOCALITY_WARNING_THRESHOLD`` rows the guard is silent; between
    the warning and error thresholds it logs a warning and proceeds; above
    ``LOCALITY_ERROR_THRESHOLD`` it raises ``ValueError``. Passing
    ``allow_remote`` disables the guard entirely.
    """
    if allow_remote:
        return
    nonlocal_resources = find_nonlocal_resources(pipeline)
    if not nonlocal_resources:
        return

    count = count_rows(LOCALITY_ERROR_THRESHOLD + 1)
    if count <= LOCALITY_WARNING_THRESHOLD:
        return

    listing = ", ".join(
        f"{resource_id} ({scheme})"
        for resource_id, scheme in nonlocal_resources
    )
    if count > LOCALITY_ERROR_THRESHOLD:
        raise ValueError(
            f"refusing to annotate more than {LOCALITY_ERROR_THRESHOLD} "
            f"variants against non-local genomic resources: {listing}. "
            f"Every variant would issue a network request, making the run "
            f"extremely slow. Use a local/directory GRR, configure a caching "
            f"GRR, or pass --allow-remote-resources to proceed anyway.")
    logger.warning(
        "annotating more than %s variants against non-local genomic "
        "resources: %s; each variant issues a network request, which may be "
        "slow. Consider caching these resources or using a local GRR.",
        LOCALITY_WARNING_THRESHOLD, listing)


def cache_pipeline_resources(
    grr: GenomicResourceRepo,
    pipeline: AnnotationPipeline,
    *,
    workers: int | None = None,
    progress: bool = True,
) -> None:
    """Cache resources that the given pipeline will use."""
    resource_ids: set[str] = {
        res.resource_id
        for annotator in pipeline.annotators
        for res in annotator.resources
    }
    cache_resources(grr, resource_ids, workers=workers, progress=progress)


def maybe_wrap_reannotation(
    pipeline: AnnotationPipeline,
    args: dict[str, Any],
    grr: GenomicResourceRepo,
) -> AnnotationPipeline:
    """Wrap ``pipeline`` in a :class:`ReannotationPipeline` if reannotating.

    When ``--reannotate`` is not given the pipeline is returned unchanged.
    Otherwise the previous pipeline is loaded, the new pipeline is wrapped in a
    :class:`ReannotationPipeline`, and the previous pipeline is closed -- the
    wrapper reuses the live new-pipeline annotators and never touches the
    previous pipeline after construction.
    """
    if not args.get("reannotate"):
        return pipeline
    pipeline_previous = load_pipeline_from_file_or_resource(
        args["reannotate"], grr)
    try:
        return ReannotationPipeline(
            pipeline, pipeline_previous,
            full_reannotation=args["full_reannotation"])
    finally:
        # The ReannotationPipeline wrapper shares the live new-pipeline
        # annotators, so close only the previous pipeline here, not the
        # wrapper.
        pipeline_previous.close()


def emit_annotation_plan(
    args: dict[str, Any],
    pipeline: AnnotationPipeline,
    grr: GenomicResourceRepo,
) -> None:
    """Print the (re)annotation plan to stderr.

    With ``--reannotate`` the previous pipeline is loaded and a
    :class:`ReannotationPipeline` plan is rendered; otherwise the plain
    all-ADDED annotation plan is rendered. Printed with ``print`` (not a
    logger) so it is visible at the default WARNING log level.
    """
    if args.get("reannotate"):
        # When ``reannotate`` is set, ``maybe_wrap_reannotation`` always
        # returns a ``ReannotationPipeline``.
        reannotation = cast(
            "ReannotationPipeline",
            maybe_wrap_reannotation(pipeline, args, grr))
        reannotation.print_plan(reference=args["reannotate"])
    else:
        print_annotation_plan(pipeline)


def handle_default_args(args: dict[str, Any]) -> dict[str, Any]:
    """Handle default arguments for annotation command line tools."""
    if not os.path.exists(args["input"]):
        raise ValueError(f"{args['input']} does not exist!")
    output = build_output_path(args["input"], args.get("output"))
    args["output"] = output

    if args.get("work_dir") is None:
        path = Path(strip_compression_suffix(args["output"]))
        path = path.with_suffix("")
        args["work_dir"] = str(f"{path}_work")

    args["work_dir_created"] = not os.path.exists(args["work_dir"])
    if args["work_dir_created"]:
        os.mkdir(args["work_dir"])

    if args.get("task_status_dir") is None:
        args["task_status_dir"] = os.path.join(
            args["work_dir"], ".task-status")
    if args.get("task_log_dir") is None:
        args["task_log_dir"] = os.path.join(
            args["work_dir"], ".task-log")

    for key in ("input", "output", "work_dir",
                "task_status_dir", "task_log_dir",
                "dask_cluster_config_file",
                "grr_filename", "grr_directory"):
        if args.get(key):
            args[key] = os.path.abspath(args[key])

    # pipeline and reannotate may be sentinels (e.g. GRR resource ids)
    # rather than file paths; only absolutize when an actual file exists.
    for key in ("pipeline", "reannotate"):
        value = args.get(key)
        if value and os.path.exists(value):
            args[key] = os.path.abspath(value)

    return args


def maybe_remove_work_dir(args: dict[str, Any], *, result: bool) -> None:
    """Remove the working directory after a clean run, if the tool made it.

    The directory is removed only when every condition holds:

    - the tool created it (it did not pre-exist; see ``work_dir_created``),
    - the command actually ran annotation (not ``list``/``status``),
    - the run succeeded (``result`` is ``True`` -- a ``--keep-going`` run that
      finished with task errors returns ``False`` and is preserved),
    - neither ``--keep-parts`` nor ``--keep-work-dir`` was requested,
    - the output file does not live inside the working directory.

    Removal is best-effort: a failure to remove logs a warning and is not
    fatal, since the annotation has already succeeded.
    """
    if not args.get("work_dir_created"):
        return
    if args.get("command") not in (None, "run"):
        return
    if not result:
        return
    if args.get("keep_parts") or args.get("keep_work_dir"):
        return

    work_dir = Path(os.path.abspath(args["work_dir"]))
    output = Path(os.path.abspath(args["output"]))
    if output.is_relative_to(work_dir):
        logger.warning(
            "output %s is inside the working directory %s; not removing it",
            output, work_dir)
        return

    try:
        shutil.rmtree(work_dir)
    except OSError as err:
        logger.warning(
            "could not remove working directory %s: %s", work_dir, err)
        return
    logger.info("removed working directory %s", work_dir)


def add_common_annotation_arguments(parser: argparse.ArgumentParser) -> None:
    """Add common arguments to an annotation command line parser."""
    parser.add_argument(
        "input", default="-", nargs="?",
        help="the input file; gzip/bgzip-compressed inputs (.gz/.bgz), "
             "optionally tabix-indexed, are detected by extension")
    parser.add_argument(
        "--version", default=False,
        action="store_true", help="Show the GAIn version and exit")
    parser.add_argument(
        "-r", "--region-size", default=300_000_000,
        type=int, help="region size to parallelize by; zero or negative "
        "values disable parallelization")
    parser.add_argument(
        "-w", "--work-dir",
        help="Directory to store intermediate output files in",
        default=None)
    parser.add_argument(
        "-o", "--output",
        help="Filename of the output result; a .gz/.bgz suffix produces a "
             "compressed, tabix-indexed output. If the suffix is omitted, a "
             "compressed input's suffix is mirrored onto the output.",
        default=None)
    parser.add_argument(
        "--reannotate", default=None,
        help="Old pipeline config to reannotate over")
    parser.add_argument(
        "--full-reannotation", "--fr",
        help="Ignore any previous annotation and run "
        " a full reannotation.",
        action="store_true",
    )
    parser.add_argument(
        "--keep-parts", "--keep-intermediate-files",
        help="Keep intermediate files after annotatio.",
        action="store_true",
        default=False,
    )
    parser.add_argument(
        "--no-keep-parts", "--no-keep-intermediate-files",
        help="Remove intermediate files after annotatio.",
        dest="keep_parts",
        action="store_false",
    )
    parser.add_argument(
        "--keep-work-dir",
        help="Keep the working directory after a successful annotation "
             "(by default a working directory the tool created is removed).",
        action="store_true",
        default=False,
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=0,  # 0 = annotate iteratively, no batches
        help="Annotate in batches of",
    )
    parser.add_argument(
        "--allow-remote-resources",
        action="store_true",
        default=False,
        help="Skip the check that warns or aborts when annotating many "
             "variants against non-local (http/https/s3) genomic resources.",
    )

    context_providers_add_argparser_arguments(parser)
    TaskGraphCli.add_arguments(parser, default_task_status_dir=None)
    VerbosityConfiguration.set_arguments(parser)


def build_output_path(raw_input_path: str, output_path: str | None) -> str:
    """Build an output filepath for an annotation tool's output.

    An explicit compression suffix (.gz/.bgz) on the output is preserved.
    An output named without one inherits ("mirrors") the input's compression
    suffix, so a .bgz input yields a .bgz output and a .gz input a .gz output.
    """
    input_suffix = compression_suffix(raw_input_path)
    if output_path:
        if compression_suffix(output_path) is not None:
            return output_path
        if input_suffix is not None:
            return f"{output_path}{input_suffix}"
        return output_path
    # no output filename given, produce from input filename
    path = Path(strip_compression_suffix(raw_input_path))
    # backup suffixes
    suffixes = path.suffixes

    path = Path(path.name)
    # append '.annotated' to filename stem
    path = path.with_stem(f"{path.stem}.annotated")
    # restore suffixes
    base = str(path) if not suffixes else str(path.with_suffix(suffixes[-1]))
    # mirror the input's compression suffix
    return f"{base}{input_suffix}" if input_suffix else base
