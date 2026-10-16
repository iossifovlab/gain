# pylint: disable=W0621,C0116
# W0621: the tests take the pytest fixtures by their names.
# C0116: the test names say what each test checks.
"""The position binner's array fold agrees with ``get_score_in_bins``.

``PositionScoreBinding.bin_region`` folds the batches of
``fetch_region_value_arrays`` with numpy for a ``float`` score under
``count``, ``sum``, ``mean``, ``max`` and ``min`` (gain#1957).  Every
other case keeps the per-run fold of ``get_score_in_bins``, which stays
the reference: the parity tests here compare the two bit for bit.
"""
import numpy as np
import pytest
import pytest_mock
from gain.binning.binners import (
    PositionScoreBinding,
    PositionScoreDef,
    Track,
)
from gain.genomic_resources.aggregators import to_float64
from gain.genomic_resources.genomic_scores import (
    DEFAULT_VALUE_ARRAYS_BATCH_SIZE,
)
from gain.genomic_resources.genomic_scores.position import PositionScore
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing.builders import (
    a_bigwig_score,
    a_grr,
    a_position_score,
)
from gain.utils.regions import BedRegion

#: Twenty one-position values whose sum in order differs from the
#: pairwise sum of ``np.add.reduceat``: the stretch ``chr1:40-59`` of
#: both fixtures holds one record for each.
ORDER_SENSITIVE = [
    -524.071, 88.458, -260.09, 207.84, 251.441, -868.942, -973.664,
    674.938, -481.292, -531.338, 991.29, -59.473, 672.923, -47.294,
    278.136, -698.767, 269.721, 736.091, 46.362, 482.504,
]
ORDER_SENSITIVE_REGION = BedRegion("chr1", 40, 59)

#: chr1 holds overlapping records (1-3 and 2-5), a missing value that
#: claims its positions ahead of a later record (6-8 NA, 7-9), a nested
#: record (12-15 inside 10-20), two records with the same start (22-25,
#: 22-30), an uncovered stretch (31-39), the order-sensitive stretch,
#: signed zeros and a trailing missing value.  chr2 has its own overlaps.
TABIX_DATA = """
    chrom  pos_begin  pos_end  s
    chr1   1          3        1.5
    chr1   2          5        2.25
    chr1   6          8        NA
    chr1   7          9        3.0
    chr1   10         20       0.5
    chr1   12         15       7.0
    chr1   22         25       1.25
    chr1   22         30       -2.0
""" + "".join(
    f"    chr1   {40 + i}         {40 + i}         {value!r}\n"
    for i, value in enumerate(ORDER_SENSITIVE)) + """
    chr1   61         62       -0.0
    chr1   63         63       0.0
    chr1   64         66       -0.0
    chr1   72         75       NA
    chr2   5          10       4.0
    chr2   8          12       NA
    chr2   11         15       5.0
    chr2   30         40       1.0
"""

#: The bigWig counterpart: bedGraph rows, 0-based half-open, which a
#: bigWig cannot hold overlapping.
BIGWIG_DATA = """
    chr1   0          3        1.5
    chr1   3          5        2.25
    chr1   9          20       0.5
    chr1   21         30       -2.0
""" + "".join(
    f"    chr1   {39 + i}         {40 + i}         {value!r}\n"
    for i, value in enumerate(ORDER_SENSITIVE)) + """
    chr1   60         62       -0.0
    chr1   62         63       0.0
    chr1   70         80       3.5
    chr2   4          10       4.0
    chr2   10         15       5.0
    chr2   29         40       1.0
"""

AGGREGATORS = ["count", "sum", "mean", "max", "min"]

REGIONS = {
    "inside-one-bin": BedRegion("chr1", 9, 12),
    "odd-offsets": BedRegion("chr1", 3, 58),
    "contig-end": BedRegion("chr2", 27, 40),
    "absent-contig": BedRegion("chr3", 1, 25),
    "order-sensitive": ORDER_SENSITIVE_REGION,
    "signed-zeros": BedRegion("chr1", 60, 70),
}

#: Larger than every region above, so each region is one bin.
LARGE_BIN_SIZE = 1000


@pytest.fixture(scope="module")
def repo(tmp_path_factory: pytest.TempPathFactory) -> GenomicResourceRepo:
    return (
        a_grr()
        .with_resource("tabix", a_position_score()
                       .with_score("s", "float")
                       .with_tabix()
                       .with_data(TABIX_DATA))
        .with_resource("bigwig", a_bigwig_score()
                       .with_score("s", "float")
                       .with_data(BIGWIG_DATA)
                       .with_chrom_lens({"chr1": 80, "chr2": 40}))
        .with_resource("int_score", a_position_score()
                       .with_score("s", "int")
                       .with_tabix()
                       .with_data("""
                           chrom  pos_begin  pos_end  s
                           chr1   1          10       3
                       """))
        .with_resource("in_memory", a_position_score()
                       .with_score("s", "float")
                       .with_data("""
                           chrom  pos_begin  pos_end  s
                           chr1   1          10       1.5
                       """))
        .build_repo(tmp_path_factory.mktemp("grr")))


