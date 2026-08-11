import tempfile
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import quote

from gain import logging
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.annotation.annotation_pipeline import AnnotationPipeline
from gain.genomic_resources.genomic_scores import GenomicScore
from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceRepo,
)
from gain.genomic_resources.resource_implementation import (
    GenomicResourceImplementation,
    InfoImplementationMixin,
)
from gain.task_graph.graph import TaskDesc
from gain.templates import get_template

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

    @property
    def _relative_prefix_to_root_dir(self) -> str:
        return "/".join([".."] * len(self.resource.resource_id.split("/")))

    def _make_resource_url(self, resource: GenomicResource) -> str:
        repo_url = self.resource.get_repo_url()
        res_url = resource.get_url()
        if repo_url in res_url:
            res_url = "/".join([
                self._relative_prefix_to_root_dir,
                resource.resource_id,
            ])
        else:
            logger.warning(
                "Referencing resource outside managed GRR %s",
                resource.get_id(),
            )
            res_url = resource.get_public_url()
        return res_url

    def _make_histogram_url(
        self, score: GenomicScore, score_id: str,
    ) -> str | None:
        repo_url = self.resource.get_repo_url()
        hist_url = score.get_histogram_image_url(score_id)
        if hist_url is None:
            return None
        if repo_url in hist_url:
            hist_url = "/".join([
                self._relative_prefix_to_root_dir,
                score.resource.resource_id,
                quote(score.get_histogram_image_filename(score_id)),
            ])
        else:
            logger.warning(
                "Referencing resource outside managed GRR %s",
                score.resource.get_id(),
            )
            hist_url = score.get_histogram_image_public_url(score_id)
        return hist_url

    def _get_template_data(self) -> dict[str, Any]:
        if self.pipeline is None:
            raise ValueError
        doc_template = get_template("annotate_doc_pipeline_template.jinja")
        return {
            "content": doc_template.render(
                pipeline=self.pipeline,
                res_url=self._make_resource_url,
                hist_url=self._make_histogram_url,
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
