"""Command line helpers for constructing annotation pipelines.

The utilities in this module complement the generic genomic context
providers by supplying annotation pipeline objects.  They enable CLI tools to
load pipeline definitions from the file system or from genomic resource
repositories, and to make the resulting :class:`AnnotationPipeline`
instances available through the shared genomic context mechanism.
"""

from __future__ import annotations

import argparse
import pathlib
from typing import Any

from gain import logging
from gain.annotation.annotation_factory import (
    load_pipeline_from_file_or_resource,
)
from gain.annotation.annotation_pipeline import AnnotationPipeline
from gain.genomic_resources.genomic_context_base import (
    GC_ANNOTATION_PIPELINE_KEY,
    GC_GRR_KEY,
    GenomicContext,
    GenomicContextProvider,
    SimpleGenomicContext,
)

logger = logging.getLogger(__name__)


class CLIAnnotationContextProvider(GenomicContextProvider):
    """Expose annotation pipeline configuration through CLI options.

    The provider allows users to point to an annotation pipeline definition
    (either as a file path or a genomic resource identifier) and optionally
    tweak pipeline behaviour via command-line flags.  When invoked without a
    ``pipeline`` argument the provider abstains from creating a context so
    that other providers can supply their default pipelines.
    """

    def __init__(
            self,
    ) -> None:
        """Initialise the provider with its public identifier and priority."""
        super().__init__(
            "CLIAnnotationContextProvider",
            800,
        )

    def add_argparser_arguments(
        self, parser: argparse.ArgumentParser,
        **kwargs: Any,
    ) -> None:
        """Register arguments that describe the annotation pipeline source.

        Parameters
        ----------
        parser
            The parser that should receive the provider specific CLI options.
        """
        if kwargs.get("skip_cli_annotation_context"):
            return
        parser.add_argument(
            "pipeline", default="context", nargs="?",
            help="The pipeline definition file. By default, or if "
            "the value is gpf_instance, the annotation pipeline "
            "from the configured gpf instance will be used.")

        parser.add_argument(
            "-ar", "--allow-repeated-attributes", default=False,
            action="store_true",
            help="Rename repeated attributes instead of raising"
            " an error.")

    def init(self, **kwargs: Any) -> GenomicContext | None:
        """Materialise a genomic context containing an annotation pipeline.

        Parameters
        ----------
        **kwargs
            Keyword arguments parsed from the command line.  The provider
            looks at ``pipeline``, ``allow_repeated_attributes``, and
            ``work_dir``.

        Returns
        -------
        GenomicContext | None
            A context containing the annotation pipeline, or ``None`` when no
            pipeline could be created (for example when the ``pipeline``
            argument is omitted).
        """
        if kwargs.get("skip_cli_annotation_context"):
            return None

        # pylint: disable=import-outside-toplevel
        from gain.genomic_resources.genomic_context import (
            get_genomic_context,
        )

        if kwargs.get("pipeline") is None \
                or kwargs["pipeline"] == "context":
            return None
        logger.info(
            "Using the annotation pipeline from %s.",
            kwargs["pipeline"])
        grr = get_genomic_context().get_context_object(GC_GRR_KEY)
        if grr is None:
            logger.warning(
                "No GRR in the current genomic context, "
                "cannot load the annotation pipeline.")
            return None

        work_dir = None
        if kwargs.get("work_dir"):
            work_dir = pathlib.Path(kwargs["work_dir"])

        pipeline = load_pipeline_from_file_or_resource(
            kwargs["pipeline"], grr,
            allow_repeated_attributes=bool(kwargs.get(
                "allow_repeated_attributes")),
            work_dir=work_dir)
        return SimpleGenomicContext(
            {GC_ANNOTATION_PIPELINE_KEY: pipeline},
            source="CLIAnnotationContextProvider")


def get_context_pipeline(
    context: GenomicContext,
) -> AnnotationPipeline | None:
    """Extract a validated :class:`AnnotationPipeline` from *context*.

    Parameters
    ----------
    context
        The genomic context from which to retrieve the pipeline object.

    Returns
    -------
    AnnotationPipeline | None
        The pipeline instance or ``None`` when the context does not expose a
        pipeline.

    Raises
    ------
    TypeError
        If the context entry is present but does not contain the expected
        :class:`AnnotationPipeline` type.
    """
    pipeline = context.get_context_object(GC_ANNOTATION_PIPELINE_KEY)
    if pipeline is None:
        return None
    if not isinstance(pipeline, AnnotationPipeline):
        raise TypeError(
            f"The annotation pipeline from the genomic "
            f"context is not an AnnotationPipeline: {type(pipeline)}")
    return pipeline