def a_track(
    resource_id: str, aggregator: str, replacement: float | None,
) -> Track:
    return Track(
        name=resource_id, resource_ids=(resource_id,), group="",
        selector="s", binner_type="position_score",
        definition=PositionScoreDef(
            aggregator=aggregator, none_value_replacement=replacement))


def bin_column(
    repo: GenomicResourceRepo, track: Track, region: BedRegion,
    bin_size: int, batch_size: int = DEFAULT_VALUE_ARRAYS_BATCH_SIZE,
) -> np.ndarray:
    """``bin_region``'s one column, read with ``batch_size`` rows a batch."""
    (resource_id,) = track.resource_ids
    score = PositionScore(repo.get_resource(resource_id))
    binding = PositionScoreBinding(
        track, score, value_arrays_batch_size=batch_size)
    with binding as bound:
        block = bound.bin_region(region, bin_size)
    assert block.dtype == np.float64
    assert block.shape == (len(block), 1)
    return block[:, 0]


def reference_column(
    repo: GenomicResourceRepo, track: Track, region: BedRegion,
    bin_size: int,
) -> np.ndarray:
    """The column the per-run fold of ``get_score_in_bins`` gives."""
    (resource_id,) = track.resource_ids
    definition = track.definition
    assert isinstance(definition, PositionScoreDef)
    score = PositionScore(repo.get_resource(resource_id))
    with score.open():
        values = [
            np.nan if value is None else to_float64(value)
            for _, _, value in score.get_score_in_bins(
                region.chrom, region.start, region.stop, bin_size,
                score=track.selector, aggregator=definition.aggregator,
                none_value_replacement=definition.none_value_replacement)
        ]
    return np.array(values, dtype=np.float64)


def assert_same_bits(actual: np.ndarray, expected: np.ndarray) -> None:
    """Equal values, and equal signs of zero, which ``==`` ignores."""
    np.testing.assert_array_equal(actual, expected)
    np.testing.assert_array_equal(np.signbit(actual), np.signbit(expected))


@pytest.mark.parametrize("region", REGIONS.values(), ids=REGIONS.keys())
@pytest.mark.parametrize("bin_size", [1, 7, LARGE_BIN_SIZE])
@pytest.mark.parametrize("replacement", [None, 0.0], ids=["none", "zero"])
@pytest.mark.parametrize("aggregator", AGGREGATORS)
@pytest.mark.parametrize("batch_size", [
    DEFAULT_VALUE_ARRAYS_BATCH_SIZE, 7, 1])
def test_the_array_fold_of_a_tabix_score_equals_get_score_in_bins(
    repo: GenomicResourceRepo, batch_size: int, aggregator: str,
    replacement: float | None, bin_size: int, region: BedRegion,
) -> None:
    track = a_track("tabix", aggregator, replacement)

    actual = bin_column(repo, track, region, bin_size, batch_size)

    assert_same_bits(
        actual, reference_column(repo, track, region, bin_size))


@pytest.mark.parametrize("region", REGIONS.values(), ids=REGIONS.keys())
@pytest.mark.parametrize("bin_size", [1, 7, LARGE_BIN_SIZE])
@pytest.mark.parametrize("replacement", [None, 0.0], ids=["none", "zero"])
@pytest.mark.parametrize("aggregator", AGGREGATORS)
def test_the_array_fold_of_a_bigwig_score_equals_get_score_in_bins(
    repo: GenomicResourceRepo, aggregator: str,
    replacement: float | None, bin_size: int, region: BedRegion,
) -> None:
    track = a_track("bigwig", aggregator, replacement)

    actual = bin_column(repo, track, region, bin_size)

    assert_same_bits(
        actual, reference_column(repo, track, region, bin_size))


def sequential_sum(values: list[float]) -> float:
    """The sum the ``sum`` aggregator builds: one addition at a time."""
    total = 0.0
    for value in values:
        total += value
    return total


def test_the_fixture_has_a_bin_whose_pairwise_sum_differs(
    repo: GenomicResourceRepo,
) -> None:
    # The parity tests above are not vacuous for the order of the
    # additions: the one bin of the order-sensitive stretch holds 20
    # pieces, and a pairwise reduction of them gives another sum.
    pieces = np.array(ORDER_SENSITIVE)
    pairwise = np.add.reduceat(pieces, [0])[0]

    reference = reference_column(
        repo, a_track("tabix", "sum", None), ORDER_SENSITIVE_REGION,
        LARGE_BIN_SIZE)

    assert len(pieces) > 8
    assert pairwise != sequential_sum(ORDER_SENSITIVE)
    np.testing.assert_array_equal(
        reference, [sequential_sum(ORDER_SENSITIVE)])


@pytest.mark.parametrize("aggregator", AGGREGATORS)
def test_a_float_score_reads_through_the_array_read(
    repo: GenomicResourceRepo, mocker: pytest_mock.MockerFixture,
    aggregator: str,
) -> None:
    arrays = mocker.spy(PositionScore, "_parsed_column_batches")
    per_run = mocker.spy(PositionScore, "get_score_in_bins")

    bin_column(
        repo, a_track("tabix", aggregator, None),
        BedRegion("chr1", 1, 40), 10)

    assert arrays.call_count == 1
    assert per_run.call_count == 0


