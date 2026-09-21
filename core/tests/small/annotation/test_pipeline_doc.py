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
from collections.abc import Callable
from typing import Any

import pytest
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.annotation.pipeline_doc import (
    PUBLIC_MIRROR_ADDRESSES,
    PipelineDocAddresses,
    RepositoryRelativeAddresses,
    render_pipeline_doc,
)
from gain.genomic_resources.histogram import CategoricalHistogram
from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceRepo,
)
from gain.genomic_resources.score_resource import ScoreResource
from gain.genomic_resources.testing.builders import (
    PositionScoreBuilder,
    a_grr,
    a_position_score,
)
from gain.genomic_resources.testing.statistics import build_statistics

PIPELINE = "- position_score: scores/pos1\n"

PUBLIC_URL = "http://grr.example.org"


class RelativeStub:
    """An address policy that is *not* the public mirror.

    A stand-in rather than the real ``RepositoryRelativeAddresses``: what
    these tests pin is that the renderer asks the injected policy at all,
    not how that policy builds its addresses.
    """

    def resource_url(self, resource: GenomicResource) -> str:
        return f"../{resource.resource_id}"

    def histogram_url(
        self, score: ScoreResource, score_id: str,
    ) -> str | None:
        return f"../{score.resource.resource_id}/{score_id}.png"


@pytest.fixture
def public_repo(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    """A GRR with one score, advertising a public mirror of its own.

    The score ships its histogram image, as a built resource does: an
    image is addressed only when the manifest lists it (gain#1533), and
    these tests are about WHERE it is addressed, not whether.
    """
    return (
        a_grr()
        .with_resource(
            "scores/pos1",
            a_position_score()
            .with_file("statistics/histogram_score.png", "drawn"))
        .with_public_url(PUBLIC_URL)
        .build_repo(tmp_path / "grr")
    )


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
        addresses=RelativeStub(),
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


@pytest.fixture
def annulled_repo(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    """A GRR whose one score has its histogram annulled by definition."""
    return (
        a_grr()
        .with_resource(
            "scores/pos1",
            a_position_score()
            .with_score("score", "float")
            .with_histogram({"type": "null", "reason": "annulled"}))
        .with_public_url(PUBLIC_URL)
        .build_repo(tmp_path / "grr")
    )


class NoHistogramStub(RelativeStub):
    """A policy that has no histogram address for any score."""

    def histogram_url(
        self, score: ScoreResource, score_id: str,
    ) -> str | None:
        return None


def test_an_annulled_histogram_renders_no_image(
    annulled_repo: GenomicResourceRepo, tmp_path: pathlib.Path,
) -> None:
    # No PNG is ever written for an annulled histogram, so a page that
    # addressed one would dangle (gain#1025).
    html = render(annulled_repo, tmp_path / "work")

    assert "<img" not in html


def a_score_with_no_values() -> PositionScoreBuilder:
    """A configured number histogram the build nullifies in its min/max pass.

    Every value is NA, so no range is found and nothing is drawn.
    """
    return (
        a_position_score()
        .with_score("score", "float")
        .with_histogram({"type": "number", "number_of_bins": 10})
        .with_na_values("NA")
        .with_data("""
            chrom  pos_begin  score
            1      10         NA
            1      20         NA
        """)
    )


def a_score_with_too_many_categories() -> PositionScoreBuilder:
    """A default categorical histogram the build nullifies accumulating.

    No ``histogram:`` block, so the ``str`` score gets the default
    categorical config, which enforces the unique-values limit -- and the
    values exceed it.  A null histogram file IS written for this one;
    still, nothing is drawn.
    """
    rows = "\n".join(
        f"1  {10 * (i + 1)}  label{i}"
        for i in range(CategoricalHistogram.UNIQUE_VALUES_LIMIT + 1))
    return (
        a_position_score()
        .with_score("score", "str")
        .with_data("chrom  pos_begin  score\n" + rows)
    )


@pytest.fixture(
    params=[a_score_with_no_values, a_score_with_too_many_categories])
def build_nullified_repo(
    request: pytest.FixtureRequest, tmp_path: pathlib.Path,
) -> GenomicResourceRepo:
    """A GRR whose one score's histogram is nullified by its own build.

    Configured, not annulled, in either of the two ways a build nullifies
    a histogram; no image is drawn and the manifest lists none
    (gain#1533).
    """
    repo = (
        a_grr()
        .with_resource("scores/pos1", request.param())
        .with_public_url(PUBLIC_URL)
        .build_repo(tmp_path / "grr")
    )
    resource = repo.get_resource("scores/pos1")
    build_statistics(resource)
    # The premise, not the subject: the build drew nothing for it.
    assert not resource.file_exists("statistics/histogram_score.png")
    return repo


def public_mirror(_repo: GenomicResourceRepo) -> PipelineDocAddresses:
    return PUBLIC_MIRROR_ADDRESSES


def repository_relative(repo: GenomicResourceRepo) -> PipelineDocAddresses:
    # Built for the page's own resource; for a page that only names the
    # score, any resource of the same GRR stands in for it.
    return RepositoryRelativeAddresses(repo.get_resource("scores/pos1"))


@pytest.mark.parametrize("policy", [public_mirror, repository_relative])
def test_a_histogram_nullified_by_its_build_renders_no_image(
    build_nullified_repo: GenomicResourceRepo,
    tmp_path: pathlib.Path,
    policy: Callable[[GenomicResourceRepo], PipelineDocAddresses],
) -> None:
    html = render(
        build_nullified_repo, tmp_path / "work",
        addresses=policy(build_nullified_repo))

    assert "<img" not in html
    assert "None" not in html
    # The attribute itself is still documented.
    assert "source: score" in html


def test_a_policy_with_no_histogram_address_renders_no_image(
    public_repo: GenomicResourceRepo, tmp_path: pathlib.Path,
) -> None:
    # The renderer's half of the contract: whether an image has an
    # address is the policy's answer, and "no address" is not a string.
    html = render(
        public_repo, tmp_path / "work", addresses=NoHistogramStub())

    assert "<img" not in html
    assert "None" not in html
