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

# When set, resources are resolved through a local cache wrapped around the
# http repo (GenomicResourceCachedRepo). The dedicated integration CI job
# (iossifovlab/gain#223) points this at a persistent cache dir on the agent so
# the ~787MB genome is downloaded once and reused across builds. Unset (the
# default for local runs) means plain http with range reads — no full download.
GRR_INTEGRATION_CACHE_DIR_ENV = "GRR_INTEGRATION_CACHE_DIR"


@pytest.fixture(scope="session")
def grr_seqpipe() -> GenomicResourceRepo:
    definition = load_definition_file(GRR_SEQPIPE_DEFINITION)
    cache_dir = os.environ.get(GRR_INTEGRATION_CACHE_DIR_ENV)
    if cache_dir:
        definition = {**definition, "cache_dir": cache_dir}
    return build_genomic_resource_repository(definition=definition)


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
