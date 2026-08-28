# pylint: disable=C0116
"""The shared pipeline-doc renderer and the address policy it defaults to.

One template, three callers: the ``annotate_doc`` CLI, the web API's
download endpoint, and the ``annotation_pipeline`` resource
implementation. Before #952 each built its own ``res_url``/``hist_url``
pair, and a correction to one copy stayed out of the others --
``d8624b787`` moved the CLI's addresses onto the GRR's public mirror and
left the endpoint's on the repository's own url, where they stayed for two
months (#841).

What is pinned here is which policy the renderer reaches for, and that the
policy can be replaced. How an address is *built* is not this module's
subject: the join, its trailing separators, the no-``public_url``
fallback and the per-child-repo hosts all belong to ``get_public_url()``
and are pinned in ``tests/small/genomic_resources/test_resource_public_url``.
Restating them here would only add a second, more brittle spelling of the
same facts, in HTML substrings that a template edit could break.

That the renderer is the *only* thing binding the template is an
architectural fence, and lives with the others in ``tests/test_architecture``.
"""
import pathlib
from typing import Any

import pytest
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.annotation.pipeline_doc import render_pipeline_doc
from gain.genomic_resources.repository import GenomicResourceRepo

from tests.small.genomic_resources.test_resource_public_url import (
    a_repo_over,
    a_score_at,
)

PIPELINE = "- position_score: scores/pos1\n"

PUBLIC_URL = "http://grr.example.org"


@pytest.fixture
def public_repo(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    """A GRR with one score, advertising a public mirror of its own."""
    a_score_at(tmp_path / "grr")
    return a_repo_over(str(tmp_path / "grr"), PUBLIC_URL)


def render(
    repo: GenomicResourceRepo,
    work_dir: pathlib.Path,
    **kwargs: Any,
) -> str:
    """Render the document for ``PIPELINE`` through the shared seam."""
    pipeline = load_pipeline_from_yaml(PIPELINE, repo, work_dir=work_dir)
    return render_pipeline_doc(pipeline, **kwargs)


def test_resource_is_linked_on_the_grrs_public_url(
    public_repo: GenomicResourceRepo, tmp_path: pathlib.Path,
) -> None:
    # Not a restatement of the join: what this pins is that the renderer
    # asks for the *public* address at all. Its default asked for
    # get_url() until d8624b787, and the endpoint's copy kept doing so
    # until #841.
    html = render(public_repo, tmp_path / "work")

    assert f'href="{PUBLIC_URL}/scores/pos1/index.html"' in html


def test_histogram_image_is_sourced_from_the_grrs_public_url(
    public_repo: GenomicResourceRepo, tmp_path: pathlib.Path,
) -> None:
    html = render(public_repo, tmp_path / "work")

    assert (
        f'src="{PUBLIC_URL}/scores/pos1/statistics/histogram_score.png"'
    ) in html


def test_an_injected_address_policy_replaces_the_public_mirror_default(
    public_repo: GenomicResourceRepo, tmp_path: pathlib.Path,
) -> None:
    # What the resource implementation relies on: its pages are served
    # from inside the GRR tree, so it supplies repository-relative
    # addresses. A renderer that hardcoded the public mirror would break
    # every static page under grr-*.iossifovlab.com.
    html = render(
        public_repo, tmp_path / "work",
        res_url=lambda resource: f"../{resource.resource_id}",
        hist_url=lambda score, score_id: (
            f"../{score.resource.resource_id}/{score_id}.png"
        ),
    )

    assert 'href="../scores/pos1/index.html"' in html
    assert 'src="../scores/pos1/score.png"' in html
    assert PUBLIC_URL not in html


def test_the_pipeline_path_is_shown_when_the_caller_names_a_file(
    public_repo: GenomicResourceRepo, tmp_path: pathlib.Path,
) -> None:
    html = render(
        public_repo, tmp_path / "work",
        pipeline_path="/configs/annotation.yaml")

    assert "Pipeline path: /configs/annotation.yaml" in html


def test_no_pipeline_path_block_when_the_caller_names_no_file(
    public_repo: GenomicResourceRepo, tmp_path: pathlib.Path,
) -> None:
    # Two of the three callers have no file to name. They leave the
    # argument at its default rather than passing a placeholder, and the
    # page must then carry no pipeline-path block at all -- the same page
    # a caller that omitted the argument entirely used to produce.
    html = render(public_repo, tmp_path / "work")

    assert "Pipeline path:" not in html
