# pylint: disable=W0621,C0114,C0116
"""The stop codon check of the UTR effect checker on the t4 transcript.

The t4c8 genome has the ``TAG`` stop codon of t4 at chr1:70-72 and the
``AAA`` codon before it at chr1:67-69.
"""

import pathlib

from gain.effect_annotation.annotator import (
    AnnotationRequestFactory,
    EffectAnnotator,
    Variant,
)
from gain.effect_annotation.effect import AnnotationEffect
from gain.effect_annotation.effect_checkers.utr import UTREffectChecker
from gain.genomic_resources.gene_models.gene_models_factory import (
    build_gene_models_from_resource,
)
from gain.genomic_resources.reference_genome import ReferenceGenome
from gain.genomic_resources.testing.gene_models_builder import a_gene_models


def _check_stop_codon(
    genome: ReferenceGenome,
    root_path: pathlib.Path,
    exons: list[tuple[int, int]],
    cds: tuple[int, int],
    variant: Variant,
) -> AnnotationEffect | None:
    gene_models = build_gene_models_from_resource(
        a_gene_models()
        .with_transcript(
            "tx1", gene="t4", chrom="chr1", exons=exons, cds=cds)
        .build_resource(root_path),
    ).load()
    transcripts = gene_models.gene_models_by_gene_name("t4")
    assert transcripts is not None
    annotator = EffectAnnotator(genome, gene_models)
    request = AnnotationRequestFactory.create_annotation_request(
        annotator, variant, transcripts[0],
    )
    return UTREffectChecker().check_stop_codon(request)


def test_stop_codon_without_3_prime_utr_has_no_utr_effect(
    t4c8_genome: ReferenceGenome,
    tmp_path: pathlib.Path,
) -> None:
    effect = _check_stop_codon(
        t4c8_genome, tmp_path,
        exons=[(6, 16), (26, 37), (46, 72)], cds=(11, 72),
        variant=Variant(loc="chr1:72", ref="G", alt="A"),
    )

    assert effect is None


def test_reference_without_a_stop_codon_has_no_utr_effect(
    t4c8_genome: ReferenceGenome,
    tmp_path: pathlib.Path,
) -> None:
    effect = _check_stop_codon(
        t4c8_genome, tmp_path,
        exons=[(6, 16), (26, 37), (46, 84)], cds=(11, 71),
        variant=Variant(loc="chr1:69", ref="A", alt="C"),
    )

    assert effect is None
