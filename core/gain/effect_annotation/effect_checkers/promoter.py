
from gain.effect_annotation.annotation_request import AnnotationRequest
from gain.effect_annotation.effect import AnnotationEffect, EffectFactory
from gain.effect_annotation.effect_checkers.effect_checker import EffectChecker
from gain.effect_annotation.variant import Variant
from gain.genomic_resources.gene_models import TranscriptModel


class PromoterEffectChecker(EffectChecker):
    """Promoter effect checker class.

    The promoter is the window of ``promoter_len`` bases upstream of the
    first exon. The near base of the variant must lie in the window. A
    near base at distance ``promoter_len`` is inside, and at distance
    ``promoter_len + 1`` it is outside. The distance of the base next to
    the exon is 1. A variant with any base inside an exon has no promoter
    effect.
    """

    def create_effect(
        self, transcript_model: TranscriptModel,
    ) -> AnnotationEffect:
        return EffectFactory.create_effect_with_tm(
            "promoter", transcript_model)

    def create_positive_strand_effect(
        self, transcript_model: TranscriptModel,
        variant: Variant,
    ) -> AnnotationEffect:
        """Create a positive strand promoter effect."""
        assert variant.corrected_ref_position_last is not None

        effect = self.create_effect(transcript_model)
        effect.dist_from_5utr = (
            transcript_model.exons[0].start
            - variant.corrected_ref_position_last
        )
        return effect

    def create_negative_strand_effect(
        self, transcript_model: TranscriptModel, variant: Variant,
    ) -> AnnotationEffect:
        effect = self.create_effect(transcript_model)
        effect.dist_from_5utr = \
            variant.position - transcript_model.exons[-1].stop
        return effect

    def get_effect(
        self, request: AnnotationRequest,
    ) -> AnnotationEffect | None:
        """Return a promoter effect or None."""
        if request.promoter_len == 0:
            return None

        assert request.variant.corrected_ref_position_last is not None

        # The near base is the base of the variant closest to the
        # transcript. On the "+" strand it is the inclusive last base. A
        # near base before the first exon means no base is inside an exon.
        if (
            request.transcript_model.strand == "+"
            and request.variant.corrected_ref_position_last
            < request.transcript_model.exons[0].start
            and request.variant.corrected_ref_position_last
            >= request.transcript_model.exons[0].start
            - request.promoter_len
        ):
            return self.create_positive_strand_effect(
                request.transcript_model, request.variant,
            )

        if (
            request.variant.position > request.transcript_model.exons[-1].stop
            and request.transcript_model.strand == "-"
            and request.variant.position
            <= request.transcript_model.exons[-1].stop
            + request.promoter_len
        ):
            return self.create_negative_strand_effect(
                request.transcript_model, request.variant,
            )
        return None
