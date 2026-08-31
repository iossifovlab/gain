# pylint: disable=C0114,C0116,W0212,W0621
"""``build_coverage_display`` at the payload, without a page around it.

Its siblings in ``test_coverage_fractions.py`` drive the whole render --
a repository, a built statistic, an info page -- because what they pin
is the denominator LADDER, which only a real resource can climb.  What
this file pins is the arithmetic at the top of that ladder, where the
inputs are two dicts, so the shapes a repository makes awkward to build
(a score with no values anywhere, a universe every length of which is
implausible) are one literal each.

The split matters for one shape in particular: a contig stored at ZERO
covered positions.  Only a bigWig produces one -- its scan visits every
contig the table declares -- so through the page it is reachable on one
backend and through this seam on any.
"""
from __future__ import annotations

import pytest
from gain.genomic_resources.statistics.coverage import (
    CoverageStatistics,
    RegionCoverage,
    UncoveredContigs,
    build_coverage_display,
)


def _a_statistic(covered: dict[str, int]) -> CoverageStatistics:
    """A resource-wide statistic holding exactly these counts.

    Folded from frozen regions, the shape a deserialized statistics file
    takes: no segment summary, so the payload's segment columns stay
    ``None`` and the rows carry only what is being asserted about.
    """
    statistics = CoverageStatistics()
    for chrom, count in covered.items():
        statistics.fold_region(RegionCoverage.frozen(chrom, count, None))
    return statistics


def test_the_universe_beyond_the_covered_contigs_is_the_denominator() -> None:
    display = build_coverage_display(
        "scores/one", _a_statistic({"chr1": 9}),
        {"chr1": 100, "chr2": 300})

    assert display.global_fraction == 9 / 400
    assert display.uncovered == UncoveredContigs(1, 300)


def test_a_contig_stored_at_zero_is_rolled_up_and_not_also_a_row() -> None:
    """The two reports of one contig must not both happen.

    A bigWig scan stores a zero for every contig its table declares and
    found nothing on; left in ``rows`` such a contig would render as a
    0.00% row AND be counted among the roll-up's contigs and base pairs.
    """
    display = build_coverage_display(
        "scores/one", _a_statistic({"chr1": 9, "chr2": 0}),
        {"chr1": 100, "chr2": 300})

    assert [row.chrom for row in display.rows] == ["chr1"]
    assert display.uncovered == UncoveredContigs(1, 300)


def test_a_score_with_no_values_anywhere_keeps_its_percent_column() -> None:
    # Every contig rolls up, so nothing is left to read a fraction off
    # a row; the section still resolved a denominator and still has a
    # percentage -- 0.00% -- to show against it.
    display = build_coverage_display(
        "scores/one", _a_statistic({"chr1": 0, "chr2": 0}),
        {"chr1": 100, "chr2": 300})

    assert display.rows == []
    assert display.global_fraction == 0.0
    assert display.uncovered == UncoveredContigs(2, 400)
    assert display.has_fractions


@pytest.mark.parametrize(("covered", "lengths"), [
    # A covered contig the universe does not bound: the resolved genome
    # is the wrong one, so its contigs are not reported as untouched.
    ({"chr1": 9, "chrX": 5}, {"chr1": 100, "chr2": 300}),
    # Every length implausible: chr1 shorter than what the score holds on
    # it, chr2 a zero-length .fai record.
    ({"chr1": 9}, {"chr1": 5, "chr2": 0}),
    # The bottom rung of the ladder: nothing resolved a length at all.
    ({"chr1": 9}, {}),
])
def test_a_universe_that_cannot_bound_the_score_rolls_nothing_up(
    covered: dict[str, int],
    lengths: dict[str, int],
) -> None:
    """No global fraction and no roll-up, for each way of failing.

    The degradation to raw counts is pinned end to end, page and all,
    in ``test_coverage_fractions.py``; what these add is that the
    roll-up is withheld ALONG WITH the fraction -- "these contigs have
    no values" is a claim about the universe being the right one, and a
    universe that cannot bound the score does not get to make it.

    Only the GLOBAL fraction is asserted away: the first case still
    bounds chr1, so its row keeps a percentage -- which is the next
    test's subject.
    """
    display = build_coverage_display(
        "scores/one", _a_statistic(covered), lengths)

    assert display.global_fraction is None
    assert display.uncovered is None


def test_a_row_the_universe_does_bound_keeps_its_own_fraction() -> None:
    # The degradation above is of the GLOBAL, not of every row.
    display = build_coverage_display(
        "scores/one", _a_statistic({"chr1": 9, "chrX": 5}),
        {"chr1": 100, "chr2": 300})

    assert [(row.chrom, row.fraction) for row in display.rows] == [
        ("chr1", 0.09), ("chrX", None)]


def test_a_one_contig_universe_has_nothing_to_roll_up() -> None:
    display = build_coverage_display(
        "scores/one", _a_statistic({"chr1": 9}), {"chr1": 100})

    assert display.global_fraction == 0.09
    assert display.uncovered is None
