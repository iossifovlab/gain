"""The one renderer of the pipeline documentation page.

Three callers render ``annotate_doc_pipeline_template.jinja``: the
``annotate_doc`` CLI, the web API's download endpoint, and the
``annotation_pipeline`` resource implementation.  Each used to bind the
template and build its own ``res_url``/``hist_url`` pair, and the copies
drifted -- ``d8624b787`` moved the CLI's addresses onto the GRR's public
mirror and left the endpoint's on the repository's own url, where they
stayed wrong for two months (#841, #952).

The addresses are a *policy*, not a constant, so they are injected rather
than hardcoded.  Two callers want the public-mirror policy and get it by
default; the resource implementation publishes its pages from inside the
GRR tree and passes repository-relative addresses instead.
"""
from __future__ import annotations

from collections.abc import Callable

from gain.annotation.annotation_pipeline import AnnotationPipeline
from gain.genomic_resources.genomic_scores import GenomicScore
from gain.genomic_resources.repository import GenomicResource
from gain.templates import get_template

#: Address of a resource's documentation page, as the document should carry
#: it.  Takes the resource; returns a url.
ResourceUrl = Callable[[GenomicResource], str]

#: Address of a score's histogram image for one score id, or ``None`` when
#: the score has no histogram to show.
HistogramUrl = Callable[[GenomicScore, str], str | None]

DOC_TEMPLATE_NAME = "annotate_doc_pipeline_template.jinja"


def public_resource_url(resource: GenomicResource) -> str:
    """Address the resource on the GRR's public mirror."""
    return resource.get_public_url()


def public_histogram_url(score: GenomicScore, score_id: str) -> str | None:
    """Address the score's histogram image on the GRR's public mirror."""
    return score.get_histogram_image_public_url(score_id)


def render_pipeline_doc(
    pipeline: AnnotationPipeline,
    *,
    pipeline_path: str | None = None,
    res_url: ResourceUrl = public_resource_url,
    hist_url: HistogramUrl = public_histogram_url,
) -> str:
    """Render the documentation page for ``pipeline``.

    ``pipeline_path`` is shown on the page when given; the callers that
    have no file to name leave it ``None``, which renders the same page as
    omitting it entirely.
    """
    template = get_template(DOC_TEMPLATE_NAME)
    return template.render(
        pipeline=pipeline,
        pipeline_path=pipeline_path,
        res_url=res_url,
        hist_url=hist_url,
    )
