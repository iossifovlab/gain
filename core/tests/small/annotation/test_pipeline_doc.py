# pylint: disable=C0116
"""The shared pipeline-doc renderer and the URL policy it defaults to.

One template, three callers: the ``annotate_doc`` CLI, the web API's
download endpoint, and the ``annotation_pipeline`` resource implementation.
Before #952 each built its own ``res_url``/``hist_url`` pair, and a
correction to one copy stayed out of the others -- ``d8624b787`` fixed the
CLI's addresses and left the endpoint's wrong for two months (#841).

The default policy tested here is the *public-mirror* one, which two of the
three callers want. It has to be a default rather than a hardcoded rule: the
resource implementation publishes pages from inside the GRR tree and needs
repository-relative addresses instead, so the policy stays injectable.

These live in ``core`` deliberately. The addresses are the shared
function's behaviour, not the endpoint's, and before #952 they were pinned
only on the web API side -- so a regression in the CLI's copy would have
shipped green.
"""
import pathlib
from typing import Any

from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.annotation.pipeline_doc import render_pipeline_doc
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)
from gain.genomic_resources.testing.builders import a_position_score

PIPELINE = "- position_score: scores/pos1\n"

TWO_CHILD_PIPELINE = """
- position_score:
    resource_id: scores/pos1
    attributes:
    - source: score
      name: from_main
- position_score:
    resource_id: scores/pos2
    attributes:
    - source: score
      name: from_encode
"""


def a_score_at(
    resources_dir: pathlib.Path, resource_id: str = "scores/pos1",
) -> None:
    """Realize one position score the pipeline can annotate with."""
    a_position_score().realize_into(resources_dir / resource_id)


def a_repo_over(
    directory: pathlib.Path, public_url: str | None = None,
) -> GenomicResourceRepo:
    """Build a directory GRR, optionally advertising a public mirror."""
    definition: dict[str, Any] = {
        "id": "main", "type": "dir", "directory": str(directory),
    }
    if public_url is not None:
        definition["public_url"] = public_url
    return build_genomic_resource_repository(definition)


def render(
    repo: GenomicResourceRepo,
    work_dir: pathlib.Path,
    pipeline_config: str = PIPELINE,
    **kwargs: Any,
) -> str:
    """Render the document for ``pipeline_config`` through the shared seam."""
    pipeline = load_pipeline_from_yaml(
        pipeline_config, repo, work_dir=work_dir)
    return render_pipeline_doc(pipeline, **kwargs)


def test_resource_is_linked_on_the_grrs_public_url(
    tmp_path: pathlib.Path,
) -> None:
    a_score_at(tmp_path / "grr")
    repo = a_repo_over(tmp_path / "grr", "http://grr.example.org")

    html = render(repo, tmp_path / "work")

    assert 'href="http://grr.example.org/scores/pos1/index.html"' in html


def test_an_injected_address_policy_replaces_the_public_mirror_default(
    tmp_path: pathlib.Path,
) -> None:
    # What the resource implementation relies on: its pages are served from
    # inside the GRR tree, so it supplies repository-relative addresses. A
    # renderer that hardcoded the public mirror would break those pages.
    a_score_at(tmp_path / "grr")
    repo = a_repo_over(tmp_path / "grr", "http://grr.example.org")

    html = render(
        repo, tmp_path / "work",
        res_url=lambda resource: f"../{resource.resource_id}",
        hist_url=lambda score, score_id: (
            f"../{score.resource.resource_id}/{score_id}.png"
        ),
    )

    assert 'href="../scores/pos1/index.html"' in html
    assert 'src="../scores/pos1/score.png"' in html
    assert "grr.example.org" not in html


def test_histogram_image_is_sourced_from_the_grrs_public_url(
    tmp_path: pathlib.Path,
) -> None:
    a_score_at(tmp_path / "grr")
    repo = a_repo_over(tmp_path / "grr", "http://grr.example.org")

    html = render(repo, tmp_path / "work")

    assert (
        'src="http://grr.example.org'
        '/scores/pos1/statistics/histogram_score.png"'
    ) in html


def test_each_resource_is_linked_on_the_host_of_its_own_child_repo(
    tmp_path: pathlib.Path,
) -> None:
    # The shape production deploys: one group whose children are published
    # on two different hosts. A document that annotates with a resource
    # from each is only correct if every address is derived from the child
    # the resource actually came from.
    a_score_at(tmp_path / "main")
    a_score_at(tmp_path / "enc", "scores/pos2")
    repo = build_genomic_resource_repository({
        "id": "group",
        "type": "group",
        "children": [
            {
                "id": "main",
                "type": "dir",
                "directory": str(tmp_path / "main"),
                "public_url": "http://grr.example.org",
            },
            {
                "id": "encode",
                "type": "dir",
                "directory": str(tmp_path / "enc"),
                "public_url": "http://grr-encode.example.org",
            },
        ],
    })

    # Named attributes: two bare position scores would both contribute an
    # attribute called "score", which the pipeline refuses as a repeat.
    html = render(repo, tmp_path / "work", TWO_CHILD_PIPELINE)

    assert 'href="http://grr.example.org/scores/pos1/index.html"' in html
    assert (
        'href="http://grr-encode.example.org/scores/pos2/index.html"'
    ) in html


def test_a_definition_without_a_public_url_falls_back_to_the_repo_url(
    tmp_path: pathlib.Path,
) -> None:
    # ``public_url`` is optional -- a GRR that never declared a public
    # mirror still has to render a document rather than raise, and the
    # addresses it carries stay the ones it carried before #841.
    a_score_at(tmp_path / "grr")
    repo = a_repo_over(tmp_path / "grr")

    html = render(repo, tmp_path / "work")

    resource = repo.get_resource("scores/pos1")
    assert f'href="{resource.get_url()}/index.html"' in html


def test_a_public_url_ending_in_a_slash_does_not_double_it(
    tmp_path: pathlib.Path,
) -> None:
    # A deployment writes ``public_url`` by hand, so both spellings turn
    # up; neither may put a "//" in the middle of a link or an image.
    a_score_at(tmp_path / "grr")
    repo = a_repo_over(tmp_path / "grr", "http://grr.example.org/")

    html = render(repo, tmp_path / "work")

    assert "http://grr.example.org//" not in html
    assert 'href="http://grr.example.org/scores/pos1/index.html"' in html
    assert (
        'src="http://grr.example.org'
        '/scores/pos1/statistics/histogram_score.png"'
    ) in html


def test_the_pipeline_path_is_shown_when_the_caller_names_a_file(
    tmp_path: pathlib.Path,
) -> None:
    a_score_at(tmp_path / "grr")
    repo = a_repo_over(tmp_path / "grr", "http://grr.example.org")

    html = render(
        repo, tmp_path / "work", pipeline_path="/configs/annotation.yaml")

    assert "Pipeline path: /configs/annotation.yaml" in html


def test_no_pipeline_path_block_when_the_caller_names_no_file(
    tmp_path: pathlib.Path,
) -> None:
    # Two of the three callers have no file to name. They leave the
    # argument at its default rather than passing a placeholder, and the
    # page must then carry no pipeline-path block at all -- the same page
    # a caller that omitted the argument entirely used to produce.
    a_score_at(tmp_path / "grr")
    repo = a_repo_over(tmp_path / "grr", "http://grr.example.org")

    html = render(repo, tmp_path / "work")

    assert "Pipeline path:" not in html
