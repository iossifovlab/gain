# pylint: disable=C0114,C0116
import pytest
from gain.utils.cnv_utils import cshl2cnv_variant


@pytest.mark.parametrize("variant, expected_type", [
    ("CNV+", "LARGE_DUPLICATION"),
    ("duplication", "LARGE_DUPLICATION"),
    ("large_insertion", "LARGE_DUPLICATION"),
    ("Gain", "LARGE_DUPLICATION"),
    ("cnv-", "LARGE_DELETION"),
    ("Deletion", "LARGE_DELETION"),
    ("large_deletion", "LARGE_DELETION"),
    ("LOSS", "LARGE_DELETION"),
])
def test_cshl2cnv_variant_parses_the_location_and_the_type(
    variant: str, expected_type: str,
) -> None:
    assert cshl2cnv_variant("chr2:100-2000", variant) == (
        "chr2", 100, 2000, expected_type)


@pytest.mark.parametrize("location", [
    "chr2", "chr2:100:200", "chr2:100", "chr2:100-200-300",
])
def test_cshl2cnv_variant_refuses_a_bad_location(location: str) -> None:
    with pytest.raises(ValueError, match="unexpected location format"):
        cshl2cnv_variant(location, "CNV+")


def test_cshl2cnv_variant_refuses_an_unknown_type() -> None:
    with pytest.raises(ValueError, match="unexpected CNV variant type: ins"):
        cshl2cnv_variant("chr2:100-200", "ins")
