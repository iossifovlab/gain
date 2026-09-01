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

That policy is **one object**, not one callable per address (#970).  #952
injected the pair as two independent arguments, which left a public
resource address beside a relative histogram address representable -- the
same drift as #841, merely moved up a level and into a single call.  Both
policies live here, beside the renderer that chooses between them, and
the repository-relative one needs nothing but the page's own resource.
"""
from __future__ import annotations

from typing import Protocol
from urllib.parse import quote

from gain import logging
from gain.annotation.annotation_pipeline import AnnotationPipeline
from gain.genomic_resources.genomic_scores import GenomicScore
from gain.genomic_resources.repository import GenomicResource
from gain.templates import get_template

logger = logging.getLogger(__name__)

DOC_TEMPLATE_NAME = "annotate_doc_pipeline_template.jinja"


class PipelineDocAddresses(Protocol):
    """Where a rendered page points, for every kind of thing it points at.

    One object rather than a callable per address: the two are a single
    policy, and a page that mixes them -- a resource named on the public
    mirror beside a histogram named relatively -- is incoherent.  Passed
    separately that pairing was merely unlikely; passed together it is
    unrepresentable (#970).
    """

    def resource_url(self, resource: GenomicResource) -> str:
        """Address of the resource's documentation page."""
        ...

    def histogram_url(
        self, score: GenomicScore, score_id: str,
    ) -> str | None:
        """Address of the score's histogram image for ``score_id``.

        ``None`` when the score has no histogram to show.
        """
        ...


class PublicMirrorAddresses:
    """Address everything on the GRR's public mirror.

    What a reader who is *not* browsing the GRR tree needs: the page may
    be downloaded, or served from somewhere else entirely, so nothing on
    it may be relative to where it happens to sit.
    """

    def resource_url(self, resource: GenomicResource) -> str:
        return resource.get_public_url()

    def histogram_url(
        self, score: GenomicScore, score_id: str,
    ) -> str | None:
        return score.get_histogram_image_public_url(score_id)


#: The policy every caller gets unless it says otherwise.  Stateless, so
#: one shared instance rather than one per render.
PUBLIC_MIRROR_ADDRESSES = PublicMirrorAddresses()


class RepositoryRelativeAddresses:
    """Address managed targets relative to the repository root.

    The policy the ``annotation_pipeline`` resource implementation wants:
    its pages are published *inside* the GRR tree, under the pipeline's
    own resource id, so a reader browsing that tree resolves the links
    without ever leaving it.  Anything outside the managed GRR cannot be
    reached that way and falls back to the mirror, with a warning.

    Built from the pipeline's own resource, and needs nothing else -- no
    implementation, no repository handle.
    """

    def __init__(self, resource: GenomicResource) -> None:
        self.resource = resource

    @property
    def _prefix_to_root_dir(self) -> str:
        return "/".join([".."] * len(self.resource.resource_id.split("/")))

    def _is_managed(self, url: str, target: GenomicResource) -> bool:
        """Whether ``url`` is published under the same root as this page.

        This is the whole of the rule the two addresses share, warning
        included: only a target under the managed GRR can be reached by a
        relative path from inside the tree, and a curator who has pointed
        a pipeline at some other repository wants to hear about it.  The
        *probe* differs per address -- see the call sites -- but the test
        and the warning are decided once, here.
        """
        if self.resource.get_repo_url() in url:
            return True
        logger.warning(
            "Referencing resource outside managed GRR %s", target.get_id(),
        )
        return False

    def resource_url(self, resource: GenomicResource) -> str:
        if not self._is_managed(resource.get_url(), resource):
            return resource.get_public_url()
        return "/".join([self._prefix_to_root_dir, resource.resource_id])

    def histogram_url(self, score: GenomicScore, score_id: str) -> str | None:
        """Address the score's histogram image, if it has one.

        Probed differently from :meth:`resource_url`, and deliberately:
        what has to be under the managed GRR is the *image*, not the
        score's own page.
        """
        # The probe doubles as the "is there an image at all" guard, and
        # has to answer that *before* the containment rule is consulted:
        # a score with no histogram has no address either way, managed or
        # not, and must not draw a warning about the repository it is in.
        image_url = score.get_histogram_image_url(score_id)
        if image_url is None:
            return None
        if not self._is_managed(image_url, score.resource):
            return score.get_histogram_image_public_url(score_id)
        # Quoted, unlike the resource id above: this tail is built from
        # the score id and reaches the page as a bare `src`. It is itself
        # a two-segment path, which is why `quote` is left to spare "/".
        return "/".join([
            self._prefix_to_root_dir,
            score.resource.resource_id,
            quote(score.get_histogram_image_filename(score_id)),
        ])


def render_pipeline_doc(
    pipeline: AnnotationPipeline,
    *,
    pipeline_path: str | None = None,
    addresses: PipelineDocAddresses = PUBLIC_MIRROR_ADDRESSES,
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
        # The template still asks for the two addresses by name, and this
        # is the one place they are taken apart -- so the pair it renders
        # always comes from a single policy.
        res_url=addresses.resource_url,
        hist_url=addresses.histogram_url,
    )
