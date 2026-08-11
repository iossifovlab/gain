# pylint: disable=W0621,C0114,C0116,W0212,W0613
import os
import pathlib
from collections.abc import Iterator

import pytest
from gain.genomic_resources.gene_models.gene_models import (
    GeneModels,
)
from gain.genomic_resources.gene_models.gene_models_factory import (
    build_gene_models_from_resource_id,
)
from gain.genomic_resources.reference_genome import (
    ReferenceGenome,
    build_reference_genome_from_resource_id,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
    load_definition_file,
)

# Checked-in ``type: http`` GRR definition pointing at grr-seqpipe. Building
# the repository from this file makes the effect-annotation integration tests
# self-contained: they resolve the genome and gene-models resources from
# grr-seqpipe without a developer/CI having to export GRR_DEFINITION_FILE.
GRR_SEQPIPE_DEFINITION = str(
    pathlib.Path(__file__).parent / "grr-seqpipe-definition.yaml")

# When set, the suite reads a node-local ``type: directory`` copy of
# grr-seqpipe at this path instead of the http repo. The dedicated integration
# CI job (iossifovlab/gain#799) bind-mounts the agent's grr-sync tree
# (seqpipe/infra#71) read-only and points this at the mount, so every resource
# is a byte-range read off local disk — no network, no cache.
GRR_INTEGRATION_DIR_ENV = "GRR_INTEGRATION_DIR"

# When set (and GRR_INTEGRATION_DIR is not), resources are resolved through a
# local cache wrapped around the http repo (GenomicResourceCachedRepo), so the
# ~787MB genome is downloaded once and reused across builds. Unset (the
# default for local runs) means plain http with range reads — no full download.
GRR_INTEGRATION_CACHE_DIR_ENV = "GRR_INTEGRATION_CACHE_DIR"


def grr_seqpipe_definition() -> dict:
    """Resolve the grr-seqpipe repository definition for this run.

    A node-local directory copy (GRR_INTEGRATION_DIR) beats the http repo;
    the cache wrapper (GRR_INTEGRATION_CACHE_DIR) applies to http only — a
    directory repo is already local, so caching it would just copy disk to
    disk.
    """
    local_dir = os.environ.get(GRR_INTEGRATION_DIR_ENV)
    if local_dir:
        return {
            "id": "grr-seqpipe",
            "type": "directory",
            "directory": local_dir,
        }
    definition: dict = load_definition_file(GRR_SEQPIPE_DEFINITION)
    cache_dir = os.environ.get(GRR_INTEGRATION_CACHE_DIR_ENV)
    if cache_dir:
        definition = {**definition, "cache_dir": cache_dir}
    return definition


@pytest.fixture(scope="session")
def grr_seqpipe() -> GenomicResourceRepo:
    return build_genomic_resource_repository(
        definition=grr_seqpipe_definition())


@pytest.fixture(scope="session")
def gene_models_2013(grr_seqpipe: GenomicResourceRepo) -> GeneModels:
    gene_models = build_gene_models_from_resource_id(
        "hg19/gene_models/refGene_v201309", grr=grr_seqpipe)
    gene_models.load()
    return gene_models


@pytest.fixture(scope="session")
def genome_2013(
    grr_seqpipe: GenomicResourceRepo,
) -> Iterator[ReferenceGenome]:
    with build_reference_genome_from_resource_id(
        "hg19/genomes/GATK_ResourceBundle_5777_b37_phiX174",
        grr=grr_seqpipe).open() as genome:
        yield genome


@pytest.fixture(scope="session")
def gene_models_2019(grr_seqpipe: GenomicResourceRepo) -> GeneModels:
    gene_models = build_gene_models_from_resource_id(
        "hg19/gene_models/refGene_v20190211", grr=grr_seqpipe)
    gene_models.load()
    return gene_models


@pytest.fixture(scope="session")
def genome_2019(
    grr_seqpipe: GenomicResourceRepo,
) -> Iterator[ReferenceGenome]:
    with build_reference_genome_from_resource_id(
        "hg19/genomes/GATK_ResourceBundle_5777_b37_phiX174",
        grr=grr_seqpipe).open() as genome:
        yield genome
