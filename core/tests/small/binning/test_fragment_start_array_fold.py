# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""The array path of a ``fragment_start`` track, against the streaming fold.

A ``fragment_start`` track of a tabix fragment score reads column arrays
and folds them with :mod:`gain.binning.fragment_array_folds`.  The
streaming fold, :class:`BinFragmentStartAggregator`, is the oracle: the
tests force it by making ``supports_region_value_arrays`` answer
``False``, which is the path of a table without an array read.
"""
import heapq
import operator
import pathlib
from typing import Any

import numpy as np
import pytest
import pytest_mock
from gain.binning.binners import discover_binner_kinds
from gain.binning.fragment_array_folds import (
    FragmentBatch,
    FragmentStartArrayFold,
    pooled_batches,
)
from gain.binning.fragment_binner import FragmentScoreBinding
from gain.binning.fragment_folds import (
    BinFragmentStartAggregator,
    FragmentValue,
)
from gain.genomic_resources.genomic_scores import FragmentScore
from gain.genomic_resources.reference_genome import ReferenceGenome
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing.builders import a_fragment_score, a_grr
from gain.utils.regions import BedRegion

from tests.small.binning.test_fragment_binner import (
    bin_regions,
    grouped_s1,
    parse_fragment_entry,
    pooled,
)

AGGREGATORS = ["count", "sum", "mean", "max", "min", "median", "product"]


@pytest.mark.parametrize("aggregator", AGGREGATORS)
def test_a_fragment_start_track_reads_column_arrays(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
    mocker: pytest_mock.MockerFixture, aggregator: str,
) -> None:
    arrays = mocker.spy(
        FragmentScore, "get_fragment_arrays_starting_in_region")
    records = mocker.spy(
        FragmentScore, "get_fragment_scores_starting_in_region")
    (job,) = parse_fragment_entry(
        pooled(value={"score_id": "count"},
               aggregate={"aggregator": aggregator}),
        repo, genome).jobs

    bin_regions(job, [BedRegion("chr1", 1, 40)], repo)

    assert arrays.call_count == 2
    assert records.call_count == 0


def streaming(mocker: pytest_mock.MockerFixture) -> None:
    """Force the streaming fold: no resource offers an array read."""
    mocker.patch.object(
        FragmentScore, "supports_region_value_arrays", return_value=False)


ENTRIES = {
    "plain": {"resource_query": "frags/s1", "pool": False},
    "s2-count": {"resource_query": "frags/s2", "pool": False,
                 "value": {"score_id": "count"}},
    "grouped-count": grouped_s1(value={"score_id": "count"}),
    "pooled-count": pooled(value={"score_id": "count"}),
    "pooled-constant": pooled(value={"value": 2.5}),
}
REGIONS = [
    BedRegion("chr1", 1, 40), BedRegion("chr1", 5, 44),
    BedRegion("chr1", 1, 100), BedRegion("chr2", 1, 40)]


def blocks_of(
    entry: dict[str, Any], aggregator: str,
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> list[np.ndarray]:
    (job,) = parse_fragment_entry(
        {**entry, "aggregate": {"aggregator": aggregator}},
        repo, genome).jobs
    return bin_regions(job, REGIONS, repo)


@pytest.mark.parametrize("aggregator", AGGREGATORS)
@pytest.mark.parametrize("entry", ENTRIES.values(), ids=ENTRIES.keys())
def test_the_array_values_equal_the_streaming_fold(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
    mocker: pytest_mock.MockerFixture,
    entry: dict[str, Any], aggregator: str,
) -> None:
    arrays = blocks_of(entry, aggregator, repo, genome)
    streaming(mocker)
    expected = blocks_of(entry, aggregator, repo, genome)

    for block, reference in zip(arrays, expected, strict=True):
        assert block.dtype == reference.dtype == np.float64
        assert block.shape == reference.shape
        assert np.array_equal(block, reference, equal_nan=True)


#: The values of the fuzz: zeros, values that make a product overflow
#: or underflow, infinities and no value.
FUZZ_VALUES = [
    0.0, -0.0, 1.0, 2.5, -3.0, 1e200, -1e200, 1e-200, np.inf, -np.inf,
    np.nan]
FUZZ_REGION = (7, 93)
FUZZ_BIN_SIZE = 10
FUZZ_TRACKS = 3


def random_reads(
    rng: np.random.Generator,
) -> list[list[tuple[int, int, float]]]:
    """Each read's ``(start, track, value)`` fragments, in start order.

    The starts come from a narrow range, so starts repeat inside a read
    and across reads.  The track ``-1`` is a barcode with no track.
    """
    start, end = FUZZ_REGION
    reads = []
    for _ in range(rng.integers(1, 4)):
        size = int(rng.integers(0, 60))
        starts = np.sort(rng.integers(start, end + 1, size))
        tracks = rng.integers(-1, FUZZ_TRACKS, size)
        values = rng.choice(FUZZ_VALUES, size)
        reads.append([
            (int(s), int(t), float(v))
            for s, t, v in zip(starts, tracks, values, strict=True)])
    return reads


def in_batches(
    rng: np.random.Generator, read: list[tuple[int, int, float]],
) -> list[FragmentBatch]:
    """A read cut into batches of random sizes, none of zero rows."""
    batches = []
    while read:
        size = int(rng.integers(1, 8))
        rows, read = read[:size], read[size:]
        batches.append((
            np.array([row[0] for row in rows], dtype=np.int64),
            np.array([row[1] for row in rows], dtype=np.int64),
            np.array([row[2] for row in rows], dtype=np.float64)))
    return batches


def streaming_block(
    reads: list[list[tuple[int, int, float]]], aggregator: str,
) -> np.ndarray:
    """The block of a streaming fold per track, fed by ``heapq.merge``."""
    start, end = FUZZ_REGION
    folds = [
        BinFragmentStartAggregator(
            start=start, end=end, bin_size=FUZZ_BIN_SIZE,
            aggregator=aggregator)
        for _ in range(FUZZ_TRACKS)]
    columns: list[list[float]] = [[] for _ in range(FUZZ_TRACKS)]
    for begin, track, value in heapq.merge(
            *reads, key=operator.itemgetter(0)):
        if track < 0:
            continue
        bins = folds[track].feed(FragmentValue(
            begin, begin, None if np.isnan(value) else value))
        columns[track].extend(bin_value.value for bin_value in bins)
    for track, fold in enumerate(folds):
        columns[track].extend(bin_value.value for bin_value in fold.flush())
    return np.array(columns).T


@pytest.mark.parametrize("aggregator", AGGREGATORS)
@pytest.mark.parametrize("seed", range(40))
def test_random_fragments_fold_as_the_streaming_fold(
    aggregator: str, seed: int,
) -> None:
    rng = np.random.default_rng(seed)
    reads = random_reads(rng)
    start, end = FUZZ_REGION
    fold = FragmentStartArrayFold(
        start=start, end=end, bin_size=FUZZ_BIN_SIZE,
        aggregators=[aggregator] * FUZZ_TRACKS)

    for batch in pooled_batches([in_batches(rng, read) for read in reads]):
        fold.feed(batch)

    assert np.array_equal(
        fold.result(), streaming_block(reads, aggregator), equal_nan=True)


#: An ``int`` score past 2**53.  Bin 1-10 holds 2**53, 1 and 1, and bin
#: 11-20 holds 2**53 + 1 and 3.
BIG_INTS = f"""
    chrom  pos_begin  pos_end  count
    chr1   1          5        {2**53}
    chr1   2          5        1
    chr1   3          5        1
    chr1   11         15       {2**53 + 1}
    chr1   12         15       3
