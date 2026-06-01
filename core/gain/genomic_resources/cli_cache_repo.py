"""CLI for caching genomic resources referenced by an annotation pipeline.

The tool resolves a `GenomicResourceRepo` and an `AnnotationPipeline`
from a combination of command-line flags and the registered genomic
context providers, then caches the resources the pipeline depends on.

The annotation pipeline can come from:
  - ``--pipeline / -p`` — a file path or a GRR resource id of type
    ``annotation_pipeline`` (file path takes precedence on the disk).
  - ``-i / --instance`` — when the ``GPFInstanceContextProvider`` plugin
    is installed, this resolves to the pipeline of the configured GPF
    instance.

When both are supplied, ``--pipeline`` wins; the tool logs which
source it used. When neither is supplied, the tool logs a warning and
exits cleanly without caching anything.
"""
import argparse
import logging
import sys
import time

from gain.annotation.annotate_utils import (
    build_cli_genomic_context,
    cache_pipeline_resources,
    get_grr_from_context,
)
from gain.annotation.annotation_factory import (
    load_pipeline_from_file_or_resource,
)
from gain.annotation.annotation_pipeline import AnnotationPipeline
from gain.genomic_resources.genomic_context import (
    context_providers_add_argparser_arguments,
)
from gain.genomic_resources.genomic_context_base import (
    GC_ANNOTATION_PIPELINE_KEY,
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
        "--pipeline", "-p", default=None,
        help="Annotation pipeline source: a yaml file path or a GRR "
             "resource id of type annotation_pipeline. When omitted, "
             "the pipeline is read from the genomic context "
             "(e.g. -i / --instance).")
    parser.add_argument(
        "--jobs", "-j", type=int, default=4,
        help="Number of parallel workers fetching resources.")
    parser.add_argument(
        "--no-progress", dest="progress", action="store_false",
        help="Disable the progress indication (live bar on a terminal, "
             "milestone log lines otherwise).")
    context_providers_add_argparser_arguments(
        parser, skip_cli_annotation_context=True)
    VerbosityConfiguration.set_arguments(parser)
    args = parser.parse_args(argv)
    VerbosityConfiguration.set(args)

    context = build_cli_genomic_context(
        {**vars(args), "skip_cli_annotation_context": True})
    grr = get_grr_from_context(context)

    pipeline: AnnotationPipeline | None = None
    if args.pipeline is not None:
        logger.info(
            "loading pipeline from --pipeline arg %s", args.pipeline)
        pipeline = load_pipeline_from_file_or_resource(args.pipeline, grr)
    else:
        pipeline = context.get_context_object(GC_ANNOTATION_PIPELINE_KEY)
        if pipeline is not None:
            logger.info("loading pipeline from genomic context")

    if pipeline is None:
        logger.warning("no pipeline supplied; nothing to cache")
        return

    start = time.time()
    cache_pipeline_resources(
        grr, pipeline, workers=args.jobs, progress=args.progress)
    logger.info("cached pipeline resources in %.2f secs", time.time() - start)
