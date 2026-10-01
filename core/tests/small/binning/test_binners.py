# pylint: disable=W0621,C0114,C0116,W0212,W0613
import numpy as np
import pytest
import pytest_mock
from gain.binning.binners import BinningJob, PositionScoreBinner, Track
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


def a_job(track: Track) -> BinningJob:
    return BinningJob(binner=track.binner, tracks=(track,))


def one_region(
    track: Track, region: BedRegion, repo: GenomicResourceRepo,
) -> np.ndarray:
    """The block of a one-region binding, as the track's one column."""
    with PositionScoreBinner.bind(a_job(track), repo) as bound:
        block = bound.bin_region(region, BIN_SIZE)
    assert block.shape == (len(block), 1)
    return block[:, 0]


def test_a_track_bins_to_one_float64_value_per_grid_bin_nan_where_uncovered(
    repo: GenomicResourceRepo,
) -> None:
    # Under ``max`` bins 1-10 and 11-20 read 1.0, 21-30 has no record and
    # is NaN, and 31-40 reads 2.0 from its covered half.
    values = one_region(
        scores_one("max"), BedRegion("chr1", 1, 40), repo)

    assert values.dtype == np.float64
    np.testing.assert_array_equal(values, [1.0, 1.0, np.nan, 2.0])


def test_a_sum_track_adds_each_bin_by_base_pair(
    repo: GenomicResourceRepo,
) -> None:
    # 1.0 over each of 1-10 and 11-20, nothing in 21-30, 2.0 over the
    # five covered positions of 31-40.
    values = one_region(
        scores_one("sum"), BedRegion("chr1", 1, 40), repo)

    assert values.dtype == np.float64
    np.testing.assert_array_equal(values, [10.0, 10.0, np.nan, 10.0])


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


def test_a_binding_returns_each_region_its_own_one_column_block(
    repo: GenomicResourceRepo,
) -> None:
    # A bundle is bins per region, not one run of bins: three regions of
    # different widths come back as three blocks, each the region's own,
    # in the order asked -- chr2 before chr1 here, so an answer taken
    # from the score's order rather than the caller's shows up.
    regions = [
        BedRegion("chr2", 1, 25), BedRegion("chr1", 1, 20),
        BedRegion("chr1", 31, 40),
    ]

    with PositionScoreBinner.bind(a_job(scores_one("max")), repo) as bound:
        blocks = [bound.bin_region(region, BIN_SIZE) for region in regions]

    assert [b.shape for b in blocks] == [(3, 1), (2, 1), (1, 1)]
    assert all(b.dtype == np.float64 for b in blocks)
    np.testing.assert_array_equal(blocks[0], [[np.nan]] * 3)
    np.testing.assert_array_equal(blocks[1], [[1.0], [1.0]])
    np.testing.assert_array_equal(blocks[2], [[2.0]])


def test_a_binding_closes_the_score_when_the_with_ends(
    repo: GenomicResourceRepo,
) -> None:
    with PositionScoreBinner.bind(a_job(scores_one("max")), repo) as bound:
        bound.bin_region(BedRegion("chr1", 1, 10), BIN_SIZE)
        score = bound.score
        assert score.is_open()

    assert not score.is_open()


def test_a_binding_closes_the_score_when_the_with_fails(
    repo: GenomicResourceRepo, mocker: pytest_mock.MockerFixture,
) -> None:
    # A task that fails mid-bundle -- a chunk it cannot save -- leaves the
    # binding through an exception, and the score is closed on that path
    # as on a normal exit.
    closes = mocker.spy(PositionScore, "close")

    def fail_inside_the_binding() -> None:
        with PositionScoreBinner.bind(
                a_job(scores_one("max")), repo) as bound:
            bound.bin_region(BedRegion("chr1", 1, 10), BIN_SIZE)
            raise OSError("chunk not saved")

    with pytest.raises(OSError, match="chunk not saved"):
        fail_inside_the_binding()

    assert closes.call_count == 1


def test_a_binding_opens_the_score_once_however_many_regions(
    repo: GenomicResourceRepo, mocker: pytest_mock.MockerFixture,
) -> None:
    # The binding, not the region, is what a resource is opened for: six
    # regions cost one open.  A bundle is a whole run's worth of regions
    # at --task-budget 0.
    opens = mocker.spy(PositionScore, "open")
    regions = [
        BedRegion("chr1", start, start + 9)
        for start in (1, 11, 21, 31, 41, 51)
    ]

    with PositionScoreBinner.bind(a_job(scores_one("max")), repo) as bound:
        blocks = [bound.bin_region(region, BIN_SIZE) for region in regions]

    assert opens.call_count == 1
    assert len(blocks) == 6
