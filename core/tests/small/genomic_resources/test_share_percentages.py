# pylint: disable=C0114,C0116,W0212,W0621
"""The one rule for writing a share of a whole (gain#1057).

Pinned here at the scalar seam rather than through either of its two
callers, because what the rule answers is a question about two
integers: the Alleles tables reach it through ``percentages_over``'s
map contract and the Coverage table through ``CoverageRow``, and
neither of those seams can state the boundaries without a resource
around it.
"""
from __future__ import annotations

from gain.genomic_resources.statistics.percentages import percentage_of


def test_a_nonzero_share_too_small_for_two_decimals_is_floored() -> None:
    """``0.00%`` beside a genuinely empty ``0.00%`` hides the difference."""
    assert percentage_of(1, 1_000_000) == "<0.01%"


def test_a_share_short_of_the_whole_that_rounds_up_is_capped() -> None:
    """``100.00%`` for all-but-one says the whole in the act of not being it."""
    assert percentage_of(999_999, 1_000_000) == ">99.99%"


def test_an_empty_share_stays_exactly_zero() -> None:
    """The floor is for a share too small to show, not for none at all."""
    assert percentage_of(0, 1_000_000) == "0.00%"


def test_a_whole_share_stays_exactly_the_whole() -> None:
    """The cap is for a share short of the whole, not for the whole."""
    assert percentage_of(1_000_000, 1_000_000) == "100.00%"
