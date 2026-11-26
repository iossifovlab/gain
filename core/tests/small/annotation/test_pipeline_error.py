import pathlib

import pytest
from gain.annotation.annotation_config import AnnotationConfigurationError
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.genomic_resources.testing import build_inmemory_test_repository


def test_pipeline_not_a_list(tmp_path: pathlib.Path) -> None:
    with pytest.raises(AnnotationConfigurationError):
        load_pipeline_from_yaml("""
            debug_annotator
            """, build_inmemory_test_repository({}), work_dir=tmp_path)


def test_pipeline_repeated_annotator_attribute(tmp_path: pathlib.Path) -> None:
    with pytest.raises(AnnotationConfigurationError, match="attributes: hi"):
        load_pipeline_from_yaml("""
            - debug_annotator:
                attributes:
                - hi
                - hi
            """, build_inmemory_test_repository({}), work_dir=tmp_path)


def test_pipeline_repeated_attribute(tmp_path: pathlib.Path) -> None:
    with pytest.raises(AnnotationConfigurationError, match="hi"):
        load_pipeline_from_yaml("""
            - debug_annotator:
                attributes:
                - hi
            - debug_annotator:
                attributes:
                - hi
            """, build_inmemory_test_repository({}), work_dir=tmp_path)


def test_pipeline_annoing_identation_error(tmp_path: pathlib.Path) -> None:
    with pytest.raises(AnnotationConfigurationError,
                       match="Incorrect annotator configuation form"):
        load_pipeline_from_yaml("""
            - debug_annotator:
              attributes:
                - hi
            """, build_inmemory_test_repository({}), work_dir=tmp_path)


def test_unused_attribute_parameter_raises(tmp_path: pathlib.Path) -> None:
    with pytest.raises(AnnotationConfigurationError,
                       match="unused parameters for attribute"):
        load_pipeline_from_yaml("""
            - debug_annotator:
                attributes:
                - name: hi
                  bogus_param: oops
            """, build_inmemory_test_repository({}), work_dir=tmp_path)
