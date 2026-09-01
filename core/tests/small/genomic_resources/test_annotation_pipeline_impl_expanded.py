# pylint: disable=W0621,C0114,C0116,W0212,W0613,C0415
"""Expanded tests for annotation_pipeline_impl module."""
import logging
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


@pytest.fixture
def grr_fixture(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    """Create test repository with score and pipeline resources."""
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
            "data.txt": "chrom\tpos_begin\tscore\n1\t100\t0.5\n",
        },
        "two": {
            "genomic_resource.yaml": """
                type: position_score
                table:
                    filename: data.txt
                scores:
                - id: score2
                  type: float
                  desc: Another score
                  name: s2
            """,
            "data.txt": "chrom\tpos_begin\tscore2\n1\t100\t0.8\n",
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
        "multi_pipeline": {
            "genomic_resource.yaml": """
                type: annotation_pipeline
                filename: annotation.yaml
            """,
            "annotation.yaml": """
                - position_score: one
                - position_score: two
            """,
        },
        "nested/deep/pipeline": {
            "genomic_resource.yaml": """
                type: annotation_pipeline
                filename: config.yaml
            """,
            "config.yaml": """
                - position_score: one
            """,
        },
    })
    return build_filesystem_test_repository(root_path)


@pytest.fixture
def wrong_type_resource(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    """Create repository with wrong resource type."""
    root_path = tmp_path / "wrong"
    setup_directories(root_path, {
        "genome": {
            "genomic_resource.yaml": """
                type: genome
                filename: genome.fa
            """,
            "genome.fa": ">chr1\nACGT\n",
        },
    })
    return build_filesystem_test_repository(root_path)


# Tests for __init__


def test_init_success(grr_fixture: GenomicResourceRepo) -> None:
    """Test successful initialization."""
    impl = AnnotationPipelineImplementation(
        grr_fixture.get_resource("pipeline"),
    )
    assert impl is not None
    assert impl.pipeline is None  # Not loaded until get_info called
    assert impl.raw is not None


def test_init_wrong_resource_type(
    wrong_type_resource: GenomicResourceRepo,
) -> None:
    """Test initialization fails with wrong resource type."""
    with pytest.raises(ValueError, match="wrong resource type"):
        AnnotationPipelineImplementation(
            wrong_type_resource.get_resource("genome"),
        )


def test_init_loads_raw_config(grr_fixture: GenomicResourceRepo) -> None:
    """Test that raw config is loaded during init."""
    impl = AnnotationPipelineImplementation(
        grr_fixture.get_resource("pipeline"),
    )
    assert "position_score: one" in impl.raw


# Tests for get_info


def test_get_info_basic(grr_fixture: GenomicResourceRepo) -> None:
    """Test basic get_info functionality."""
    impl = AnnotationPipelineImplementation(
        grr_fixture.get_resource("pipeline"),
    )
    info = impl.get_info(repo=grr_fixture)
    assert info
    assert isinstance(info, str)


def test_get_info_contains_annotator_info(
    grr_fixture: GenomicResourceRepo,
) -> None:
    """Test that info contains annotator information."""
    impl = AnnotationPipelineImplementation(
        grr_fixture.get_resource("pipeline"),
    )
    info = impl.get_info(repo=grr_fixture)
    assert "position_score" in info
    assert "one" in info


def test_get_info_contains_score_description(
    grr_fixture: GenomicResourceRepo,
) -> None:
    """Test that info contains score descriptions."""
    impl = AnnotationPipelineImplementation(
        grr_fixture.get_resource("pipeline"),
    )
    info = impl.get_info(repo=grr_fixture)
    assert "A score description testtest" in info


def test_get_info_loads_pipeline(grr_fixture: GenomicResourceRepo) -> None:
    """Test that get_info loads the pipeline."""
    impl = AnnotationPipelineImplementation(
        grr_fixture.get_resource("pipeline"),
    )
    assert impl.pipeline is None
    impl.get_info(repo=grr_fixture)
    assert impl.pipeline is not None


@pytest.mark.parametrize("render", ["get_info", "get_statistics_info"])
def test_rendering_a_page_does_not_take_the_deprecated_work_dir(
    grr_fixture: GenomicResourceRepo,
    caplog: pytest.LogCaptureFixture,
    render: str,
) -> None:
    """Both pages name their own work dir instead of inheriting the fallback.

    `grr_manage` renders both for every resource, so a pipeline resource that
    leaves `work_dir` to `build_annotation_pipeline` puts two deprecation
    warnings in every repo-repair, repo-stats and repo-info run -- ten of
    them on the deployed GRR (#507).
    """
    impl = AnnotationPipelineImplementation(
        grr_fixture.get_resource("pipeline"),
    )

    with caplog.at_level(
            logging.WARNING, logger="gain.annotation.annotation_factory"):
        getattr(impl, render)(repo=grr_fixture)

    assert [
        record.getMessage() for record in caplog.records
        if "work_dir" in record.getMessage()
    ] == []


@pytest.mark.parametrize("render", ["get_info", "get_statistics_info"])
def test_rendering_a_page_does_not_open_the_pipeline(
    grr_fixture: GenomicResourceRepo,
    render: str,
) -> None:
    """The precondition that lets the work dir be scoped to the call.

    `AnnotatorBase` creates its work dir in `open()` (`annotator_base.py`),
    and describing a pipeline never gets there -- which is why a directory
    that does not outlive the render is a legitimate thing to hand it.  Were
    a render to start opening its annotators, they would come up pointing at
    a directory that has already been removed, so this is pinned rather than
    assumed.
    """
    impl = AnnotationPipelineImplementation(
        grr_fixture.get_resource("pipeline"),
    )

    getattr(impl, render)(repo=grr_fixture)

    assert impl.pipeline is not None
    assert [
        annotator for annotator in impl.pipeline.annotators
        if annotator.is_open()
    ] == []


def test_get_info_multiple_annotators(
    grr_fixture: GenomicResourceRepo,
) -> None:
    """Test get_info with multiple annotators."""
    impl = AnnotationPipelineImplementation(
        grr_fixture.get_resource("multi_pipeline"),
    )
    info = impl.get_info(repo=grr_fixture)
    assert "one" in info
    assert "two" in info


# Tests for get_statistics_info


def test_get_statistics_info_basic(
    grr_fixture: GenomicResourceRepo,
) -> None:
    """Test basic get_statistics_info functionality."""
    impl = AnnotationPipelineImplementation(
        grr_fixture.get_resource("pipeline"),
    )
    stats_info = impl.get_statistics_info(repo=grr_fixture)
    assert stats_info
    assert isinstance(stats_info, str)


def test_get_statistics_info_loads_pipeline(
    grr_fixture: GenomicResourceRepo,
) -> None:
    """Test that get_statistics_info loads the pipeline."""
    impl = AnnotationPipelineImplementation(
        grr_fixture.get_resource("pipeline"),
    )
    assert impl.pipeline is None
    impl.get_statistics_info(repo=grr_fixture)
    assert impl.pipeline is not None


# Tests for get_template


def test_get_template_structure(grr_fixture: GenomicResourceRepo) -> None:
    from gain.templates import get_jinja_env
    impl = AnnotationPipelineImplementation(
        grr_fixture.get_resource("pipeline"),
    )
    tmpl = get_jinja_env().get_template(impl.template_name)
    assert tmpl is not None


# The address policy this implementation hands the renderer is no longer
# its own (#970): it lives in ``gain.annotation.pipeline_doc`` as
# ``RepositoryRelativeAddresses``, and is exercised in
# ``tests/small/annotation/test_pipeline_doc_addresses`` -- relative and
# external addresses, both warnings, the missing-image guard, the
# percent-quoted file name and the per-level prefix.  What stays here is
# that the *page* asks for that policy, in
# ``test_the_rendered_page_carries_the_relative_addresses``.


# Tests for _get_template_data


def test_get_template_data_raises_without_pipeline(
    grr_fixture: GenomicResourceRepo,
) -> None:
    """Test _get_template_data raises ValueError without loaded pipeline."""
    impl = AnnotationPipelineImplementation(
        grr_fixture.get_resource("pipeline"),
    )
    # The actual error has no message, so we verify it's a ValueError
    # by catching any ValueError type
    with pytest.raises(ValueError):
        impl._get_template_data()


def test_get_template_data_with_loaded_pipeline(
    grr_fixture: GenomicResourceRepo,
) -> None:
    """Test _get_template_data returns data when pipeline is loaded."""
    impl = AnnotationPipelineImplementation(
        grr_fixture.get_resource("pipeline"),
    )
    impl.get_info(repo=grr_fixture)  # This loads the pipeline
    data = impl._get_template_data()
    assert "content" in data
    assert isinstance(data["content"], str)


def test_get_template_data_content_structure(
    grr_fixture: GenomicResourceRepo,
) -> None:
    """Test that template data content has expected information."""
    impl = AnnotationPipelineImplementation(
        grr_fixture.get_resource("pipeline"),
    )
    impl.get_info(repo=grr_fixture)
    data = impl._get_template_data()
    content = data["content"]
    # The content should contain information about the pipeline
    assert len(content) > 0


# Tests for files property


def test_files_property(grr_fixture: GenomicResourceRepo) -> None:
    """Test files property returns config filename."""
    impl = AnnotationPipelineImplementation(
        grr_fixture.get_resource("pipeline"),
    )
    files = impl.files
    assert files == {"annotation.yaml"}


def test_files_property_different_filename(
    grr_fixture: GenomicResourceRepo,
) -> None:
    """Test files property with different config filename."""
    impl = AnnotationPipelineImplementation(
        grr_fixture.get_resource("nested/deep/pipeline"),
    )
    files = impl.files
    assert files == {"config.yaml"}


# Tests for calc_statistics_hash


def test_calc_statistics_hash(grr_fixture: GenomicResourceRepo) -> None:
    """Test calc_statistics_hash returns bytes."""
    impl = AnnotationPipelineImplementation(
        grr_fixture.get_resource("pipeline"),
    )
    hash_val = impl.calc_statistics_hash()
    assert isinstance(hash_val, bytes)
    assert hash_val == b"placeholder"


# Tests for calc_info_hash


def test_calc_info_hash(grr_fixture: GenomicResourceRepo) -> None:
    """Test calc_info_hash returns bytes."""
    impl = AnnotationPipelineImplementation(
        grr_fixture.get_resource("pipeline"),
    )
    hash_val = impl.calc_info_hash()
    assert isinstance(hash_val, bytes)
    assert hash_val == b"placeholder"


# Tests for add_statistics_build_tasks


def test_add_statistics_build_tasks_returns_empty_list(
    grr_fixture: GenomicResourceRepo,
) -> None:
    """Test add_statistics_build_tasks returns empty list."""
    impl = AnnotationPipelineImplementation(
        grr_fixture.get_resource("pipeline"),
    )
    tasks = impl.create_statistics_build_tasks()
    assert not tasks


def test_add_statistics_build_tasks_with_kwargs(
    grr_fixture: GenomicResourceRepo,
) -> None:
    """Test add_statistics_build_tasks ignores kwargs."""
    impl = AnnotationPipelineImplementation(
        grr_fixture.get_resource("pipeline"),
    )
    tasks = impl.create_statistics_build_tasks(
        some_arg="value",
        another_arg=123,
    )
    assert not tasks


# Integration tests


def test_full_workflow(grr_fixture: GenomicResourceRepo) -> None:
    """Test full workflow from init to getting info."""
    # Create implementation
    impl = AnnotationPipelineImplementation(
        grr_fixture.get_resource("pipeline"),
    )

    # Verify initial state
    assert impl.pipeline is None
    assert impl.raw is not None

    # Get info
    info = impl.get_info(repo=grr_fixture)

    # Verify pipeline loaded
    assert impl.pipeline is not None
    assert info is not None
    assert "position_score" in info

    # Get template data
    data = impl._get_template_data()
    assert "content" in data


def test_multiple_calls_to_get_info(
    grr_fixture: GenomicResourceRepo,
) -> None:
    """Test that multiple calls to get_info work correctly."""
    impl = AnnotationPipelineImplementation(
        grr_fixture.get_resource("pipeline"),
    )

    info1 = impl.get_info(repo=grr_fixture)
    info2 = impl.get_info(repo=grr_fixture)

    # Should return consistent results
    assert info1 == info2
    assert impl.pipeline is not None


@pytest.fixture
def preamble_grr_fixture(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    """A repo holding pipelines that carry a preamble.

    ``grr_fixture``'s pipelines are bare annotator lists, so the whole
    preamble block of the page template is skipped for them and nothing
    there exercises the reference-genome row.
    """
    root_path = tmp_path / "preamble_grr"
    setup_directories(root_path, {
        "one": {
            "genomic_resource.yaml": """
                type: position_score
                table:
                    filename: data.txt
                scores:
                - id: score
                  type: float
                  desc: A score description
                  name: s1
            """,
            "data.txt": "chrom\tpos_begin\tscore\n1\t100\t0.5\n",
        },
        "acgt": {
            "genomic_resource.yaml": """
                type: reference_genome
                filename: genome.fa
            """,
            "genome.fa": "blabla",
        },
        "with_genome": {
            "genomic_resource.yaml": """
                type: annotation_pipeline
                filename: annotation.yaml
            """,
            "annotation.yaml": """
                preamble:
                    summary: a summary
                    description: a description
                    input_reference_genome: acgt
                annotators:
                - position_score: one
            """,
        },
        "without_genome": {
            "genomic_resource.yaml": """
                type: annotation_pipeline
                filename: annotation.yaml
            """,
            "annotation.yaml": """
                preamble:
                    summary: a summary
                    description: a description
                annotators:
                - position_score: one
            """,
        },
    })
    return build_filesystem_test_repository(root_path)


def test_get_info_renders_the_reference_genome_row(
    preamble_grr_fixture: GenomicResourceRepo,
) -> None:
    """The positive counterpart to the genome-less case below.

    Without this, the absence assertion in its sibling could pass simply
    because the row label had been renamed.
    """
    impl = AnnotationPipelineImplementation(
        preamble_grr_fixture.get_resource("with_genome"),
    )

    info = impl.get_info(repo=preamble_grr_fixture)

    assert "<th>Input reference genome</th>" in info
    assert "../acgt/index.html" in info


def test_get_info_without_a_reference_genome_omits_the_row(
    preamble_grr_fixture: GenomicResourceRepo,
) -> None:
    """``grr_manage repo-info`` renders every pipeline resource this way.

    ``input_reference_genome`` is optional, so ``input_reference_genome_res``
    is legally ``None``.  The row used to be guarded on the result of the
    address callable, which dereferences its argument unconditionally, so
    one genome-less pipeline resource failed the whole repo-wide run:
    ``AttributeError: 'NoneType' object has no attribute 'get_url'``, then
    ``failed resources in GRR <...>`` and a non-zero exit (#1021).
    """
    impl = AnnotationPipelineImplementation(
        preamble_grr_fixture.get_resource("without_genome"),
    )

    info = impl.get_info(repo=preamble_grr_fixture)

    assert "<th>Input reference genome</th>" not in info
    # the rest of the page is whole, not truncated at the missing row
    assert "a summary" in info
    assert "a description" in info
    assert "position_score" in info
