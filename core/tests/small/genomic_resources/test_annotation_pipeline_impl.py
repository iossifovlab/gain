# pylint: disable=W0621,C0114,C0116,W0613
import pathlib

import pytest
from gain.genomic_resources.implementations.annotation_pipeline_impl import (
    AnnotationPipelineImplementation,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing import (
    build_filesystem_test_repository,
    setup_directories,
)
from gain.genomic_resources.testing.builders import a_position_score
from gain.genomic_resources.testing.statistics import publish_statistics


@pytest.fixture
def grr_fixture(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    root_path = tmp_path / "grr"
    setup_directories(root_path, {
        "one": {
            "genomic_resource.yaml": """
                type: position_score
                table:
                    filename: data.txt
                scores:
                - id: score
                  type: float
                  desc: |
                      A score description testtest
                  name: s1
            """,
            # The image too, as a built resource would have it.
            "statistics/histogram_score.png": "drawn",
        },
        "pipeline": {
            "genomic_resource.yaml": """
                type: annotation_pipeline
                filename: annotation.yaml
            """,
            "annotation.yaml": """
                - position_score: one
            """,
        },
    })
    return build_filesystem_test_repository(root_path)


def test_pipeline_impl_init(grr_fixture: GenomicResourceRepo) -> None:
    assert AnnotationPipelineImplementation(
        grr_fixture.get_resource("pipeline"))


def test_pipeline_impl_info(grr_fixture: GenomicResourceRepo) -> None:
    impl = AnnotationPipelineImplementation(
        grr_fixture.get_resource("pipeline"))
    info = impl.get_info(repo=grr_fixture)
    assert info
    assert "position_score" in info
    assert "one" in info
    assert "A score description testtest" in info


def test_the_rendered_page_carries_the_relative_addresses(
    grr_fixture: GenomicResourceRepo,
) -> None:
    """The page must *use* this implementation's own address policy.

    ``tests/small/annotation/test_pipeline_doc_addresses`` exercises the
    policy directly, so it stays green even if the page stops asking for
    it -- and since #952 the renderer is shared, and its default is the
    public mirror.  Dropping the ``addresses`` argument would therefore
    silently republish every static page under ``grr-*.iossifovlab.com``
    with addresses that resolve nowhere from inside the GRR tree.  This
    is the test that renders and looks.
    """
    impl = AnnotationPipelineImplementation(
        grr_fixture.get_resource("pipeline"))

    page = impl.get_info(repo=grr_fixture)

    # The image is addressed by the score's *id*, which is what the
    # template hands the policy; the policy's own tests pass the column
    # name "s1" by hand and so never exercise that.
    assert 'href="../one/index.html"' in page
    assert 'src="../one/statistics/histogram_score.png"' in page


@pytest.fixture
def built_grr(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    """A pipeline over a score whose statistics were REALLY built.

    ``drawn`` has values and gets its histogram drawn; ``empty`` has a
    configured number histogram and no values, so the build nullifies it
    and draws nothing (gain#1533).  The build runs before the page is
    rendered, as repo-repair orders it, so the score's manifest already
    lists what was drawn.
    """
    root_path = tmp_path / "grr"
    setup_directories(root_path, {
        "pipeline": {
            "genomic_resource.yaml": """
                type: annotation_pipeline
                filename: annotation.yaml
            """,
            "annotation.yaml": """
                - position_score: one
            """,
        },
    })
    (
        a_position_score()
        .with_score("drawn", "float")
        .with_score("empty", "float")
        .with_histogram({"type": "number", "number_of_bins": 10})
        .with_na_values("NA")
        .with_data("""
            chrom  pos_begin  drawn  empty
            1      10         0.1    NA
            1      20         0.2    NA
        """)
        .realize_into(root_path / "one")
    )
    repo = build_filesystem_test_repository(root_path)
    score = repo.get_resource("one")
    publish_statistics(score)
    # The premise, not the subject: one drawn, one not.
    assert score.file_exists("statistics/histogram_drawn.png")
    assert not score.file_exists("statistics/histogram_empty.png")
    return repo


def test_the_published_page_addresses_only_the_histograms_the_build_drew(
    built_grr: GenomicResourceRepo,
) -> None:
    impl = AnnotationPipelineImplementation(built_grr.get_resource("pipeline"))

    page = impl.get_info(repo=built_grr)

    assert 'src="../one/statistics/histogram_drawn.png"' in page
    assert "histogram_empty" not in page
    assert "None" not in page
    # Both attributes are still documented.
    assert "source: drawn" in page
    assert "source: empty" in page
