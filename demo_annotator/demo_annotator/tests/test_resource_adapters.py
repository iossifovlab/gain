# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pathlib
import textwrap

import pytest
from gain.annotation.annotation_config import AnnotationConfigurationError
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing import build_inmemory_test_repository


@pytest.fixture
def empty_grr() -> GenomicResourceRepo:
    """A GRR with nothing in it.

    The refusals under test fire before any resource is fetched; an
    empty GRR is what proves that ordering.
    """
    return build_inmemory_test_repository({})


def assert_refuses_empty_resource_id(
    excinfo: pytest.ExceptionInfo[AnnotationConfigurationError],
    annotator_type: str,
    parameter: str,
) -> None:
    """The pipeline refused an explicit empty resource id by name.

    The cause names the annotator and the parameter a curator has to
    fix, and carries neither ``<>`` nor ``None`` -- the two spellings
    the empty id takes when it is resolved instead of refused. Same
    contract as the helper gain-core's annotation tests share.
    """
    cause = excinfo.value.__cause__
    assert isinstance(cause, ValueError)
    assert annotator_type in str(cause)
    assert parameter in str(cause)
    assert "<>" not in str(cause)
    assert "None" not in str(cause)


def test_gene_annotator_refuses_an_empty_gene_models_id(
    empty_grr: GenomicResourceRepo,
    tmp_path: pathlib.Path,
) -> None:
    """An explicit ``gene_models: ""`` is refused, not looked up (gain#1534)."""
    config = textwrap.dedent("""
        - external_demo_gene_annotator:
            gene_models: ""
    """)

    with pytest.raises(AnnotationConfigurationError) as excinfo:
        load_pipeline_from_yaml(config, empty_grr, work_dir=tmp_path)

    assert_refuses_empty_resource_id(
        excinfo, "external_demo_gene_annotator", "gene_models")


def test_genome_annotator_refuses_an_empty_reference_genome_id(
    empty_grr: GenomicResourceRepo,
    tmp_path: pathlib.Path,
) -> None:
    """An explicit ``reference_genome: ""`` is refused by name (gain#1534)."""
    config = textwrap.dedent("""
        - external_demo_genome_annotator:
            reference_genome: ""
    """)

    with pytest.raises(AnnotationConfigurationError) as excinfo:
        load_pipeline_from_yaml(config, empty_grr, work_dir=tmp_path)

    assert_refuses_empty_resource_id(
        excinfo, "external_demo_genome_annotator", "reference_genome")
