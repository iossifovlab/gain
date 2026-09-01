import tempfile
from pathlib import Path
from typing import Any, ClassVar

from gain import logging
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.annotation.annotation_pipeline import AnnotationPipeline
from gain.annotation.pipeline_doc import (
    RepositoryRelativeAddresses,
    render_pipeline_doc,
)
from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceRepo,
)
from gain.genomic_resources.resource_implementation import (
    GenomicResourceImplementation,
    InfoImplementationMixin,
)
from gain.task_graph.graph import TaskDesc

logger = logging.getLogger(__name__)


class AnnotationPipelineImplementation(
    GenomicResourceImplementation,
    InfoImplementationMixin,
):
    """Resource implementation for annotation pipeline."""

    def __init__(self, resource: GenomicResource):
        if resource.get_type() != "annotation_pipeline":
            logger.error(
                "trying to open a resource %s of type "
                "%s as annotation pipeline",
                resource.resource_id, resource.get_type())
            raise ValueError(
                f"wrong resource type {resource.get_type()} of "
                f"{resource.resource_id}; expected annotation_pipeline")

        super().__init__(resource)

        self.raw: str = self.resource.get_file_content(
            self.resource.get_config()["filename"])
        self.pipeline: AnnotationPipeline | None = None

    def _load_pipeline_to_describe(
        self, grr: GenomicResourceRepo,
    ) -> AnnotationPipeline:
        """Build the pipeline the page describes.

        A page renders what the pipeline *is*, and never runs it: the
        annotators are built so their attributes and resources can be listed,
        and nothing here opens them.  So the ``work_dir`` handed in is needed
        only to satisfy the annotator constructors, and no directory is ever
        created under it -- ``AnnotatorBase`` makes that in ``open()``, which
        this path does not reach (pinned by
        ``test_rendering_a_page_does_not_open_the_pipeline``).

        It is still named explicitly rather than left to
        ``build_annotation_pipeline``'s fallback, which is deprecated and due
        to become an error (#333): ``grr_manage`` renders both pages of every
        resource, so this caller alone accounted for two deprecation warnings
        per pipeline resource in every repo-wide run (#507).  A temporary
        directory scoped to the call, rather than a fixed shared path: a
        constant under the system temp dir is the shape that made a
        pipeline's work dir another user's to create (#276, #278).
        """
        with tempfile.TemporaryDirectory(
                prefix="gain-annotation-doc-") as work_dir:
            return load_pipeline_from_yaml(
                self.raw, grr, work_dir=Path(work_dir))

    def get_info(self, **kwargs: Any) -> str:
        self.pipeline = self._load_pipeline_to_describe(kwargs["repo"])
        return InfoImplementationMixin.get_info(self)

    def get_statistics_info(self, **kwargs: Any) -> str:
        self.pipeline = self._load_pipeline_to_describe(kwargs["repo"])
        return InfoImplementationMixin.get_statistics_info(self)

    template_name: ClassVar[str] = "annotation_pipeline.jinja"

    def _get_template_data(self) -> dict[str, Any]:
        if self.pipeline is None:
            raise ValueError
        return {
            # This page is published inside the GRR tree, so it must carry
            # addresses relative to the repository root -- the renderer's
            # default is the public mirror, which resolves nowhere from
            # there. Pinned by
            # ``test_the_rendered_page_carries_the_relative_addresses``.
            "content": render_pipeline_doc(
                self.pipeline,
                addresses=RepositoryRelativeAddresses(self.resource),
            ),
        }

    @property
    def files(self) -> set[str]:
        return {self.resource.get_config()["filename"]}

    def calc_statistics_hash(self) -> bytes:
        return b"placeholder"

    def calc_info_hash(self) -> bytes:
        return b"placeholder"

    def create_statistics_build_tasks(
            self, **kwargs: Any,  # noqa: ARG002
    ) -> list[TaskDesc]:
        return []
