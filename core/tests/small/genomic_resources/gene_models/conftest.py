# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Fixtures shared by the gene-models test modules.

Record-formatting stays per-module — chromosome names, coordinates and
attribute syntax differ per GTF flavour — while building a resource out
of the records and locating an on-disk fixture live here.
"""
import os
from collections.abc import Callable

import pytest
import pytest_mock
from gain.genomic_resources.gene_models.gene_models import GeneModels
from gain.genomic_resources.gene_models.gene_models_factory import (
    build_gene_models_from_resource,
)
from gain.genomic_resources.testing import build_inmemory_test_resource


@pytest.fixture
def gtf_gene_models() -> Callable[..., GeneModels]:
    """Build loaded gene models from a GTF of the given records.

    ``gene_mapping`` is the body of an alternative-names table, header
    included, wired up through the resource config.
    """
    def _build(
        *records: str,
        gene_mapping: str | None = None,
    ) -> GeneModels:
        config = "type: gene_models, filename: genes.gtf, format: gtf"
        content = {"genes.gtf": "\n".join(records) + "\n"}
        if gene_mapping is not None:
            config += ", gene_mapping: names.txt"
            content["names.txt"] = gene_mapping
        content["genomic_resource.yaml"] = f"{{{config}}}"

        res = build_inmemory_test_resource(content=content)
        return build_gene_models_from_resource(res).load()

    return _build


@pytest.fixture
def fixture_dirname() -> Callable[[str], str]:
    """Resolve a filename against this directory's ``fixtures/`` tree."""
    def _fixture_dirname(filename: str) -> str:
        return os.path.join(
            os.path.dirname(os.path.realpath(__file__)),
            "fixtures",
            filename)

    return _fixture_dirname


@pytest.fixture
def clean_gene_models_cache(
    mocker: pytest_mock.MockerFixture,
) -> None:
    """Empty the path-keyed gene-models cache for this test.

    ``build_gene_models_from_file`` caches on the path alone, so without
    this a module that loads one fixture under two formats gets the first
    result back.
    """
    mocker.patch(
        "gain.genomic_resources.gene_models."
        "gene_models_factory._INMEMORY_CACHE",
        {})
