# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""The array fold of ``fragment_start`` tracks against the streaming fold.

The streaming fold, :class:`BinFragmentStartAggregator` fed the
``heapq.merge`` of the record reads, is the oracle: the array path answers
the same ``/values`` matrix, bit for bit, for each aggregator.
"""
import heapq
import logging
import math
import operator
import pathlib
from collections.abc import Generator
from typing import Any

import numpy as np
import pytest
import pytest_mock
from gain.binning import fragment_binner
from gain.binning.binners import discover_binner_kinds
from gain.binning.fragment_array_folds import (
    LengthRead,
    StartRead,
    _windows,
    fold_fragment_lengths,
    fold_fragment_starts,
)
from gain.binning.fragment_binner import FragmentScoreBinding
from gain.binning.fragment_folds import (
    BinFragmentLengthAggregator,
    BinFragmentStartAggregator,
    FragmentValue,
)
from gain.binning.run_definition import parse_run_definition
from gain.genomic_resources.genomic_scores import FragmentScore
from gain.genomic_resources.genomic_scores.aggregation import (
    EMPTY_BIN_VALUES,
)
from gain.genomic_resources.reference_genome import ReferenceGenome
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing.builders import a_fragment_score, a_grr
from gain.utils.regions import BedRegion

BIN_SIZE = 10
KIND = "fragment_score_binner"
AGGREGATORS = ["count", "sum", "mean", "max", "min", "median", "product"]


def bound_block(
    entry: dict[str, Any], region: BedRegion,
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> tuple[np.ndarray, dict[tuple[str, str], int]]:
    run = parse_run_definition({
        "bins": {"bin_size": BIN_SIZE},
        "binners": [{KIND: entry}],
    }, repo, genome)
    (job,) = run.jobs
    binner = discover_binner_kinds()[job.binner]
    with binner.bind(job, repo) as bound:
        assert isinstance(bound, FragmentScoreBinding)
        block = bound.bin_region(region, BIN_SIZE)
        return block, bound.dropped


@pytest.mark.parametrize("aggregator", AGGREGATORS)
def test_a_fragment_start_track_reads_the_array_reader(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
    mocker: pytest_mock.MockerFixture, aggregator: str,
) -> None:
    arrays = mocker.spy(
        FragmentScore, "get_fragment_arrays_starting_in_region")
    records = mocker.spy(
        FragmentScore, "get_fragment_scores_starting_in_region")

    bound_block(
        {"resource_query": "frags/*", "value": {"score_id": "count"},
         "aggregate": {"aggregator": aggregator}},
        BedRegion("chr1", 1, 40), repo, genome)

    assert arrays.call_count == 2
    assert records.call_count == 0


BY_CLASS = {
    "resource_query": "frags/*",
    "group": {"cell_score_id": "cell", "cell_meta_column": "barcode",
              "group_meta_column": "class"},
    "meta": {"resource_id": "meta/cells",
             "filter": [{"column": "sample_id", "label": "sample_id"}]},
}
REGIONS = [
    BedRegion("chr1", 1, 40), BedRegion("chr1", 5, 44),
    BedRegion("chr2", 1, 40)]


def streaming_block(
    entry: dict[str, Any], region: BedRegion,
    repo: GenomicResourceRepo, genome: ReferenceGenome,
    mocker: pytest_mock.MockerFixture,
) -> tuple[np.ndarray, dict[tuple[str, str], int]]:
    """The block of the streaming fold: a score without array reads."""
    no_arrays = without_array_reads(mocker)
    folds = spy_array_folds(mocker)
    try:
        result = bound_block(entry, region, repo, genome)
    finally:
        mocker.stop(no_arrays)
        for fold in folds:
            mocker.stop(fold)
    assert sum(fold.call_count for fold in folds) == 0
    return result


def array_block(
    entry: dict[str, Any], region: BedRegion,
    repo: GenomicResourceRepo, genome: ReferenceGenome,
    mocker: pytest_mock.MockerFixture,
) -> tuple[np.ndarray, dict[tuple[str, str], int]]:
    """The block of the array path, which must fold the region once."""
    folds = spy_array_folds(mocker)
    try:
        result = bound_block(entry, region, repo, genome)
    finally:
        for fold in folds:
            mocker.stop(fold)
    assert sum(fold.call_count for fold in folds) == 1
    return result


def spy_array_folds(mocker: pytest_mock.MockerFixture) -> list[Any]:
    """Spy on the two array folds of the binner."""
    return [
        mocker.spy(fragment_binner, "fold_fragment_starts"),
        mocker.spy(fragment_binner, "fold_fragment_lengths")]


def without_array_reads(mocker: pytest_mock.MockerFixture) -> Any:
    """Make every fragment score refuse the column-array read."""
    return mocker.patch.object(
        FragmentScore, "supports_region_value_arrays", return_value=False)


@pytest.mark.parametrize("aggregator", AGGREGATORS)
@pytest.mark.parametrize("entry", [
    {"resource_query": "frags/*"},
    {"resource_query": "frags/s1", "pool": False,
     "value": {"value": 2.5}},
    BY_CLASS,
    {**BY_CLASS, "value": {"score_id": "count"}},
])
@pytest.mark.parametrize("region", REGIONS, ids=str)
def test_the_array_path_answers_the_streaming_fold_on_the_fixtures(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
    mocker: pytest_mock.MockerFixture,
    aggregator: str, entry: dict[str, Any], region: BedRegion,
) -> None:
    entry = {**entry, "aggregate": {"aggregator": aggregator}}
    expected, _ = streaming_block(entry, region, repo, genome, mocker)

    block, _ = array_block(entry, region, repo, genome, mocker)

    assert block.dtype == expected.dtype
    assert block.shape == expected.shape
    assert np.array_equal(block, expected, equal_nan=True)


@pytest.mark.parametrize("aggregator", AGGREGATORS)
def test_a_region_on_a_contig_that_no_resource_has_holds_empty_bins(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
    mocker: pytest_mock.MockerFixture, aggregator: str,
) -> None:
    # frags/s2 has no chr2, so the array path folds an empty list of reads.
    entry = {"resource_query": "frags/s2",
             "aggregate": {"aggregator": aggregator}}
    region = BedRegion("chr2", 1, 40)
    expected, _ = streaming_block(entry, region, repo, genome, mocker)

    block, _ = array_block(entry, region, repo, genome, mocker)

    assert block.shape == (4, 1)
    assert np.array_equal(
        block, np.full((4, 1), EMPTY_BIN_VALUES[aggregator]),
        equal_nan=True)
    assert np.array_equal(block, expected, equal_nan=True)


def test_the_dropped_counts_and_their_log_lines_match_the_streaming_fold(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
    mocker: pytest_mock.MockerFixture, caplog: pytest.LogCaptureFixture,
) -> None:
    def run(block_of: Any) -> tuple[
            list[dict[tuple[str, str], int]], list[str]]:
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="gain.binning"):
            dropped = [
                block_of(BY_CLASS, region, repo, genome, mocker)[1]
                for region in REGIONS]
        return dropped, [r.getMessage() for r in caplog.records
                         if "dropped" in r.getMessage()]
    expected = run(streaming_block)

    dropped, logged = run(array_block)

    assert dropped == expected[0]
    assert logged == expected[1]
    assert dropped[0] == {
        ("frags/s1", "chr1:1-40"): 2, ("frags/s2", "chr1:1-40"): 0}


#: The barcodes of the fuzz fragments, with their tracks; ``ZZZ`` and a
#: missing barcode reach no track.
FUZZ_TRACKS = {"AAA": 0, "BBB": 1, "CCC": 0, "DDD": 2}
FUZZ_BARCODES = [*FUZZ_TRACKS, "ZZZ", None]
#: Values that hit the traps of a product: zeros, an overflow to an
#: infinity, an underflow to 0 that a later infinity would turn into NaN,
#: NaN, which is no value, and subnormal values, which a product
#: multiplies one value at a time.
FUZZ_VALUES = [
    0.0, -0.0, 1.5, -2.25, 3.0, 1e200, -1e200, 1e-200, -1e-200,
    np.inf, -np.inf, np.nan, 7.0, 0.5, 7.40148500012542e-309, -3e-310,
    0.3, -6.318824842378611e+265]

Fragment = tuple[int, str | None, float]


def random_reads(
    rng: np.random.Generator, start: int, end: int,
) -> list[list[Fragment]]:
    """Two to four resources of fragments sorted by start.

    The starts come from a few positions, so equal starts in different
    resources and runs of one start inside a resource are common.
    """
    positions = rng.choice(np.arange(start, end + 1), size=12)
    reads = []
    for _ in range(rng.integers(2, 5)):
        size = int(rng.integers(0, 40))
        starts = np.sort(rng.choice(positions, size=size))
        reads.append([
            (int(begin),
             FUZZ_BARCODES[rng.integers(len(FUZZ_BARCODES))],
             float(FUZZ_VALUES[rng.integers(len(FUZZ_VALUES))]))
            for begin in starts])
    return reads


def batches_of(
    rng: np.random.Generator, fragments: list[Fragment],
) -> Generator[tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]],
               None, None]:
    """The fragments in batches of random sizes, as the array reader."""
    position = 0
    while position < len(fragments):
        size = int(rng.integers(1, 6))
        batch = fragments[position:position + size]
        position += size
        begins = np.array([begin for begin, _, _ in batch], dtype=np.int64)
        yield begins, begins + 5, {
            "value": np.array([value for _, _, value in batch]),
            "cell": np.array([cell for _, cell, _ in batch], dtype=object),
        }


def streaming_fold(
    reads: list[list[Fragment]], *,
    start: int, end: int, aggregator: str,
) -> np.ndarray:
    """The oracle: one streaming fold per track over the merged reads."""
    folds = [
        BinFragmentStartAggregator(
            start=start, end=end, bin_size=BIN_SIZE, aggregator=aggregator)
        for _ in range(3)]
    columns: list[list[float]] = [[] for _ in folds]
    for begin, cell, value in heapq.merge(
            *reads, key=operator.itemgetter(0)):
        track = FUZZ_TRACKS.get(cell or "")
        if track is None:
            continue
        no_value = np.isnan(value)
        columns[track].extend(
            bin_value.value for bin_value in folds[track].feed(
                FragmentValue(begin, begin + 5,
                              None if no_value else value)))
    for column, fold in zip(columns, folds, strict=True):
        column.extend(bin_value.value for bin_value in fold.flush())
    return np.array(columns, dtype=np.float64).T


@pytest.mark.parametrize("aggregator", AGGREGATORS)
@pytest.mark.parametrize("seed", range(40))
def test_the_array_fold_answers_the_streaming_fold_on_random_fragments(
    aggregator: str, seed: int,
) -> None:
    rng = np.random.default_rng(seed)
    start = int(rng.integers(1, 30))
    end = start + int(rng.integers(0, 60))
    reads = random_reads(rng, start, end)
    expected = streaming_fold(
        reads, start=start, end=end, aggregator=aggregator)
    starts = [
        StartRead(
            batches_of(rng, fragments), value_score="value",
            group_score="cell", track_of=FUZZ_TRACKS.get)
        for fragments in reads]

    block = fold_fragment_starts(
        starts, start=start, end=end, bin_size=BIN_SIZE, tracks=3,
        aggregator=aggregator)

    assert np.array_equal(block, expected, equal_nan=True)
    assert np.array_equal(np.signbit(block), np.signbit(expected))
    assert [read.dropped for read in starts] == [
        sum(FUZZ_TRACKS.get(cell or "") is None for _, cell, _ in fragments)
        for fragments in reads]


@pytest.mark.parametrize("aggregator,counts", [
    ("sum", [2**53, 1, 1]),
    ("mean", [2**53, 1, 1]),
    ("product", [2**27 + 1] * 3),
])
def test_an_int_score_past_2_53_is_folded_as_float64_on_the_array_path(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
    mocker: pytest_mock.MockerFixture, aggregator: str, counts: list[int],
) -> None:
    # The one allowed difference: the record read keeps an int score
    # exact, the array read gives float64, and the two fold differently
    # once an exact result needs more than 53 bits.
    data = "chrom pos_begin pos_end cell count\n" + "".join(
        f"chr1 {3 + i} 12 AAA {count}\n" for i, count in enumerate(counts))
    repo = a_grr().with_resource(
        "frags/big", a_fragment_score()
        .with_score("cell", "str").with_score("count", "int")
        .with_tabix().with_data(data)).build_repo(tmp_path / "grr")
    entry = {"resource_query": "frags/big", "value": {"score_id": "count"},
             "aggregate": {"aggregator": aggregator}}
    region = BedRegion("chr1", 1, 10)
    exact = {"sum": sum(counts), "mean": sum(counts) / len(counts),
             "product": math.prod(counts)}[aggregator]
    floating = 0.0 if aggregator != "product" else 1.0
    for count in counts:
        floating = floating * count if aggregator == "product" \
            else floating + count
    if aggregator == "mean":
        floating /= len(counts)
    expected, _ = streaming_block(entry, region, repo, genome, mocker)

    block, _ = bound_block(entry, region, repo, genome)

    assert expected[0, 0] == float(exact)
    assert block[0, 0] == floating
    assert block[0, 0] != expected[0, 0]


def test_a_subnormal_factor_of_a_product_rounds_as_the_streaming_fold(
) -> None:
    # ``ProductAggregator`` multiplies a subnormal value through frexp and
    # ldexp, which round twice.  ``np.multiply.at`` rounds once, so the
    # array fold multiplies such a cell one value at a time.
    reads = [[(52, "AAA", 0.3), (56, "AAA", 7.40148500012542e-309),
              (59, "AAA", -6.318824842378611e+265)]]
    expected = streaming_fold(reads, start=51, end=60, aggregator="product")
    rng = np.random.default_rng(0)
    starts = [StartRead(
        batches_of(rng, reads[0]), value_score="value",
        group_score="cell", track_of=FUZZ_TRACKS.get)]

    block = fold_fragment_starts(
        starts, start=51, end=60, bin_size=BIN_SIZE, tracks=3,
        aggregator="product")

    assert expected[0, 0].hex() == "-0x1.9080c502c789cp-143"
    assert block[0, 0].hex() == expected[0, 0].hex()


def test_an_int_constant_value_is_folded_as_float64_on_the_array_path(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
    mocker: pytest_mock.MockerFixture,
) -> None:
    # The int-past-2**53 difference holds for an int constant ``value``
    # too: the record path multiplies 3 ** 36 exactly, the array path
    # rounds after each float64 multiplication.
    data = "chrom pos_begin pos_end cell\n" + "chr1 3 12 AAA\n" * 36
    repo = a_grr().with_resource(
        "frags/three", a_fragment_score()
        .with_score("cell", "str").with_tabix().with_data(data),
    ).build_repo(tmp_path / "grr")
    entry = {"resource_query": "frags/three", "value": {"value": 3},
             "aggregate": {"aggregator": "product"}}
    region = BedRegion("chr1", 1, 10)
    floating = 1.0
    for _ in range(36):
        floating *= 3
    expected, _ = streaming_block(entry, region, repo, genome, mocker)

    block, _ = bound_block(entry, region, repo, genome)

    assert expected[0, 0] == float(3 ** 36)
    assert block[0, 0] == floating
    assert block[0, 0] != expected[0, 0]


@pytest.mark.parametrize("aggregator,record_value,array_value", [
    ("count", 3.0, 2.0),
    ("sum", math.nan, 7.0),
])
def test_a_nan_that_na_values_does_not_cover_is_no_value_on_the_array_path(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
    mocker: pytest_mock.MockerFixture, aggregator: str,
    record_value: float, array_value: float,
) -> None:
    # The record read keeps a NaN cell that na_values does not cover as a
    # value.  The array fold takes every NaN as no value.
    data = ("chrom pos_begin pos_end cell signal\n"
            "chr1 3 12 AAA 2.0\n"
            "chr1 4 12 AAA nan\n"
            "chr1 5 12 AAA 5.0\n")
    repo = a_grr().with_resource(
        "frags/nan", a_fragment_score()
        .with_score("cell", "str").with_score("signal", "float")
        .with_na_values(".", score_id="signal")
        .with_tabix().with_data(data)).build_repo(tmp_path / "grr")
    entry = {"resource_query": "frags/nan",
             "value": {"score_id": "signal"},
             "aggregate": {"aggregator": aggregator}}
    region = BedRegion("chr1", 1, 10)
    expected, _ = streaming_block(entry, region, repo, genome, mocker)

    block, _ = bound_block(entry, region, repo, genome)

    assert np.array_equal(expected[0, 0], record_value, equal_nan=True)
    assert block[0, 0] == array_value


LENGTH_AGGREGATORS = ["sum", "mean", "max", "min", "median"]


@pytest.mark.parametrize("aggregator", LENGTH_AGGREGATORS)
def test_a_fragment_length_track_reads_the_overlapping_array_reader(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
    mocker: pytest_mock.MockerFixture, aggregator: str,
) -> None:
    arrays = mocker.spy(
        FragmentScore, "get_fragment_arrays_overlapping_region")
    records = mocker.spy(
        FragmentScore, "get_fragment_scores_overlapping_region")

    bound_block(
        {"resource_query": "frags/*", "value": {"score_id": "count"},
         "aggregate": {"mode": "fragment_length",
                       "aggregator": aggregator}},
        BedRegion("chr1", 1, 40), repo, genome)

    assert arrays.call_count == 2
    assert records.call_count == 0


LENGTH = {"mode": "fragment_length"}
LENGTH_UNCOVERED = [
    {}, {"uncovered_value": 0}, {"uncovered_value": 3},
    {"uncovered_value": -0.7}]


@pytest.mark.parametrize("aggregate", [
    {**LENGTH, **uncovered, "aggregator": aggregator}
    for aggregator in LENGTH_AGGREGATORS for uncovered in LENGTH_UNCOVERED
], ids=str)
@pytest.mark.parametrize("entry", [
    {"resource_query": "frags/*"},
    {"resource_query": "frags/s1", "pool": False,
     "value": {"value": 2.5}},
    BY_CLASS,
    {**BY_CLASS, "value": {"score_id": "count"}},
])
@pytest.mark.parametrize("region", [
    *REGIONS, BedRegion("chr1", 13, 33), BedRegion("chr1", 21, 21)],
    ids=str)
def test_a_fragment_length_track_answers_the_streaming_fold_on_the_fixtures(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
    mocker: pytest_mock.MockerFixture, aggregate: dict[str, Any],
    entry: dict[str, Any], region: BedRegion,
) -> None:
    entry = {**entry, "aggregate": aggregate}
    expected, expected_dropped = streaming_block(
        entry, region, repo, genome, mocker)

    block, dropped = array_block(entry, region, repo, genome, mocker)

    assert block.dtype == expected.dtype
    assert block.shape == expected.shape
    assert np.array_equal(block, expected, equal_nan=True)
    assert np.array_equal(np.signbit(block), np.signbit(expected))
    assert dropped == expected_dropped


#: Extents that hit the traps of the length fold: an extent runs past
#: either edge of the region, and a long fragment ends after a shorter,
#: later one, often in a later window.
Extent = tuple[int, int, str | None, float]
#: The values of the length fuzz.  A product is not a length aggregator,
#: so the values need no product traps, but zeros of both signs, NaN and
#: the infinities stay.
LENGTH_VALUES = [
    0.0, -0.0, 1.5, -2.25, 3.0, 1e200, -1e200, np.inf, -np.inf, np.nan,
    np.nan, 7.0, 0.5, 0.1, 0.3, -6.318824842378611e+265]


def random_extents(
    rng: np.random.Generator, start: int, end: int,
) -> list[list[Extent]]:
    """Two to four resources of fragments that overlap ``[start, end]``.

    Each resource is sorted by begin.  The begins come from a few
    positions, some before ``start``, and an extent is up to 45 bp long.
    """
    positions = rng.choice(np.arange(start - 15, end + 1), size=12)
    reads = []
    for _ in range(rng.integers(2, 5)):
        size = int(rng.integers(0, 30))
        begins = np.sort(rng.choice(positions, size=size))
        extents = []
        for begin in begins.tolist():
            stop = begin + int(rng.integers(0, 46))
            if stop < start:
                stop = start + int(rng.integers(0, 3))
            extents.append((
                begin, stop,
                FUZZ_BARCODES[rng.integers(len(FUZZ_BARCODES))],
                float(LENGTH_VALUES[rng.integers(len(LENGTH_VALUES))])))
        reads.append(extents)
    return reads


def extent_batches(
    rng: np.random.Generator, extents: list[Extent],
) -> Generator[tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]],
               None, None]:
    """The extents in batches of random sizes, as the array reader."""
    position = 0
    while position < len(extents):
        size = int(rng.integers(1, 6))
        batch = extents[position:position + size]
        position += size
        yield (
            np.array([begin for begin, _, _, _ in batch], dtype=np.int64),
            np.array([stop for _, stop, _, _ in batch], dtype=np.int64),
            {"value": np.array([value for _, _, _, value in batch]),
             "cell": np.array(
                 [cell for _, _, cell, _ in batch], dtype=object)})


def streaming_length_fold(
    reads: list[list[Extent]], *, start: int, end: int, bin_size: int,
    aggregator: str, uncovered_value: float | None,
) -> np.ndarray:
    """The oracle: one streaming length fold per track, as the binner.

    Each extent is clipped to the region before the merge by begin.
    """
    folds = [
        BinFragmentLengthAggregator(
            start=start, end=end, bin_size=bin_size, aggregator=aggregator,
            uncovered_value=uncovered_value)
        for _ in range(3)]
    columns: list[list[float]] = [[] for _ in folds]
    clipped = [
        [(max(begin, start), min(stop, end), cell, value)
         for begin, stop, cell, value in extents]
        for extents in reads]
    for begin, stop, cell, value in heapq.merge(
            *clipped, key=operator.itemgetter(0)):
        track = FUZZ_TRACKS.get(cell or "")
        if track is None:
            continue
        columns[track].extend(
            bin_value.value for bin_value in folds[track].feed(
                FragmentValue(
                    begin, stop, None if np.isnan(value) else value)))
    for column, fold in zip(columns, folds, strict=True):
        column.extend(bin_value.value for bin_value in fold.flush())
    return np.array(columns, dtype=np.float64).T


@pytest.mark.parametrize("aggregator", LENGTH_AGGREGATORS)
@pytest.mark.parametrize(
    "uncovered_value", [None, 3, -0.7, 0.1, 0.0, -0.0], ids=repr)
@pytest.mark.parametrize("bin_size", [1, 3, 10])
@pytest.mark.parametrize("seed", range(30))
def test_the_length_fold_answers_the_streaming_fold_on_random_fragments(
    aggregator: str, uncovered_value: float | None, bin_size: int,
    seed: int,
) -> None:
    rng = np.random.default_rng(seed)
    start = int(rng.integers(1, 30))
    end = start + int(rng.integers(0, 60))
    reads = random_extents(rng, start, end)
    expected = streaming_length_fold(
        reads, start=start, end=end, bin_size=bin_size,
        aggregator=aggregator, uncovered_value=uncovered_value)
    lengths = [
        LengthRead(
            extent_batches(rng, extents), value_score="value",
            group_score="cell", track_of=FUZZ_TRACKS.get,
            start=start, end=end)
        for extents in reads]

    block = fold_fragment_lengths(
        lengths, start=start, end=end, bin_size=bin_size, tracks=3,
        aggregator=aggregator, uncovered_value=uncovered_value)

    assert np.array_equal(block, expected, equal_nan=True)
    assert np.array_equal(np.signbit(block), np.signbit(expected))
    assert [read.dropped for read in lengths] == [
        sum(FUZZ_TRACKS.get(cell or "") is None
            for _, _, cell, _ in extents)
        for extents in reads]


def test_the_fill_at_a_window_frontier_stops_at_a_bin_boundary() -> None:
    # The frontier 5 falls inside the bin [1, 10].  The streaming fold
    # fills [3, 8] of AAA in one piece when the fragment at 9 arrives:
    # 0.6 + 0.7 * 6.  A fill split at the frontier would add
    # 0.6 + 0.7 * 2 + 0.7 * 4, which rounds to another float.
    extents: list[Extent] = [
        (1, 2, "AAA", 0.3), (5, 5, "BBB", 1.0), (9, 9, "AAA", 2.0)]
    expected = streaming_length_fold(
        [extents], start=1, end=10, bin_size=BIN_SIZE, aggregator="sum",
        uncovered_value=0.7)

    def one_at_a_time() -> LengthRead:
        rng = np.random.default_rng(0)
        return LengthRead(
            (batch for extent in extents
             for batch in extent_batches(rng, [extent])),
            value_score="value", group_score="cell",
            track_of=FUZZ_TRACKS.get, start=1, end=10)

    frontiers = [
        frontier for _, frontier in _windows([one_at_a_time()])]
    block = fold_fragment_lengths(
        [one_at_a_time()], start=1, end=10, bin_size=BIN_SIZE, tracks=3,
        aggregator="sum", uncovered_value=0.7)

    assert frontiers[0] == 5

    assert expected[0, 0] != 0.3 * 2 + 0.7 * 2 + 0.7 * 4 + 2.0
    assert block[0, 0] == expected[0, 0]
    assert np.array_equal(block, expected, equal_nan=True)


def test_a_length_fold_clips_fragments_that_cross_the_region_edges(
) -> None:
    # [3, 14] starts before the region [11, 25] and adds 4 bp to the bin
    # [11, 20].  [18, 40] ends after it and adds 3 bp there and 5 bp to
    # the bin [21, 30], whose part past 25 is not filled.
    extents: list[Extent] = [(3, 14, "AAA", 2.0), (18, 40, "AAA", 10.0)]
    read = LengthRead(
        extent_batches(np.random.default_rng(0), extents),
        value_score="value", group_score="cell",
        track_of=FUZZ_TRACKS.get, start=11, end=25)

    block = fold_fragment_lengths(
        [read], start=11, end=25, bin_size=BIN_SIZE, tracks=3,
        aggregator="sum", uncovered_value=1.0)

    assert block[:, 0].tolist() == [2.0 * 4 + 1.0 * 3 + 10.0 * 3, 10.0 * 5]
    assert block[:, 1].tolist() == [10.0, 5.0]
