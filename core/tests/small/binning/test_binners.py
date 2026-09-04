# pylint: disable=W0621,C0114,C0116,W0212,W0613
import numpy as np
from gain.binning.binners import PositionScoreBinner, Track, grid_bins
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.utils.regions import BedRegion

BIN_SIZE = 10


def test_grid_bins_are_anchored_at_one_and_clipped_to_the_region() -> None:
    # The grid is global (1-10, 11-20, ...), so a window starting at 5 gets
    # a clipped first bin and a window ending at 24 a clipped last one --
    # and bins from different runs tile.
    region = BedRegion("chr1", 5, 24)

    bins = grid_bins(region, BIN_SIZE)

    assert bins == [(5, 10), (11, 20), (21, 24)]


def test_a_track_bins_to_one_float64_value_per_grid_bin_nan_where_uncovered(
    repo: GenomicResourceRepo,
) -> None:
    # scores/one: 1.0 over 1-20, 2.0 over 31-35, nothing else.  Under
    # ``max`` bins 1-10 and 11-20 read 1.0, 21-30 has no record and is
    # NaN, and 31-40 reads 2.0 from its covered half.
    track = Track(
        name="scores/one", resource_id="scores/one", score_id="s",
        aggregator="max", none_value_replacement=None,
        binner="position_score_binner")

    values = PositionScoreBinner.bin_track(
        track, BedRegion("chr1", 1, 40), BIN_SIZE, repo)

    assert values.dtype == np.float64
    np.testing.assert_array_equal(values, [1.0, 1.0, np.nan, 2.0])


def test_a_replacement_stands_in_for_every_uncovered_position(
    repo: GenomicResourceRepo,
) -> None:
    # With 0.0 standing in for the uncovered half of 31-40, its mean is
    # (2.0 * 5 + 0.0 * 5) / 10; the wholly uncovered 21-30 becomes 0.0.
    track = Track(
        name="scores/one", resource_id="scores/one", score_id="s",
        aggregator="mean", none_value_replacement=0.0,
        binner="position_score_binner")

    values = PositionScoreBinner.bin_track(
        track, BedRegion("chr1", 21, 40), BIN_SIZE, repo)

    np.testing.assert_allclose(values, [0.0, 1.0])


def test_a_chromosome_the_score_never_mentions_is_wholly_uncovered(
    repo: GenomicResourceRepo,
) -> None:
    # scores/one has records on chr1 only.  A genome-wide run over a
    # track that skips a chromosome is the normal case, not an error: the
    # chromosome's bins are NaN like any other uncovered bin.
    track = Track(
        name="scores/one", resource_id="scores/one", score_id="s",
        aggregator="max", none_value_replacement=None,
        binner="position_score_binner")

    values = PositionScoreBinner.bin_track(
        track, BedRegion("chr2", 1, 25), BIN_SIZE, repo)

    np.testing.assert_array_equal(values, [np.nan, np.nan, np.nan])


def test_a_replacement_covers_a_chromosome_the_score_never_mentions(
    repo: GenomicResourceRepo,
) -> None:
    track = Track(
        name="scores/one", resource_id="scores/one", score_id="s",
        aggregator="mean", none_value_replacement=0.0,
        binner="position_score_binner")

    values = PositionScoreBinner.bin_track(
        track, BedRegion("chr2", 1, 25), BIN_SIZE, repo)

    np.testing.assert_array_equal(values, [0.0, 0.0, 0.0])
