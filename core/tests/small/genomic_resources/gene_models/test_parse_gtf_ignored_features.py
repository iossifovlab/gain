# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""GTF records of ignored features are skipped before attribute parsing,
so no ``transcript_id`` is required of them."""

import pytest
from gain.genomic_resources.gene_models import parsers
from gain.genomic_resources.gene_models.gene_models import GeneModels
from gain.genomic_resources.gene_models.gene_models_factory import (
    build_gene_models_from_resource,
)
from gain.genomic_resources.testing import build_inmemory_test_resource


def _record(feature: str, start: int, end: int, attributes: str) -> str:
    return "\t".join([
        "X", "test", feature, str(start), str(end), ".", "+", ".",
        attributes,
    ])


def _gene_models(*records: str) -> GeneModels:
    res = build_inmemory_test_resource(content={
        "genomic_resource.yaml":
            "{type: gene_models, filename: genes.gtf, format: gtf}",
        "genes.gtf": "\n".join(records) + "\n",
    })
    return build_gene_models_from_resource(res).load()


@pytest.mark.parametrize("feature", sorted(parsers.GTF_IGNORED_FEATURES))
def test_an_ignored_record_needs_no_transcript_id(feature: str) -> None:
    gene_models = _gene_models(
        _record(feature, 100, 200, 'gene_id "G1";'),
    )

    assert gene_models.transcript_models == {}


def test_the_ignored_set_claims_no_feature_handled_later() -> None:
    """The ignore check runs first, so an overlap would silently win."""
    handled_later = (
        parsers.GTF_TRANSCRIPT_FEATURES
        | parsers.GTF_EXONLESS_TRANSCRIPT_FEATURES
        | parsers.GTF_EXON_FEATURES
        | parsers.GTF_CODON_FEATURES
        | {"Selenocysteine"}
    )

    assert not parsers.GTF_IGNORED_FEATURES & handled_later
