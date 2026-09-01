# pylint: disable=W0621,C0114,C0116,W0212,W0613
import textwrap
from collections.abc import Callable

import pytest
from gain.genomic_resources.gene_models.gene_models import (
    GeneModels,
)
from gain.genomic_resources.testing import (
    convert_to_tab_separated,
)


@pytest.fixture(params=[
    "UTR", "5UTR", "3UTR", "five_prime_utr", "three_prime_utr",
])
def ensembl_gtf_example(
    request: pytest.FixtureRequest,
    gtf_gene_models: Callable[..., GeneModels],
) -> GeneModels:
    # Example from: https://ftp.ensembl.org/pub/current/gtf/homo_sapiens/README
    utr_name = request.param
    records = convert_to_tab_separated(textwrap.dedent(f"""
#!genome-build GRCh38
11  ensembl_havana  gene        5422111  5423206  .  +  .  gene_id||"ENSG00000167360";gene_version||"4";gene_name||"OR51Q1";gene_source||"ensembl_havana";gene_biotype||"protein_coding";
11  ensembl_havana  transcript  5422111  5423206  .  +  .  gene_id||"ENSG00000167360";gene_version||"4";transcript_id||"ENST00000300778";transcript_version||"4";gene_name||"OR51Q1";gene_source||"ensembl_havana";gene_biotype||"protein_coding";transcript_name||"OR51Q1-001";transcript_source||"ensembl_havana";transcript_biotype||"protein_coding";tag||"CCDS";ccds_id||"CCDS31381";
11  ensembl_havana  exon        5422111  5423206  .  +  .  gene_id||"ENSG00000167360";gene_version||"4";transcript_id||"ENST00000300778";transcript_version||"4";exon_number||"1";gene_name||"OR51Q1";gene_source||"ensembl_havana";gene_biotype||"protein_coding";transcript_name||"OR51Q1-001";transcript_source||"ensembl_havana";transcript_biotype||"protein_coding";tag||"CCDS";ccds_id||"CCDS31381";exon_id||"ENSE00001276439";exon_version||"4";
11  ensembl_havana  CDS         5422201  5423151  .  +  0  gene_id||"ENSG00000167360";gene_version||"4";transcript_id||"ENST00000300778";transcript_version||"4";exon_number||"1";gene_name||"OR51Q1";gene_source||"ensembl_havana";gene_biotype||"protein_coding";transcript_name||"OR51Q1-001";transcript_source||"ensembl_havana";transcript_biotype||"protein_coding";tag||"CCDS";ccds_id||"CCDS31381";protein_id||"ENSP00000300778";protein_version||"4";
11  ensembl_havana  start_codon 5422201  5422203  .  +  0  gene_id||"ENSG00000167360";gene_version||"4";transcript_id||"ENST00000300778";transcript_version||"4";exon_number||"1";gene_name||"OR51Q1";gene_source||"ensembl_havana";gene_biotype||"protein_coding";transcript_name||"OR51Q1-001";transcript_source||"ensembl_havana";transcript_biotype||"protein_coding";tag||"CCDS";ccds_id||"CCDS31381";
11  ensembl_havana  stop_codon  5423152  5423154  .  +  0  gene_id||"ENSG00000167360";gene_version||"4";transcript_id||"ENST00000300778";transcript_version||"4";exon_number||"1";gene_name||"OR51Q1";gene_source||"ensembl_havana";gene_biotype||"protein_coding";transcript_name||"OR51Q1-001";transcript_source||"ensembl_havana";transcript_biotype||"protein_coding";tag||"CCDS";ccds_id||"CCDS31381";
11  ensembl_havana  {utr_name}  5422111  5422200  .  +  .  gene_id||"ENSG00000167360";gene_version||"4";transcript_id||"ENST00000300778";transcript_version||"4";gene_name||"OR51Q1";gene_source||"ensembl_havana";gene_biotype||"protein_coding";transcript_name||"OR51Q1-001";transcript_source||"ensembl_havana";transcript_biotype||"protein_coding";tag||"CCDS";ccds_id||"CCDS31381";
11  ensembl_havana  {utr_name}  5423155  5423206  .  +  .  gene_id||"ENSG00000167360";gene_version||"4";transcript_id||"ENST00000300778";transcript_version||"4";gene_name||"OR51Q1";gene_source||"ensembl_havana";gene_biotype||"protein_coding";transcript_name||"OR51Q1-001";transcript_source||"ensembl_havana";transcript_biotype||"protein_coding";tag||"CCDS";ccds_id||"CCDS31381";tag||"a||b||c";
""")).splitlines()  # ruff: ignore[line-too-long]
    return gtf_gene_models(*records)


def test_parse_gtf_utr(
    ensembl_gtf_example: GeneModels,
) -> None:
    gene_models = ensembl_gtf_example
    assert gene_models is not None
    assert gene_models.gene_models_by_gene_name("OR51Q1") is not None
