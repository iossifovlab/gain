# pylint: disable=W0621,C0114,C0116,W0212,W0613
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


@pytest.fixture(scope="session")
def gene_models_2013() -> GeneModels:
    pytest.skip(
        "refGene_v201309 is being retired; tests using it are skipped "
        "pending migration to refGene_v20190211 or deletion "
        "(see iossifovlab/gain#15)",
    )
    gene_models = build_gene_models_from_resource_id(
        "hg19/gene_models/refGene_v201309")
    gene_models.load()
    return gene_models


@pytest.fixture(scope="session")
def genome_2013() -> ReferenceGenome:
    return build_reference_genome_from_resource_id(
        "hg19/genomes/GATK_ResourceBundle_5777_b37_phiX174").open()


@pytest.fixture(scope="session")
def gene_models_2019() -> GeneModels:
    gene_models = build_gene_models_from_resource_id(
        "hg19/gene_models/refGene_v20190211")
    gene_models.load()
    return gene_models


@pytest.fixture(scope="session")
def genome_2019() -> ReferenceGenome:
    return build_reference_genome_from_resource_id(
        "hg19/genomes/GATK_ResourceBundle_5777_b37_phiX174").open()
