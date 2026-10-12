# pylint: disable=W0621,C0114,C0116,W0212,W0613
from typing import cast

import pytest
from gain.effect_annotation.annotator import (
    AnnotationRequestFactory,
    EffectAnnotator,
    Variant,
)
from gain.effect_annotation.effect_checkers.promoter import (
    PromoterEffectChecker,
)
from gain.genomic_resources.gene_models import Exon, TranscriptModel
from gain.genomic_resources.reference_genome import ReferenceGenome

from .mocks import AnnotatorMock, ReferenceGenomeMock, TranscriptModelMock

PROMOTER_LEN = 100


def _annotator(promoter_len: int) -> EffectAnnotator:
    mock = AnnotatorMock(cast(ReferenceGenome, ReferenceGenomeMock()))
    mock.promoter_len = promoter_len
    return cast(EffectAnnotator, mock)


def _transcript(strand: str) -> TranscriptModel:
    exons = [Exon(1000, 1100, 0), Exon(1200, 1300, 1)]
    return cast(
        TranscriptModel, TranscriptModelMock(strand, 1000, 1300, exons))


PROMOTER_ROWS = [
    ("+", "1:899", "A", "C", None),
    ("+", "1:900", "A", "C", 100),
    ("+", "1:999", "A", "C", 1),
    ("+", "1:998", "AAA", "", None),
    ("+", "1:997", "AAA", "", 1),
    ("-", "1:1401", "A", "C", None),
    ("-", "1:1400", "A", "C", 100),
    ("-", "1:1301", "A", "C", 1),
    ("-", "1:1299", "AAA", "", None),
]


@pytest.mark.parametrize(
    "strand,loc,ref,alt,expected_dist", PROMOTER_ROWS)
def test_promoter_window_and_distance(
    strand: str, loc: str, ref: str, alt: str, expected_dist: int | None,
) -> None:
    variant = Variant(loc=loc, ref=ref, alt=alt)
    request = AnnotationRequestFactory.create_annotation_request(
        _annotator(PROMOTER_LEN), variant, _transcript(strand))

    effect = PromoterEffectChecker().get_effect(request)

    if expected_dist is None:
        assert effect is None
    else:
        assert effect is not None
        assert effect.effect == "promoter"
        assert effect.dist_from_5utr == expected_dist


@pytest.mark.parametrize(
    "strand,loc,ref,alt,expected_dist", PROMOTER_ROWS)
def test_promoter_len_zero_gives_no_effect(
    strand: str, loc: str, ref: str, alt: str, expected_dist: int | None,
) -> None:
    variant = Variant(loc=loc, ref=ref, alt=alt)
    request = AnnotationRequestFactory.create_annotation_request(
        _annotator(0), variant, _transcript(strand))

    assert PromoterEffectChecker().get_effect(request) is None
