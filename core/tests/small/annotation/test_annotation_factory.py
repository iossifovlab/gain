# pylint: disable=W0621,C0114,C0116,W0212,W0613
import inspect
import pathlib
from collections.abc import Iterator

import pytest
from gain.annotation import annotation_factory
from gain.annotation.annotation_config import AnnotationConfigurationError
from gain.annotation.annotation_factory import (
    build_annotation_pipeline,
    load_pipeline_from_file,
    load_pipeline_from_file_or_resource,
)
from gain.genomic_resources.repository import GenomicResourceRepo

_FACTORY_LOGGER = "gain.annotation.annotation_factory"

#: Every keyword argument a pipeline builder threads down to its annotators.
_FORWARDED_KEYWORDS = ("work_dir", "allow_repeated_attributes")


def _pipeline_builder_names() -> list[str]:
    """Name every public entry point that turns a config into a pipeline."""
    return sorted(
        name
        for name, obj in vars(annotation_factory).items()
        if not name.startswith("_")
        and inspect.isfunction(obj)
        and obj.__module__ == annotation_factory.__name__
        and (name.startswith("load_pipeline_from")
             or name == "build_annotation_pipeline")
    )


@pytest.fixture
def reset_default_work_dir() -> Iterator[None]:
    """Isolate the memoized per-process default work dir between tests."""
    annotation_factory._DEFAULT_WORK_DIR = None
    yield
    annotation_factory._DEFAULT_WORK_DIR = None


def test_build_pipeline_default_work_dir_is_absolute(
    annotation_grr: GenomicResourceRepo,
    reset_default_work_dir: None,
) -> None:
    pipeline = build_annotation_pipeline(
        [{"position_score": "one"}], annotation_grr)

    annotator = pipeline.annotators[0]
    assert annotator.work_dir.is_absolute()
    assert not str(annotator.work_dir).startswith("work")