"""


@pytest.fixture
def big_int_repo(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    return a_grr().with_resource(
        "frags/big", a_fragment_score()
        .with_score("count", "int")
        .with_tabix()
        .with_data(BIG_INTS),
    ).build_repo(tmp_path / "grr")


@pytest.mark.parametrize("aggregator,arrays,exact", [
    # The array path adds float64 values, and 2**53 + 1 rounds to 2**53.
    ("sum", float(2**53), float(2**53 + 2)),
    ("mean", 2**53 / 3, (2**53 + 2) / 3),
    # The array path reads 2**53 + 1 as 2**53.
    ("product", float(2**53) * 3, float((2**53 + 1) * 3)),
])
def test_an_int_score_past_2_53_is_the_one_allowed_difference(
    big_int_repo: GenomicResourceRepo, genome: ReferenceGenome,
    mocker: pytest_mock.MockerFixture,
    aggregator: str, arrays: float, exact: float,
) -> None:
    bin_index = 1 if aggregator == "product" else 0
    entry = {
        "resource_query": "frags/big", "pool": False,
        "value": {"score_id": "count"},
        "aggregate": {"aggregator": aggregator}}
    (job,) = parse_fragment_entry(entry, big_int_repo, genome).jobs
    region = BedRegion("chr1", 1, 20)

    (array_block,) = bin_regions(job, [region], big_int_repo)
    streaming(mocker)
    (streaming_block_,) = bin_regions(job, [region], big_int_repo)

    assert arrays != exact
    assert array_block[bin_index, 0] == arrays
    assert streaming_block_[bin_index, 0] == exact


def dropped_and_logged(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
    caplog: pytest.LogCaptureFixture,
) -> tuple[dict[tuple[str, str], int], list[str]]:
    """The ``dropped`` counts of a pooled grouped job, and its log lines."""
    (job,) = parse_fragment_entry(pooled(), repo, genome).jobs
    caplog.clear()
    with caplog.at_level("INFO", logger="gain.binning.fragment_binner"), \
            discover_binner_kinds()[job.binner].bind(job, repo) as bound:
        assert isinstance(bound, FragmentScoreBinding)
        for region in [BedRegion("chr1", 1, 40), BedRegion("chr2", 1, 40)]:
            bound.bin_region(region, 10)
        return dict(bound.dropped), [
            record.getMessage() for record in caplog.records
            if record.name == "gain.binning.fragment_binner"]


def test_the_dropped_counts_and_their_log_lines_do_not_change(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
    mocker: pytest_mock.MockerFixture, caplog: pytest.LogCaptureFixture,
) -> None:
    # On chr1, CCC (an empty class) and DDD (not in the table) of
    # frags/s1 reach no track.
    arrays = dropped_and_logged(repo, genome, caplog)
    streaming(mocker)
    expected = dropped_and_logged(repo, genome, caplog)

    assert arrays == expected
    assert arrays[0] == {
        ("frags/s1", "chr1:1-40"): 2, ("frags/s2", "chr1:1-40"): 0,
        ("frags/s1", "chr2:1-40"): 0, ("frags/s2", "chr2:1-40"): 0}
    assert len(arrays[1]) == 4
