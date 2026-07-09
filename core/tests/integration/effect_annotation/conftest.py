# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pathlib

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
)

# Checked-in ``type: http`` GRR definition pointing at grr-seqpipe. Building
# the repository from this file makes the effect-annotation integration tests
# self-contained: they resolve the genome and gene-models resources from
# grr-seqpipe without a developer/CI having to export GRR_DEFINITION_FILE.
GRR_SEQPIPE_DEFINITION = str(
    pathlib.Path(__file__).parent / "grr-seqpipe-definition.yaml")


@pytest.fixture(scope="session")
def grr_seqpipe() -> GenomicResourceRepo:
    return build_genomic_resource_repository(file_name=GRR_SEQPIPE_DEFINITION)


@pytest.fixture(scope="session")
def gene_models_2013(grr_seqpipe: GenomicResourceRepo) -> GeneModels:
    gene_models = build_gene_models_from_resource_id(
        "hg19/gene_models/refGene_v201309", grr=grr_seqpipe)
    gene_models.load()
    return gene_models


@pytest.fixture(scope="session")
def genome_2013(grr_seqpipe: GenomicResourceRepo) -> ReferenceGenome:
    return build_reference_genome_from_resource_id(
        "hg19/genomes/GATK_ResourceBundle_5777_b37_phiX174",
        grr=grr_seqpipe).open()


@pytest.fixture(scope="session")
def gene_models_2019(grr_seqpipe: GenomicResourceRepo) -> GeneModels:
    gene_models = build_gene_models_from_resource_id(
        "hg19/gene_models/refGene_v20190211", grr=grr_seqpipe)
    gene_models.load()
    return gene_models


@pytest.fixture(scope="session")
def genome_2019(grr_seqpipe: GenomicResourceRepo) -> ReferenceGenome:
    return build_reference_genome_from_resource_id(
        "hg19/genomes/GATK_ResourceBundle_5777_b37_phiX174",
        grr=grr_seqpipe).open()