@pytest.mark.parametrize("resource_id,aggregator", [
    ("int_score", "sum"),
    ("tabix", "median"),
    ("tabix", "product"),
    ("in_memory", "mean"),
], ids=["int-score", "median", "product", "no-array-read"])
def test_the_other_cases_keep_the_per_run_fold(
    repo: GenomicResourceRepo, mocker: pytest_mock.MockerFixture,
    resource_id: str, aggregator: str,
) -> None:
    arrays = mocker.spy(PositionScore, "_parsed_column_batches")
    per_run = mocker.spy(PositionScore, "get_score_in_bins")
    track = a_track(resource_id, aggregator, None)

    actual = bin_column(repo, track, BedRegion("chr1", 1, 40), 10)

    assert arrays.call_count == 0
    assert per_run.call_count == 1
    np.testing.assert_array_equal(
        actual, reference_column(repo, track, BedRegion("chr1", 1, 40), 10))


def test_a_contig_the_score_does_not_have_is_not_read(
    repo: GenomicResourceRepo, mocker: pytest_mock.MockerFixture,
) -> None:
    # The array read refuses a contig the table does not hold, so the
    # array fold answers it as one uncovered run without a read.
    arrays = mocker.spy(PositionScore, "_parsed_column_batches")

    actual = bin_column(
        repo, a_track("tabix", "mean", 0.0), BedRegion("chr3", 1, 25), 10)

    assert arrays.call_count == 0
    np.testing.assert_array_equal(actual, [0.0, 0.0, 0.0])


def test_a_binding_reads_default_size_batches_unless_told_otherwise(
    repo: GenomicResourceRepo, mocker: pytest_mock.MockerFixture,
) -> None:
    arrays = mocker.spy(PositionScore, "_parsed_column_batches")
    track = a_track("tabix", "mean", None)
    score = PositionScore(repo.get_resource("tabix"))

    with PositionScoreBinding(track, score) as bound:
        bound.bin_region(BedRegion("chr1", 1, 40), 10)

    assert arrays.call_args.kwargs["batch_size"] == (
        DEFAULT_VALUE_ARRAYS_BATCH_SIZE)


#: A ``NaN`` cell is not one of the default NA sentinels, which are
#: case-sensitive: the score holds a NaN value there, and the per-run
#: fold reads it as a value, not as a missing one.  The NaN records sit
#: first in a bin (1-3), after a value (5-6), and ahead of a missing
#: value (9-10 NA) and a later value (11-12), and after an infinite
#: minimum (17 inf, 18 NaN).
NAN_TABIX_DATA = """
    chrom  pos_begin  pos_end  s
    chr1   1          3        NaN
    chr1   4          4        1.5
    chr1   5          6        NaN
    chr1   7          8        inf
    chr1   9          10       NA
    chr1   11         12       NaN
    chr1   13         14       -2.0
    chr1   17         17       inf
    chr1   18         18       NaN
"""

#: The bigWig counterpart: its payload holds NaN values, which no NA
#: sentinel matches.
NAN_BIGWIG_DATA = """
    chr1   0          3        nan
    chr1   3          4        1.5
    chr1   4          6        nan
    chr1   6          8        inf
    chr1   10         12       nan
    chr1   12         14       -2.0
    chr1   16         17       inf
    chr1   17         18       nan
"""

NAN_REGION = BedRegion("chr1", 1, 20)


@pytest.fixture(scope="module")
def nan_repo(
    tmp_path_factory: pytest.TempPathFactory,
) -> GenomicResourceRepo:
    return (
        a_grr()
        .with_resource("tabix", a_position_score()
                       .with_score("s", "float")
                       .with_tabix()
                       .with_data(NAN_TABIX_DATA))
        .with_resource("bigwig", a_bigwig_score()
                       .with_score("s", "float")
                       .with_data(NAN_BIGWIG_DATA)
                       .with_chrom_lens({"chr1": 20}))
        .build_repo(tmp_path_factory.mktemp("nan_grr")))


@pytest.mark.parametrize("resource_id", ["tabix", "bigwig"])
@pytest.mark.parametrize("bin_size", [1, 4, LARGE_BIN_SIZE])
@pytest.mark.parametrize("replacement", [None, 0.0], ids=["none", "zero"])
@pytest.mark.parametrize("aggregator", AGGREGATORS)
@pytest.mark.parametrize("batch_size", [
    DEFAULT_VALUE_ARRAYS_BATCH_SIZE, 1])
def test_a_nan_value_is_a_value_in_the_array_fold(
    nan_repo: GenomicResourceRepo, batch_size: int, aggregator: str,
    replacement: float | None, bin_size: int, resource_id: str,
) -> None:
    track = a_track(resource_id, aggregator, replacement)

    actual = bin_column(nan_repo, track, NAN_REGION, bin_size, batch_size)

    assert_same_bits(
        actual, reference_column(nan_repo, track, NAN_REGION, bin_size))
