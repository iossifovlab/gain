# pylint: disable=W0621,C0114,C0116,W0212,W0613

from typing import cast

from gain.effect_annotation.effect import (
    AlleleEffects,
    EffectGene,
    EffectTranscript,
)

EFFECT_DATA_1 = (
    "splice-site!"
    "NUDT4:splice-site|"
    "NUDT4B:splice-site|"
    "NUDT4:splice-site|"
    "NUDT4P2:non-coding-intron!"
    "NM_001301022:NUDT4:splice-site:63/129|"
    "NM_001301023:NUDT4:splice-site:62/128|"
    "NM_001301024:NUDT4:splice-site:62/128|"
    "NM_001355407:NUDT4B:splice-site:115/181|"
    "NM_019094:NUDT4:splice-site:114/180|"
    "NM_199040:NUDT4:splice-site:115/181|"
    "NR_104005:NUDT4P2:non-coding-intron:None/None[None]"
)


EFFECT_TRANSCRIPT_DATA_1 = "NM_031857:PCDHA9:3'UTR:1480"


def test_effect_transcript_deserialization() -> None:
    effect_trancript = EffectTranscript.from_string(EFFECT_TRANSCRIPT_DATA_1)
    assert str(effect_trancript) == EFFECT_TRANSCRIPT_DATA_1


def test_allele_effect_deserialization() -> None:
    allele_effect = AlleleEffects.from_string(EFFECT_DATA_1)

    assert str(allele_effect) == EFFECT_DATA_1


def test_allele_effects_not_equal_to_other_type() -> None:
    allele_effect = AlleleEffects.from_string(EFFECT_DATA_1)

    assert allele_effect != EFFECT_DATA_1


def test_allele_effects_equal_when_deserialized_from_same_data() -> None:
    first = AlleleEffects.from_string(EFFECT_DATA_1)
    second = AlleleEffects.from_string(EFFECT_DATA_1)

    assert first == second


def test_allele_effects_differ_by_worst_effect() -> None:
    first = AlleleEffects.from_string(EFFECT_DATA_1)
    second = AlleleEffects.from_string(
        EFFECT_DATA_1.replace("splice-site!", "missense!", 1))

    assert first != second


def test_allele_effects_differ_by_genes() -> None:
    first = AlleleEffects.from_string(EFFECT_DATA_1)
    second = AlleleEffects.from_string(
        EFFECT_DATA_1.replace("NUDT4B:splice-site|", "NUDT4B:missense|", 1))

    assert first != second


def test_allele_effects_differ_by_transcripts() -> None:
    first = AlleleEffects.from_string(EFFECT_DATA_1)
    second = AlleleEffects.from_string(
        EFFECT_DATA_1.replace(":63/129|", ":64/129|", 1))

    assert first != second


def test_from_effect_transcripts_parses_four_part_details() -> None:
    result = EffectTranscript.from_effect_transcripts(
        [("NM_X", EFFECT_TRANSCRIPT_DATA_1)])

    assert list(result) == ["NM_X"]
    assert str(result["NM_X"]) == EFFECT_TRANSCRIPT_DATA_1


def test_from_effect_transcripts_keeps_other_details_whole() -> None:
    result = EffectTranscript.from_effect_transcripts(
        [("NM_001301022", "63/129")])

    assert str(result["NM_001301022"]) == "NM_001301022:None:None:63/129"


def test_from_effect_transcripts_maps_none_id_to_intergenic() -> None:
    result = EffectTranscript.from_effect_transcripts(
        [cast(tuple[str, str], (None, "intergenic"))])

    assert list(result) == ["intergenic"]
    assert str(result["intergenic"]) == (
        "intergenic:intergenic:intergenic:intergenic")


def test_from_simplified_effects_builds_genes_and_transcripts() -> None:
    result = AlleleEffects.from_simplified_effects(
        "3'UTR",
        [("PCDHA9", "3'UTR")],
        [("NM_031857", EFFECT_TRANSCRIPT_DATA_1)],
    )

    assert result is not None
    assert result.genes == [EffectGene("PCDHA9", "3'UTR")]
    assert str(result) == f"3'UTR!PCDHA9:3'UTR!{EFFECT_TRANSCRIPT_DATA_1}"
