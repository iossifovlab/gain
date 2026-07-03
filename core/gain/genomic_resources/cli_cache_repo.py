"""CLI for caching genomic resources referenced by an annotation pipeline.

The tool resolves a `GenomicResourceRepo` and an `AnnotationPipeline`
from a combination of command-line flags and the registered genomic
context providers, then caches the resources the pipeline depends on.

The annotation pipeline can come from:
  - the positional ``pipeline`` argument — a file path or a GRR resource
    id of type ``annotation_pipeline``. This is supplied by the standard
    ``CLIAnnotationContextProvider`` (the same mechanism
    ``annotate_columns`` / ``annotate_vcf`` use).
  - ``-i / --instance`` — when the ``GPFInstanceContextProvider`` plugin
    is installed, this resolves to the pipeline of the configured GPF
    instance.

When both are supplied, the positional ``pipeline`` wins (it is supplied
by a higher-priority context provider); the tool logs which source it
used. When neither is supplied (positional omitted / left at its
``"context"`` sentinel and no instance pipeline available), the tool logs
a warning and exits cleanly without caching anything.
"""
import argparse
import sys
import time

from gain import logging
from gain.annotation.annotate_utils import (
    build_cli_genomic_context,
    cache_pipeline_resources,
    get_grr_from_context,
)
from gain.annotation.annotation_genomic_context_cli import (
    get_context_pipeline,
)
from gain.genomic_resources.genomic_context import (
    context_providers_add_argparser_arguments,
)
from gain.utils.verbosity_configuration import VerbosityConfiguration

logger = logging.getLogger("grr_cache_repo")


def cli_cache_repo(argv: list[str] | None = None) -> None:
    """Cache genomic resources used by an annotation pipeline."""
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(
        description="Cache the genomic resources used by an "
                    "annotation pipeline.")
    parser.add_argument(
        "--jobs", "-j", type=int, default=4,
        help="Number of parallel workers fetching resources.")
    parser.add_argument(
        "--no-progress", dest="progress", action="store_false",
        help="Disable the progress indication (live bar on a terminal, "
             "milestone log lines otherwise).")
    context_providers_add_argparser_arguments(parser)
    VerbosityConfiguration.set_arguments(parser)
    args = parser.parse_args(argv)
    VerbosityConfiguration.set(args)

    context = build_cli_genomic_context(vars(args))
    grr = get_grr_from_context(context)

    pipeline = get_context_pipeline(context)
    if pipeline is None:
        logger.warning("no pipeline supplied; nothing to cache")
        return

    if args.pipeline != "context":
        logger.info(
            "caching pipeline from positional arg %s", args.pipeline)
    else:
        logger.info("caching pipeline from gpf instance / genomic context")

    start = time.time()
    cache_pipeline_resources(
        grr, pipeline, workers=args.jobs, progress=args.progress)
    logger.info("cached pipeline resources in %.2f secs", time.time() - start)
