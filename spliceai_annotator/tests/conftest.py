# pylint: disable=W0621,C0114,C0116,W0212,W0613

import os
import pathlib
import textwrap
from typing import cast

import pytest
import yaml
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.annotation.annotation_pipeline import AnnotationPipeline
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)
from gain.genomic_resources.testing import build_filesystem_test_repository

from spliceai_annotator.spliceai_annotator import (
    SpliceAIAnnotator,
)


@pytest.fixture(scope="session", autouse=True)
def assert_requested_backend_is_the_one_running() -> None:
    """Fail loudly if SPLICEAI_BACKEND was asked for but did not take effect.

    The ONNX CI tier differs from the TensorFlow one by a single `docker run`
    flag, and nothing else in the suite would notice its absence: the
    backend-selection tests set the variable themselves, and the ONNX tests
    load their backend by name. Drop the flag and you get a fully green "ONNX
    tier" that ran TensorFlow twice -- and #297's "passes on both backends"
    would be asserted by nothing.
    """
    requested = os.environ.get("SPLICEAI_BACKEND")
    if requested is None:
        return
    # pylint: disable=import-outside-toplevel
    from spliceai_annotator import spliceai_annotator_impl as impl
    assert requested.strip().lower() == impl.SPLICEAI_BACKEND_NAME, (
        f"SPLICEAI_BACKEND={requested!r} was requested but the loaded "
        f"runtime is {impl.SPLICEAI_BACKEND_NAME!r}")


INTEGRATION_GRR_DEFINITION = (
    pathlib.Path(__file__).parent / "integration_grr_definition.yaml"
)


@pytest.fixture(scope="session")
def spliceai_grr() -> GenomicResourceRepo:
    """Fixture for SpliceAI genomic resources repository."""
    return build_filesystem_test_repository(
        pathlib.Path(__file__).parent / "fixtures",
    )


@pytest.fixture(scope="session")
def real_grr() -> GenomicResourceRepo:
    """The node-local *real* GRR for the #321 integration tier.

    Resolves the checked-in ``integration_grr_definition.yaml`` (a ``type:
    directory``, ``read_only: true`` GRR mounted at ``/grr`` in the Jenkins
    job). The directory can be overridden for local runs with
    ``SPLICEAI_INTEGRATION_GRR_DIR``.

    Absence handling (issue #321 decision): ``pytest.skip`` on a dev box so a
    bare ``pytest`` stays green without ``/grr`` -- UNLESS
    ``SPLICEAI_INTEGRATION_REQUIRE_GRR`` is set (only ``Jenkinsfile.
    integration`` sets it), which turns absence into a hard error so a
    mis-provisioned agent can never pass falsely-green.
    """
    definition = yaml.safe_load(INTEGRATION_GRR_DEFINITION.read_text())
    override = os.environ.get("SPLICEAI_INTEGRATION_GRR_DIR")
    if override:
        for child in definition["children"]:
            child["directory"] = override

    grr_dir = pathlib.Path(definition["children"][0]["directory"])
    if not grr_dir.exists():
        message = (
            f"node-local real GRR not found at {grr_dir}; set "
            "SPLICEAI_INTEGRATION_GRR_DIR or mount the grr-sync tree at /grr"
        )
        if os.environ.get("SPLICEAI_INTEGRATION_REQUIRE_GRR"):
            raise RuntimeError(message)
        pytest.skip(message)

    return build_genomic_resource_repository(definition)


@pytest.fixture(scope="session")
def spliceai_annotation_pipeline(
    spliceai_grr: GenomicResourceRepo,
) -> AnnotationPipeline:
    """Fixture for SpliceAI annotator."""

    pipeline_config = textwrap.dedent("""
    - spliceai_annotator:
        genome: hg19/genome_10
        gene_models: hg19/gene_models_small
        distance: 500
        attributes:
        - delta_score
    """)
    return load_pipeline_from_yaml(
        pipeline_config,
        spliceai_grr,
    )


@pytest.fixture(scope="session")
def spliceai_annotator(
    spliceai_annotation_pipeline: AnnotationPipeline,
) -> SpliceAIAnnotator:
    """Fixture for SpliceAI annotator."""
    annotator = spliceai_annotation_pipeline.annotators[0]
    return cast(
        SpliceAIAnnotator, annotator.open())