def test_build_pipeline_warns_once_for_multiple_annotators(
    annotation_grr: GenomicResourceRepo,
    reset_default_work_dir: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = [
        {"position_score": {
            "resource_id": "one",
            "attributes": [{"source": "score", "name": "score_1"}]}},
        {"position_score": {
            "resource_id": "four",
            "attributes": [{"source": "score", "name": "score_4"}]}},
    ]
    with caplog.at_level("WARNING", logger=_FACTORY_LOGGER):
        pipeline = build_annotation_pipeline(config, annotation_grr)

    assert len(pipeline.annotators) == 2
    default_work_dir_warnings = [
        rec for rec in caplog.records
        if rec.name == _FACTORY_LOGGER
        and "no `work_dir` passed" in rec.message
    ]
    assert len(default_work_dir_warnings) == 1

    # both annotators live under the same memoized temp root
    root = annotation_factory._get_default_work_dir()
    for annotator in pipeline.annotators:
        assert root in annotator.work_dir.parents


def test_build_pipeline_explicit_work_dir_used_verbatim_no_warning(
    annotation_grr: GenomicResourceRepo,
    reset_default_work_dir: None,
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    explicit = tmp_path / "my_work"
    with caplog.at_level("WARNING", logger=_FACTORY_LOGGER):
        pipeline = build_annotation_pipeline(
            [{"position_score": "one"}], annotation_grr, work_dir=explicit)

    annotator = pipeline.annotators[0]
    assert annotator.work_dir == explicit / "A0_position_score"

    default_work_dir_warnings = [
        rec for rec in caplog.records
        if rec.name == _FACTORY_LOGGER
        and "no `work_dir` passed" in rec.message
    ]
    assert default_work_dir_warnings == []
    # the memo is never minted when an explicit work_dir is supplied
    assert annotation_factory._DEFAULT_WORK_DIR is None


@pytest.mark.parametrize(
        "pipeline_ext", [".yaml", ".yml"],
)
def test_load_pipeline_from_file(
    annotate_directory_fixture: pathlib.Path,
    annotation_grr: GenomicResourceRepo,
    tmp_path: pathlib.Path,
    pipeline_ext: str,
) -> None:
    pipeline_filename = tmp_path / f"pipeline{pipeline_ext}"
    pipeline_filename.write_text(
        """
        - position_score:
            resource_id: one
        """)
    pipeline = load_pipeline_from_file(
        str(pipeline_filename), annotation_grr)
    assert len(pipeline.annotators) == 1


def test_load_pipeline_from_file_or_resource_file_branch(
    annotation_grr: GenomicResourceRepo,
    tmp_path: pathlib.Path,
) -> None:
    pipeline_filename = tmp_path / "pipeline.yaml"
    pipeline_filename.write_text(
        """
        - position_score:
            resource_id: one
        """)
    pipeline = load_pipeline_from_file_or_resource(
        str(pipeline_filename), annotation_grr)
    assert len(pipeline.annotators) == 1


def test_load_pipeline_from_file_or_resource_grr_id_branch(
    annotation_grr: GenomicResourceRepo,
) -> None:
    pipeline = load_pipeline_from_file_or_resource(
        "res_pipeline", annotation_grr)
    assert len(pipeline.annotators) == 1


@pytest.mark.parametrize("builder_name", _pipeline_builder_names())
@pytest.mark.parametrize("keyword", _FORWARDED_KEYWORDS)
def test_pipeline_builders_all_accept_the_forwarded_keywords(
    builder_name: str,
    keyword: str,
) -> None:
    """No way into a pipeline may quietly drop what its siblings forward.

    A builder that omits one of these does not fail loudly -- it silently
    falls back to the deprecated default work dir, or silently loses the
    caller's repeated-attribute handling.  Whoever adds the next builder gets
    told here rather than by a stray warning in a repo-wide run.
    """
    builder = getattr(annotation_factory, builder_name)
    parameters = inspect.signature(builder).parameters

    assert keyword in parameters, (
        f"{builder_name} does not accept `{keyword}`, so a caller cannot "
        f"pass one and it cannot be forwarded on"
    )


def test_load_pipeline_from_file_or_resource_grr_id_branch_work_dir(
    annotation_grr: GenomicResourceRepo,
    reset_default_work_dir: None,
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An explicit work dir survives the resource branch, not just the file one.

    The resource branch reads the pipeline out of the GRR rather than off the
    disk, and used to be the branch where a caller's work dir could go missing.
    """
    explicit = tmp_path / "resource_work"
    with caplog.at_level("WARNING", logger=_FACTORY_LOGGER):
        pipeline = load_pipeline_from_file_or_resource(
            "res_pipeline", annotation_grr, work_dir=explicit)

    annotator = pipeline.annotators[0]
    assert annotator.work_dir == explicit / "A0_position_score"

    default_work_dir_warnings = [
        rec for rec in caplog.records
        if rec.name == _FACTORY_LOGGER
        and "no `work_dir` passed" in rec.message
    ]
    assert default_work_dir_warnings == []
    assert annotation_factory._DEFAULT_WORK_DIR is None


def test_load_pipeline_from_file_or_resource_grr_id_branch_repeated(
    annotation_grr: GenomicResourceRepo,
    tmp_path: pathlib.Path,
) -> None:
    """`allow_repeated_attributes` survives the resource branch too.

    Loading the same pipeline resource is an error or a rename depending
    purely on the flag, which is only true if the branch forwards it.
    """
    with pytest.raises(AnnotationConfigurationError, match="Repeated"):
        load_pipeline_from_file_or_resource(
            "res_pipeline_repeated", annotation_grr, work_dir=tmp_path)

    pipeline = load_pipeline_from_file_or_resource(
        "res_pipeline_repeated", annotation_grr,
        allow_repeated_attributes=True, work_dir=tmp_path)

    names = [attr.name for attr in pipeline.get_attributes()]
    assert len(pipeline.annotators) == 2
    # the clash is resolved by qualifying each name, not by dropping one
    assert len(names) == len(set(names))
    assert "score" not in names


def test_load_pipeline_from_file_or_resource_missing(
    annotation_grr: GenomicResourceRepo,
) -> None:
    with pytest.raises(ValueError, match="neither a valid file path"):
        load_pipeline_from_file_or_resource(
            "definitely_not_a_path_or_resource", annotation_grr)


def test_load_pipeline_from_file_or_resource_wrong_type(
    annotation_grr: GenomicResourceRepo,
) -> None:
    with pytest.raises(TypeError, match="annotation_pipeline"):
        load_pipeline_from_file_or_resource("one", annotation_grr)
