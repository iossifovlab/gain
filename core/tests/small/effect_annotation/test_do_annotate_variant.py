# pylint: disable=W0621,C0114,C0116

import pytest
from gain.effect_annotation.annotator import EffectAnnotator
from gain.effect_annotation.effect import AnnotationEffect
from gain.genomic_resources.gene_models.gene_models import GeneModels
from gain.genomic_resources.reference_genome import ReferenceGenome


def test_cnv_plus_with_chrom_pos_and_length_is_a_duplication(
    t4c8_genome: ReferenceGenome,
    t4c8_genes: GeneModels,
) -> None:
    annotator = EffectAnnotator(t4c8_genome, t4c8_genes)

    effects = annotator.do_annotate_variant(
        variant="CNV+", chrom="chr1", pos=1, length=90)

    assert AnnotationEffect.simplify_effects(effects) == (
        "CNV+", [("t4", "CNV+")], [("tx1", "t4", "CNV+", "91")])


def test_cnv_minus_with_location_is_a_deletion(
    t4c8_genome: ReferenceGenome,
    t4c8_genes: GeneModels,
) -> None:
    annotator = EffectAnnotator(t4c8_genome, t4c8_genes)

    effects = annotator.do_annotate_variant(
        variant="CNV-", location="chr1:1-150")

    assert AnnotationEffect.simplify_effects(effects) == (
        "CNV-",
        [("t4", "CNV-"), ("c8", "CNV-")],
        [("tx1", "t4", "CNV-", "150"), ("tx1", "c8", "CNV-", "150")],
    )


@pytest.mark.parametrize("variant", ["CNV+", "CNV-"])
def test_cnv_without_position_or_location_is_refused(
    t4c8_genome: ReferenceGenome,
    t4c8_genes: GeneModels,
    variant: str,
) -> None:
    annotator = EffectAnnotator(t4c8_genome, t4c8_genes)

    with pytest.raises(
            ValueError, match="unexpected CNV variant description"):
        annotator.do_annotate_variant(variant=variant, chrom="chr1")


@pytest.mark.parametrize(
    "variant,expected_effect_type,expected_details", [
        ("sub(C->T)", "missense", "4/14(Pro->Ser)"),
        ("sub(c->a)", "missense", "4/14(Pro->Thr)"),
        ("ins(TTT)", "no-frame-shift", "4/14(->Phe)"),
        ("del(3)", "no-frame-shift", "4/14(Pro->)"),
        ("complex(CC->TT)", "missense", "4/14(Pro->Phe)"),
    ],
)
def test_cshl_variant_in_the_coding_region_of_t4(
    t4c8_genome: ReferenceGenome,
    t4c8_genes: GeneModels,
    variant: str,
    expected_effect_type: str,
    expected_details: str,
) -> None:
    annotator = EffectAnnotator(t4c8_genome, t4c8_genes)

    effects = annotator.do_annotate_variant(
        location="chr1:29", variant=variant)

    assert AnnotationEffect.simplify_effects(effects) == (
        expected_effect_type,
        [("t4", expected_effect_type)],
        [("tx1", "t4", expected_effect_type, expected_details)],
    )


def test_unknown_cshl_variant_is_refused(
    t4c8_genome: ReferenceGenome,
    t4c8_genes: GeneModels,
) -> None:
    annotator = EffectAnnotator(t4c8_genome, t4c8_genes)

    with pytest.raises(ValueError, match="Unknown variant"):
        annotator.do_annotate_variant(location="chr1:29", variant="xyz")
