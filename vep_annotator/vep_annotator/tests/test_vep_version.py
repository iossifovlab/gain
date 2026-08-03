# pylint: disable=W0621,C0114,C0116,W0212,W0613
import textwrap
from pathlib import Path
from typing import cast

import pytest
from gain.annotation.annotation_config import AnnotationConfigurationError
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.annotation.docker_annotator import DockerAnnotator
from pytest_mock import MockerFixture

from vep_annotator.vep_annotator import VEPCacheAnnotator


def build_vep_annotator(
    mocker: MockerFixture,
    tmp_path: Path,
    vep_version_config: str,
) -> VEPCacheAnnotator:
    """Build a VEP cache annotator with the given `vep_version` config line.

    The docker client is stubbed out, so the annotator resolves its VEP
    version without talking to a docker daemon.
    """
    mocker.patch.object(
        DockerAnnotator, "_create_client", return_value=mocker.MagicMock(),
    )
    pipeline = load_pipeline_from_yaml(textwrap.dedent(f"""
        - vep_full_annotator:
            cache_dir: {tmp_path}
            {vep_version_config}
    """), None, work_dir=tmp_path)  # type: ignore
    return cast(VEPCacheAnnotator, pipeline.annotators[0])


def test_major_only_version_is_expanded_to_major_minor(
    mocker: MockerFixture, tmp_path: Path,
) -> None:
    annotator = build_vep_annotator(mocker, tmp_path, 'vep_version: "113"')

    assert annotator._vep_version == "release_113.0"


def test_unquoted_major_only_version_is_expanded_to_major_minor(
    mocker: MockerFixture, tmp_path: Path,
) -> None:
    annotator = build_vep_annotator(mocker, tmp_path, "vep_version: 113")

    assert annotator._vep_version == "release_113.0"


def test_version_with_minor_is_used_as_given(
    mocker: MockerFixture, tmp_path: Path,
) -> None:
    annotator = build_vep_annotator(mocker, tmp_path, 'vep_version: "113.4"')

    assert annotator._vep_version == "release_113.4"


def test_missing_version_selects_the_latest_release(
    mocker: MockerFixture, tmp_path: Path,
) -> None:
    annotator = build_vep_annotator(mocker, tmp_path, "")

    assert annotator._vep_version == "release_latest"


def test_unquoted_version_with_minor_is_rejected(
    mocker: MockerFixture, tmp_path: Path,
) -> None:
    # YAML reads an unquoted `113.10` as the float 113.1, which would
    # silently select a different image; refuse it instead.
    with pytest.raises(AnnotationConfigurationError, match="vep_version"):
        build_vep_annotator(mocker, tmp_path, "vep_version: 113.4")
