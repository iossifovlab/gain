# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""GTF records of ignored features are skipped before attribute parsing,
so no ``transcript_id`` is required of them."""

from collections.abc import Callable

import pytest
from gain.genomic_resources.gene_models import parsers
from gain.genomic_resources.gene_models.gene_models import GeneModels


def _record(feature: str, start: int, end: int, attributes: str) -> str:
    return "\t".join([
        "X", "test", feature, str(start), str(end), ".", "+", ".",
        attributes,
    ])


@pytest.mark.parametrize("feature", sorted(parsers.GTF_IGNORED_FEATURES))
def test_an_ignored_record_needs_no_transcript_id(
    gtf_gene_models: Callable[..., GeneModels],
    feature: str,
) -> None:
    gene_models = gtf_gene_models(
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
        | parsers.GTF_CDS_FEATURES
        | parsers.GTF_SELENOCYSTEINE_FEATURES
    )

    assert not parsers.GTF_IGNORED_FEATURES & handled_later
