# pylint: disable=C0116
"""Pin ``trim_str_left_right`` and ``trim_str_right_left``, which gpf calls."""
from collections.abc import Callable

import pytest
from gain.utils.variant_utils import trim_str_left_right, trim_str_right_left

TRIMS = pytest.mark.parametrize(
    "trim", [trim_str_left_right, trim_str_right_left])
Trim = Callable[[int, str, str], tuple[int, str, str]]


@TRIMS
@pytest.mark.parametrize("pos,ref,alt", [
    (10, "", "A"),
    (10, "A", ""),
    (10, "", ""),
])
def test_trim_returns_an_empty_allele_unchanged(
    trim: Trim, pos: int, ref: str, alt: str,
) -> None:
    assert trim(pos, ref, alt) == (pos, ref, alt)


@TRIMS
@pytest.mark.parametrize("pos,ref,alt,expected", [
    (10, "C", "G", (10, "C", "G")),
    (10, "AC", "AG", (11, "C", "G")),
    (10, "CA", "GA", (10, "C", "G")),
    (10, "TACG", "TGCG", (11, "A", "G")),
])
def test_trim_trims_both_ends(
    trim: Trim, pos: int, ref: str, alt: str,
    expected: tuple[int, str, str],
) -> None:
    assert trim(pos, ref, alt) == expected


def test_trim_str_left_right_stops_when_the_left_trim_empties_an_allele(
) -> None:
    assert trim_str_left_right(10, "AC", "ACT") == (12, "", "T")


def test_trim_str_right_left_stops_when_the_right_trim_empties_an_allele(
) -> None:
    assert trim_str_right_left(10, "GAC", "AC") == (10, "G", "")


def test_trim_order_decides_the_position_of_a_repeat_deletion() -> None:
    assert trim_str_left_right(10, "AA", "A") == (11, "A", "")
    assert trim_str_right_left(10, "AA", "A") == (10, "A", "")


@pytest.mark.parametrize("pos,ref,alt,expected", [
    (10, "AC", "AGC", (11, "", "G")),
    (10, "AGC", "AC", (11, "G", "")),
])
def test_trim_str_right_left_trims_left_after_one_base_is_left(
    pos: int, ref: str, alt: str, expected: tuple[int, str, str],
) -> None:
    assert trim_str_right_left(pos, ref, alt) == expected
