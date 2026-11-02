from typing import cast

import pytest
from gain.effect_annotation.annotator import EffectAnnotator
from gain.genomic_resources.gene_models import Exon
from gain.genomic_resources.gene_models.gene_models import GeneModels
from gain.genomic_resources.gene_models.gene_models_factory import (
    build_gene_models_from_resource,
)
from gain.genomic_resources.reference_genome import ReferenceGenome
from gain.genomic_resources.testing.gene_models_builder import a_gene_models
from gain.testing.t4c8_import import t4c8_genes as setup_t4c8_genes
from gain.testing.t4c8_import import t4c8_genome as setup_t4c8_genome

from .mocks import AnnotatorMock, ReferenceGenomeMock


@pytest.fixture(scope="session")
def annotator() -> EffectAnnotator:
    return cast(
        EffectAnnotator,
        AnnotatorMock(
            cast(ReferenceGenome, ReferenceGenomeMock())),
    )


@pytest.fixture(scope="session")
def exons() -> list[Exon]:
    return [Exon(60, 70, 0), Exon(80, 90, 1), Exon(100, 110, 2)]


@pytest.fixture(scope="session")
def coding() -> list[Exon]:
    return [Exon(65, 70, 0), Exon(80, 90, 1), Exon(100, 110, 2)]


@pytest.fixture(scope="session")
def t4c8_genome(tmp_path_factory: pytest.TempPathFactory) -> ReferenceGenome:
    root_path = tmp_path_factory.mktemp("t4c8_genome")
    genome = setup_t4c8_genome(root_path)
    genome.open()
    return genome


@pytest.fixture(scope="session")
def t4c8_genes(tmp_path_factory: pytest.TempPathFactory) -> GeneModels:
    root_path = tmp_path_factory.mktemp("t4c8_genes")
    gene_models = setup_t4c8_genes(root_path)
    gene_models.load()
    return gene_models


@pytest.fixture(scope="session")
def t4_stop_codon_genes(
    tmp_path_factory: pytest.TempPathFactory,
) -> GeneModels:
    """The t4 transcript of t4c8, with the whole stop codon in its cds.

    The t4c8 cds of t4 ends at 71, one base before the ``G`` of the
    ``TAG`` stop codon at chr1:70-72. Here the cds ends at 72, so the
    coding sequence is 15 whole codons, ``ATG ... AAA TAG``, and the 3'
    UTR is chr1:73-84.
    """
    root_path = tmp_path_factory.mktemp("t4_stop_codon_genes")
    return build_gene_models_from_resource(
        a_gene_models()
        .with_transcript(
            "tx1", gene="t4", chrom="chr1",
            exons=[(6, 16), (26, 37), (46, 84)], cds=(11, 72))
        .build_resource(root_path),
    ).load()
