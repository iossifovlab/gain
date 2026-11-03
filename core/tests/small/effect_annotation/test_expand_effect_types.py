# pylint: disable=C0116
"""Pin the behavior of ``expand_effect_types``, which gpf calls."""
from gain.effect_annotation.effect import expand_effect_types


def test_expand_effect_types_expands_a_group_name_given_as_a_string() -> None:
    result = expand_effect_types("LGDs")

    assert result == [
        "frame-shift", "nonsense", "splice-site", "no-frame-shift-newStop",
    ]


def test_expand_effect_types_maps_a_ui_name_to_its_effect_types() -> None:
    result = expand_effect_types("Intron")

    assert result == ["intron", "non-coding-intron"]


def test_expand_effect_types_keeps_an_unknown_name_as_given() -> None:
    result = expand_effect_types("not-an-effect")

    assert result == ["not-an-effect"]


def test_expand_effect_types_expands_each_string_of_a_list_in_order() -> None:
    result = expand_effect_types(["CNV", "3'-UTR", "missense"])

    assert result == ["CNV+", "CNV-", "3'UTR", "3'UTR-intron", "missense"]


def test_expand_effect_types_flattens_a_list_of_lists() -> None:
    result = expand_effect_types([["UTRs"], ["Synonymous", "Nonsense"]])

    assert result == [
        "3'UTR", "5'UTR", "3'UTR-intron", "5'UTR-intron",
        "synonymous", "nonsense",
    ]


def test_expand_effect_types_keeps_a_group_member_that_has_no_mapping() -> (
    None
):
    result = expand_effect_types("noncoding")

    assert result == ["non coding", "intron", "intergenic", "3'UTR", "5'UTR"]


def test_expand_effect_types_returns_an_empty_list_for_no_groups() -> None:
    result = expand_effect_types([])

    assert isinstance(result, list)
    assert not result
