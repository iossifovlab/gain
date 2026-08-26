# pylint: disable=C0116
"""Links and images in the downloadable pipeline documentation.

The document is downloaded and opened in the user's own browser, so every
address it carries has to be one that browser can resolve. Production's GRR
is a *group* of directory repositories mounted read-only into the container,
so the repository's own url is a container-local path -- an address that
resolves nowhere once the document leaves the server. What the deployment
publishes its GRR as is the child repository's ``public_url``, and only the
resource knows which child it came from (#841, and #838 for the same
correction at the single-allele call site).
"""
import pathlib
import tempfile

from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)
from gain.genomic_resources.testing.builders import a_position_score

from web_annotation.pipeline_cache import ThreadSafePipeline
from web_annotation.pipelines.views import PipelineDoc

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


def render_doc(
    repo: GenomicResourceRepo, pipeline_config: str = PIPELINE,
) -> str:
    """Render the downloadable document for ``pipeline_config``.

    Wrapped the way the view receives it -- the doc endpoint renders
    whatever the pipeline cache hands it, which is always a
    ``ThreadSafePipeline``.
    """
    with tempfile.TemporaryDirectory() as work_dir:
        pipeline = load_pipeline_from_yaml(
            pipeline_config, repo, work_dir=pathlib.Path(work_dir))
        return PipelineDoc._render_doc(
            ThreadSafePipeline(pipeline, "test-pipeline"))


def a_score_at(
    resources_dir: pathlib.Path, resource_id: str = "scores/pos1",
) -> None:
    """Realize one position score the pipeline can annotate with."""
    a_position_score().realize_into(resources_dir / resource_id)


def test_resource_is_linked_on_the_grrs_public_url(
    tmp_path: pathlib.Path,
) -> None:
    a_score_at(tmp_path)
    repo = build_genomic_resource_repository({
        "id": "main",
        "type": "dir",
        "directory": str(tmp_path),
        "public_url": "http://grr.example.org",
    })

    html = render_doc(repo)

    assert 'href="http://grr.example.org/scores/pos1/index.html"' in html


def test_histogram_image_is_sourced_from_the_grrs_public_url(
    tmp_path: pathlib.Path,
) -> None:
    a_score_at(tmp_path)
    repo = build_genomic_resource_repository({
        "id": "main",
        "type": "dir",
        "directory": str(tmp_path),
        "public_url": "http://grr.example.org",
    })

    html = render_doc(repo)

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
    html = render_doc(repo, TWO_CHILD_PIPELINE)

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
    a_score_at(tmp_path)
    repo = build_genomic_resource_repository({
        "id": "main",
        "type": "dir",
        "directory": str(tmp_path),
    })

    html = render_doc(repo)

    resource = repo.get_resource("scores/pos1")
    assert resource.get_public_url() == resource.get_url()
    assert f'href="{resource.get_url()}/index.html"' in html


def test_a_public_url_ending_in_a_slash_does_not_double_it(
    tmp_path: pathlib.Path,
) -> None:
    # A deployment writes ``public_url`` by hand, so both spellings turn
    # up; neither may put a "//" in the middle of a link or an image.
    a_score_at(tmp_path)
    repo = build_genomic_resource_repository({
        "id": "main",
        "type": "dir",
        "directory": str(tmp_path),
        "public_url": "http://grr.example.org/",
    })

    html = render_doc(repo)

    assert "http://grr.example.org//" not in html
    assert 'href="http://grr.example.org/scores/pos1/index.html"' in html
    assert (
        'src="http://grr.example.org'
        '/scores/pos1/statistics/histogram_score.png"'
    ) in html
