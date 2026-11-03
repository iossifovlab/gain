from gain.effect_annotation.annotation_effects import (
    get_effect_types,
    get_effect_types_set,
)
from gain.effect_annotation.effect import (
    AlleleEffects,
    EffectGene,
    gene_effect_get_genes_worst,
)

EFFECT_GROUPS = [
    "LGDs", "LoF", "nonsynonymous", "coding", "introns", "UTRs", "CNVs"]


def test_get_effect_types_default_gives_types_without_groups() -> None:
    result = get_effect_types()

    assert result[0] == "tRNA:ANTICODON"
    assert result[-1] == "CNV+"
    assert len(result) == 25
    assert not set(EFFECT_GROUPS) & set(result)


def test_get_effect_types_with_groups_puts_groups_before_types() -> None:
    result = get_effect_types(types=True, groups=True)

    assert result == EFFECT_GROUPS + get_effect_types()


def test_get_effect_types_groups_only_gives_groups() -> None:
    result = get_effect_types(types=False, groups=True)

    assert result == EFFECT_GROUPS


def test_get_effect_types_neither_types_nor_groups_gives_empty() -> None:
    result = get_effect_types(types=False, groups=False)

    assert isinstance(result, list)
    assert not result


def test_get_effect_types_set_splits_string_on_comma() -> None:
    result = get_effect_types_set("missense,synonymous")

    assert result == {"missense", "synonymous"}


def test_get_effect_types_set_does_not_strip_whitespace() -> None:
    result = get_effect_types_set("missense, synonymous")

    assert result == {"missense", " synonymous"}


def test_get_effect_types_set_expands_group_names() -> None:
    result = get_effect_types_set("LGDs,CNVs,intergenic")

    assert result == {
        "splice-site",
        "frame-shift",
        "nonsense",
        "no-frame-shift-newStop",
        "CNV+",
        "CNV-",
        "intergenic",
    }


def test_get_effect_types_set_accepts_an_iterable() -> None:
    result = get_effect_types_set(["introns", "missense"])

    assert result == {
        "intron",
        "non-coding-intron",
        "5'UTR-intron",
        "3'UTR-intron",
        "missense",
    }


def test_gene_effect_get_genes_worst_of_none_is_empty() -> None:
    result = gene_effect_get_genes_worst(None)

    assert result == ""


def test_gene_effect_get_genes_worst_joins_sorted_unique_worst_genes() -> None:
    """Drop a duplicate, a gene without a symbol and a milder effect."""
    effects = AlleleEffects(
        "splice-site",
        [
            EffectGene("NUDT4B", "splice-site"),
            EffectGene("NUDT4", "splice-site"),
            EffectGene("NUDT4", "splice-site"),
            EffectGene("NUDT4P2", "non-coding-intron"),
            EffectGene(None, "splice-site"),
        ],
        {},
    )

    result = gene_effect_get_genes_worst(effects)

    assert result == "NUDT4;NUDT4B"
