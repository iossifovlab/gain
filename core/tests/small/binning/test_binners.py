# pylint: disable=W0621,C0114,C0116,W0212,W0613
import numpy as np
import pytest_mock
from gain.binning.binners import PositionScoreBinner, Track
from gain.genomic_resources.genomic_scores.position import PositionScore
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.utils.regions import BedRegion

BIN_SIZE = 10


def scores_one(aggregator: str, replacement: float | None = None) -> Track:
    """scores/one: 1.0 over chr1:1-20, 2.0 over chr1:31-35, nothing else."""
    return Track(
        name="scores/one", resource_id="scores/one", score_id="s",
        aggregator=aggregator, none_value_replacement=replacement,
        binner="position_score_binner")


def one_region(
    track: Track, region: BedRegion, repo: GenomicResourceRepo,
) -> np.ndarray:
    """The single array a one-region bundle yields.

    Binning is per region whatever the bundle, so the per-region rules
    below read at their own scale rather than through a bundle.
    """
    array, = PositionScoreBinner.bin_track(track, [region], BIN_SIZE, repo)
    return array


def test_a_track_bins_to_one_float64_value_per_grid_bin_nan_where_uncovered(
    repo: GenomicResourceRepo,
) -> None:
    # Under ``max`` bins 1-10 and 11-20 read 1.0, 21-30 has no record and
    # is NaN, and 31-40 reads 2.0 from its covered half.
    values = one_region(
        scores_one("max"), BedRegion("chr1", 1, 40), repo)

    assert values.dtype == np.float64
    np.testing.assert_array_equal(values, [1.0, 1.0, np.nan, 2.0])


def test_a_replacement_stands_in_for_every_uncovered_position(
    repo: GenomicResourceRepo,
) -> None:
    # With 0.0 standing in for the uncovered half of 31-40, its mean is
    # (2.0 * 5 + 0.0 * 5) / 10; the wholly uncovered 21-30 becomes 0.0.
    values = one_region(
        scores_one("mean", 0.0), BedRegion("chr1", 21, 40), repo)

    np.testing.assert_allclose(values, [0.0, 1.0])


def test_a_chromosome_the_score_never_mentions_is_wholly_uncovered(
    repo: GenomicResourceRepo,
) -> None:
    # scores/one has records on chr1 only.  A genome-wide run over a
    # track that skips a chromosome is the normal case, not an error: the
    # chromosome's bins are NaN like any other uncovered bin.
    values = one_region(
        scores_one("max"), BedRegion("chr2", 1, 25), repo)

    np.testing.assert_array_equal(values, [np.nan, np.nan, np.nan])


def test_a_replacement_covers_a_chromosome_the_score_never_mentions(
    repo: GenomicResourceRepo,
) -> None:
    values = one_region(
        scores_one("mean", 0.0), BedRegion("chr2", 1, 25), repo)

    np.testing.assert_array_equal(values, [0.0, 0.0, 0.0])


def test_a_bundle_yields_one_array_per_region_in_the_order_given(
    repo: GenomicResourceRepo,
) -> None:
    # A bundle is bins per region, not one run of bins: three regions of
    # different widths come back as three arrays, each the region's own,
    # in the order the bundle listed them -- chr2 before chr1 here, so a
    # yield order taken from the score rather than the bundle shows up.
    regions = [
        BedRegion("chr2", 1, 25), BedRegion("chr1", 1, 20),
        BedRegion("chr1", 31, 40),
    ]

    arrays = list(PositionScoreBinner.bin_track(
        scores_one("max"), regions, BIN_SIZE, repo))

    assert [len(a) for a in arrays] == [3, 2, 1]
    np.testing.assert_array_equal(arrays[0], [np.nan, np.nan, np.nan])
    np.testing.assert_array_equal(arrays[1], [1.0, 1.0])
    np.testing.assert_array_equal(arrays[2], [2.0])


def test_a_bundle_opens_the_score_once_however_many_regions(
    repo: GenomicResourceRepo, mocker: pytest_mock.MockerFixture,
) -> None:
    # The bundle, not the region, is what a resource is opened for: six
    # regions cost one open.  Opening per region is what this replaces,
    # and a bundle is a whole run's worth of regions at --task-budget 0.
    opens = mocker.spy(PositionScore, "open")
    regions = [
        BedRegion("chr1", start, start + 9)
        for start in (1, 11, 21, 31, 41, 51)
    ]

    arrays = list(PositionScoreBinner.bin_track(
        scores_one("max"), regions, BIN_SIZE, repo))

    assert opens.call_count == 1
    assert len(arrays) == 6
