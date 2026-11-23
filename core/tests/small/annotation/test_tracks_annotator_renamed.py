# pylint: disable=redefined-outer-name,C0116
"""The refusal of the old annotator type ``binning_tracks_annotator``.

A pipeline config that names the old type is refused at the two seams
that refuse a retired annotator name: the annotator factory and the
wildcard expansion of the config parser.  Each refusal names the old
type and the new type, ``tracks_annotator``.
"""
import pathlib

import pytest
from gain.annotation.annotation_config import (
    AnnotationConfigParser,
    AnnotationConfigurationError,
    AnnotatorInfo,
)
from gain.annotation.annotation_factory import build_pipeline_annotator
from gain.annotation.annotation_pipeline import AnnotationPipeline
from gain.genomic_resources.repository import GenomicResourceProtocolRepo
from gain.genomic_resources.resource_types import (
    RETIRED_VOCABULARY_REMOVAL_RELEASE,
)
from gain.genomic_resources.testing import build_inmemory_test_repository

#: The whole text of the refusal.  Spelled out, so that a change of the
#: message fails here.
RENAME_MESSAGE = (
    "annotator 'binning_tracks_annotator' was renamed; "
    "write 'tracks_annotator' instead")


@pytest.fixture
def grr() -> GenomicResourceProtocolRepo:
    return build_inmemory_test_repository({})


def test_the_annotator_factory_refuses_the_old_type(
    grr: GenomicResourceProtocolRepo, tmp_path: pathlib.Path,
) -> None:
    info = AnnotatorInfo("binning_tracks_annotator", [], {"tracks": []})

    with pytest.raises(AnnotationConfigurationError) as excinfo:
        build_pipeline_annotator(
            AnnotationPipeline(grr), info, tmp_path / "work")

    message = str(excinfo.value)
    assert RENAME_MESSAGE in message
    assert "unsupported annotator type" not in message
    assert RETIRED_VOCABULARY_REMOVAL_RELEASE not in message


def test_the_config_parser_refuses_the_old_type(
    grr: GenomicResourceProtocolRepo,
) -> None:
    """The parser expands a wildcard before any factory runs."""
    with pytest.raises(AnnotationConfigurationError) as excinfo:
        AnnotationConfigParser.parse_str("""
            - binning_tracks_annotator:
                resource_id: scores/*
        """, grr=grr)

    message = str(excinfo.value)
    assert RENAME_MESSAGE in message
    assert "No resources match the wildcard" not in message
    assert RETIRED_VOCABULARY_REMOVAL_RELEASE not in message


def test_a_pipeline_with_the_new_type_does_not_get_the_refusal(
    grr: GenomicResourceProtocolRepo, tmp_path: pathlib.Path,
) -> None:
    """A control: the new type reaches the annotator itself."""
    info = AnnotatorInfo("tracks_annotator", [], {"tracks": []})

    with pytest.raises(AnnotationConfigurationError) as excinfo:
        build_pipeline_annotator(
            AnnotationPipeline(grr), info, tmp_path / "work")

    assert "tracks must be a non-empty list" in str(excinfo.value)
