# pylint: disable=W0621,C0114,C0116
"""Variants in the ``TAG`` stop codon of t4, at chr1:70-72.

The ``t4_stop_codon_genes`` fixture gives t4 the whole stop codon in its
cds and a 3' UTR at chr1:73-84. The codon before the stop codon is
``AAA`` (Lys) at chr1:67-69.
"""

import pytest
from gain.effect_annotation.annotator import EffectAnnotator
from gain.effect_annotation.effect import AnnotationEffect
from gain.genomic_resources.gene_models.gene_models import GeneModels
from gain.genomic_resources.reference_genome import ReferenceGenome


@pytest.fixture
def stop_codon_annotator(
    t4c8_genome: ReferenceGenome,
    t4_stop_codon_genes: GeneModels,
) -> EffectAnnotator:
    return EffectAnnotator(t4c8_genome, t4_stop_codon_genes)


def test_new_stop_before_the_stop_codon_is_no_frame_shift(
    stop_codon_annotator: EffectAnnotator,
) -> None:
    # AAA TAG -> TAA TAG: the first stop moves one codon earlier.
    effects = stop_codon_annotator.annotate_allele(
        chrom="chr1", pos=67, ref="AAAT", alt="TAAT")

    assert AnnotationEffect.simplify_effects(effects) == (
        "no-frame-shift",
        [("t4", "no-frame-shift")],
        [("tx1", "t4", "no-frame-shift", "14/14(LysEnd->EndEnd)")],
    )


def test_snv_that_keeps_the_stop_codon_is_synonymous(
    stop_codon_annotator: EffectAnnotator,
) -> None:
    # TAG -> TAA: the stop codon stays a stop codon.
    effects = stop_codon_annotator.annotate_allele(
        chrom="chr1", pos=72, ref="G", alt="A")

    assert AnnotationEffect.simplify_effects(effects) == (
        "synonymous",
        [("t4", "synonymous")],
        [("tx1", "t4", "synonymous", "15/14")],
    )


def test_insertion_that_keeps_the_first_stop_is_3_prime_utr(
    stop_codon_annotator: EffectAnnotator,
) -> None:
    # TAG -> TAAG: the first stop codon stays at the same index, and the
    # effect is a 3'UTR effect at distance 0 from the coding region.
    effects = stop_codon_annotator.annotate_allele(
        chrom="chr1", pos=70, ref="T", alt="TA")

    assert AnnotationEffect.simplify_effects(effects) == (
        "3'UTR",
        [("t4", "3'UTR")],
        [("tx1", "t4", "3'UTR", "0")],
    )
