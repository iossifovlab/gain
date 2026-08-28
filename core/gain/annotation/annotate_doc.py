from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from gain import logging
from gain.annotation.annotate_utils import get_pipeline_from_context
from gain.annotation.pipeline_doc import render_pipeline_doc
from gain.genomic_resources.genomic_context import (
    context_providers_add_argparser_arguments,
    context_providers_init,
    get_genomic_context,
)
from gain.utils.verbosity_configuration import VerbosityConfiguration

logger = logging.getLogger("annotate_doc")


def configure_argument_parser() -> argparse.ArgumentParser:
    """Construct and configure argument parser."""
    parser = argparse.ArgumentParser(
        description="Annotate columns",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-o", "--output",
                        help="Filename of the output VCF result",
                        default=None)
    VerbosityConfiguration.set_arguments(parser)
    return parser


def cli(raw_args: list[str] | None = None) -> None:
    """Run command line interface for annotate_vcf tool."""
    if not raw_args:
        raw_args = sys.argv[1:]

    parser = configure_argument_parser()
    context_providers_add_argparser_arguments(parser)

    args = parser.parse_args(raw_args)
    VerbosityConfiguration.set(args)
    context_providers_init(**vars(args))

    context = get_genomic_context()
    # The sibling annotate tools' idiom, and a reachable case: the pipeline
    # argument is optional, so running the tool without one leaves the
    # context with no pipeline to describe.  That used to reach the template
    # as ``None`` and surface as ``UndefinedError: 'None' has no attribute
    # 'preamble'``; it now says what is actually wrong.
    pipeline = get_pipeline_from_context(context)

    pipeline_path = None
    if os.path.exists(args.pipeline):
        pipeline_path = args.pipeline

    html_doc = render_pipeline_doc(pipeline, pipeline_path=pipeline_path)
    if args.output:
        Path(args.output).write_text(html_doc)
    else:
        print(html_doc)


if __name__ == "__main__":
    cli(sys.argv[1:])
