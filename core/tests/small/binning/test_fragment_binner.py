# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""``fragment_score_binner``: entries, tracks and the blocks they bin to.

The toy fragments and the cell table are the conftest's ``S1_FRAGMENTS``,
``S2_FRAGMENTS`` and ``CELL_META``; bins are 10 wide, so on chr1 the
fragments of ``frags/s1`` start in bins as follows:

==========  ====  =====  =====  ==============================
fragment    cell  count  bin    class (S1 rows of the table)
==========  ====  =====  =====  ==============================
(3, 12)     AAA   2      1-10   T
(10, 25)    BBB   5      1-10   B
(11, 14)    CCC   1      11-20  empty: no group
(15, 40)    AAA   3      11-20  T
(33, 34)    DDD   4      31-40  not in the table
==========  ====  =====  =====  ==============================

and on chr2 one BBB fragment (count 6) starts in 1-10.  ``frags/s2`` has
no chr2; on chr1 its AAA (class B in S2) starts at 4 with count 7, and
its EEE (class T) at 12, 16 and 35 with counts 1, 4 and 2.
"""
import json
import pathlib
from collections.abc import Iterator
from typing import Any

import numpy as np
import pytest
import pytest_mock
from gain.binning.binners import BinningJob, discover_binner_kinds
from gain.binning.run_definition import (
    RunDefinition,
    RunDefinitionError,
    parse_run_definition,
)
from gain.genomic_resources.aggregators import ScoreAggregationQuery
from gain.genomic_resources.genomic_scores import FragmentScore
from gain.genomic_resources.reference_genome import ReferenceGenome
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing.builders import a_fragment_score, a_grr
from gain.genomic_resources.testing.data_frame_builder import a_data_frame
from gain.utils.regions import BedRegion

from tests.small.binning.conftest import CELL_META, S1_FRAGMENTS

BIN_SIZE = 10
KIND = "fragment_score_binner"


def parse_fragment_entry(
    entry: dict[str, Any], repo: GenomicResourceRepo,
    genome: ReferenceGenome,
) -> RunDefinition:
    return parse_run_definition({
        "bins": {"bin_size": BIN_SIZE},
        "binners": [{KIND: entry}],
    }, repo, genome)


def bin_regions(
    job: BinningJob, regions: list[BedRegion], repo: GenomicResourceRepo,
) -> list[np.ndarray]:
    binner = discover_binner_kinds()[job.binner]
    with binner.bind(job, repo) as bound:
        return [bound.bin_region(region, BIN_SIZE) for region in regions]


def test_a_plain_entry_is_one_all_track_counting_fragments_per_bin(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    # No group: one group, ``all``, counting the fragments that start in
    # each bin -- what the score's own binned read answers for a count of
    # the barcode every fragment carries.
    run = parse_fragment_entry(
        {"resource_query": "frags/s1", "pool": False}, repo, genome)

    assert [(t.name, t.group) for t in run.tracks] == [
        ("frags/s1:all", "all")]
    (job,) = run.jobs
    regions = [BedRegion("chr1", 1, 40), BedRegion("chr2", 1, 40)]
    blocks = bin_regions(job, regions, repo)
    with FragmentScore(repo.get_resource("frags/s1")).open() as score:
        expected = [
            score.get_scores_in_bins(
                region.chrom, region.start, region.stop, BIN_SIZE,
                [ScoreAggregationQuery("cell", "count")])
            for region in regions
        ]
    for block, reference in zip(blocks, expected, strict=True):
        assert block.dtype == np.float64
        np.testing.assert_array_equal(block, reference)
    np.testing.assert_array_equal(blocks[0][:, 0], [2.0, 2.0, 0.0, 1.0])


def bin_entry(
    entry: dict[str, Any], region: BedRegion,
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> np.ndarray:
    """The block of a one-job entry over one region."""
    (job,) = parse_fragment_entry(entry, repo, genome).jobs
    (block,) = bin_regions(job, [region], repo)
    return block


CHR1 = BedRegion("chr1", 1, 40)


@pytest.mark.parametrize("aggregator", [
    "count", "sum", "mean", "max", "min", "median", "product"])
def test_the_start_fold_answers_the_score_s_own_binned_read(
    repo: GenomicResourceRepo, genome: ReferenceGenome, aggregator: str,
) -> None:
    # 5-44 starts mid-bin: S2's AAA at 4 lies in the edge bin 1-10 yet
    # outside the region; EEE starts at 12 and 16 (counts 1, 4) and 35 (2).
    region = BedRegion("chr1", 5, 44)

    block = bin_entry(
        {"resource_query": "frags/s2", "pool": False,
         "value": {"score_id": "count"},
         "aggregate": {"aggregator": aggregator}},
        region, repo, genome)

    with FragmentScore(repo.get_resource("frags/s2")).open() as score:
        reference = score.get_scores_in_bins(
            region.chrom, region.start, region.stop, BIN_SIZE,
            [ScoreAggregationQuery("count", aggregator)])
    assert block.shape == (5, 1)
    np.testing.assert_array_equal(block, reference)


def test_an_entry_without_value_or_aggregate_counts_fragments(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    # ``frags/s1`` has an int ``count`` score, with counts 2 and 5 in the
    # first bin; the default counts the two fragments, not 7 read pairs.
    block = bin_entry(
        {"resource_query": "frags/s1", "pool": False}, CHR1, repo, genome)

    np.testing.assert_array_equal(block[:, 0], [2.0, 2.0, 0.0, 1.0])


def test_a_constant_value_of_two_sums_to_twice_the_fragment_count(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    plain = {"resource_query": "frags/s1", "pool": False}
    counted = bin_entry(
        {**plain, "value": {"value": 1}}, CHR1, repo, genome)

    doubled = bin_entry(
        {**plain, "value": {"value": 2}, "aggregate": {"aggregator": "sum"}},
        CHR1, repo, genome)

    np.testing.assert_array_equal(doubled, 2 * counted)
    np.testing.assert_array_equal(doubled[:, 0], [4.0, 4.0, 0.0, 2.0])


def test_a_score_value_without_an_aggregate_is_summed(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    # ``value: {score_id: count}`` gives the read pairs per bin: 2 + 5,
    # 1 + 3, nothing, 4.
    plain = {"resource_query": "frags/s1", "pool": False,
             "value": {"score_id": "count"}}

    implied = bin_entry(plain, CHR1, repo, genome)
    spelled = bin_entry(
        {**plain, "aggregate": {"mode": "fragment_start",
                                "aggregator": "sum"}},
        CHR1, repo, genome)

    np.testing.assert_array_equal(implied, spelled)
    np.testing.assert_array_equal(implied[:, 0], [7.0, 4.0, 0.0, 4.0])


@pytest.mark.parametrize("aggregate,moved_to", [
    ({"score": "count"}, "value: {score_id: count}"),
    ({"score": "count", "aggregator": "mean"}, "value: {score_id: count}"),
    ({"value": 2}, "value: {value: 2}"),
])
def test_a_value_given_in_aggregate_is_refused_naming_the_value_key(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
    aggregate: dict[str, Any], moved_to: str,
) -> None:
    with pytest.raises(RunDefinitionError) as excinfo:
        parse_fragment_entry(
            {"resource_query": "frags/s1", "aggregate": aggregate},
            repo, genome)

    message = str(excinfo.value)
    assert message.startswith("binners[0].aggregate")
    assert moved_to in message


@pytest.mark.parametrize("mode", [
    "coverage", "sideways", ["fragment_start"]])
def test_an_unknown_mode_is_refused_naming_the_modes(
    repo: GenomicResourceRepo, genome: ReferenceGenome, mode: Any,
) -> None:
    with pytest.raises(RunDefinitionError) as excinfo:
        parse_fragment_entry(
            {"resource_query": "frags/s1", "aggregate": {"mode": mode}},
            repo, genome)

    message = str(excinfo.value)
    assert message.startswith("binners[0].aggregate")
    assert repr(mode) in message
    assert ("use one of fragment_start, fragment_length, "
            "coverage_profile") in message


def length_entry(**aggregate: Any) -> dict[str, Any]:
    """An unpooled ``frags/s1`` entry in ``fragment_length`` mode."""
    return {"resource_query": "frags/s1", "pool": False,
            "aggregate": {"mode": "fragment_length", **aggregate}}


@pytest.mark.parametrize("aggregate,fragments", [
    ({"aggregator": "count"},
     ["aggregator 'count'", "fragment_length",
      "aggregator: sum with value: {value: 1}"]),
    ({"aggregator": "product"},
     ["aggregator 'product'", "fragment_length",
      "use one of max, mean, median, min, sum"]),
    ({"uncovered_value": "zero"},
     ["uncovered_value must be a number or null", "'zero'"]),
    ({"uncovered_value": True},
     ["uncovered_value must be a number or null", "True"]),
    ({"uncovered_value": float("nan")},
     ["uncovered_value must be a number or null", "nan"]),
    ({"uncovered_value": float("inf")},
     ["uncovered_value must be a number or null", "inf"]),
    ({"uncovered_value": -float("inf")},
     ["uncovered_value must be a number or null", "-inf"]),
    ({"uncovered_value": 10 ** 400},
     ["uncovered_value must be a number or null", "1" + "0" * 400]),
])
def test_a_length_aggregate_it_cannot_honour_is_refused(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
    aggregate: dict[str, Any], fragments: list[str],
) -> None:
    with pytest.raises(RunDefinitionError) as excinfo:
        parse_fragment_entry(length_entry(**aggregate), repo, genome)

    message = str(excinfo.value)
    assert message.startswith("binners[0].aggregate")
    for fragment in fragments:
        assert fragment in message


@pytest.mark.parametrize("aggregate", [
    {"uncovered_value": 0},
    {"mode": "fragment_start", "uncovered_value": None},
])
def test_an_uncovered_value_in_fragment_start_mode_is_refused(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
    aggregate: dict[str, Any],
) -> None:
    with pytest.raises(RunDefinitionError) as excinfo:
        parse_fragment_entry(
            {"resource_query": "frags/s1", "aggregate": aggregate},
            repo, genome)

    message = str(excinfo.value)
    assert message.startswith("binners[0].aggregate")
    assert "uncovered_value does not apply in fragment_start mode" in message
    assert "give mode: fragment_length or coverage_profile" in message


@pytest.mark.parametrize("aggregate,expected", [
    ({}, ("sum", None)),
    ({"aggregator": "mean", "uncovered_value": 0}, ("mean", 0.0)),
    ({"aggregator": "median", "uncovered_value": 2.5}, ("median", 2.5)),
    ({"aggregator": "max", "uncovered_value": None}, ("max", None)),
])
def test_a_length_entry_carries_its_mode_and_uncovered_value(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
    aggregate: dict[str, Any], expected: tuple[str, float | None],
) -> None:
    run = parse_fragment_entry(length_entry(**aggregate), repo, genome)

    (track,) = run.tracks
    aggregator, uncovered = expected
    assert (track.mode, track.aggregator, track.uncovered_value) == (
        "fragment_length", aggregator, uncovered)
    parameters = json.loads(track.parameters)
    assert parameters["mode"] == "fragment_length"
    assert parameters["uncovered_value"] == uncovered


def test_a_constant_group_names_the_entry_s_one_track(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    run = parse_fragment_entry({
        "resource_query": "frags/s1", "pool": False,
        "group": {"group": "bulk"},
    }, repo, genome)

    assert [(t.name, t.group) for t in run.tracks] == [
        ("frags/s1:bulk", "bulk")]


BY_CLASS = {
    "cell_score_id": "cell",
    "cell_meta_column": "barcode",
    "group_meta_column": "class",
}
BY_SAMPLE_LABEL = {
    "resource_id": "meta/cells",
    "filter": [{"column": "sample_id", "label": "sample_id"}],
}


def grouped_s1(**extra: Any) -> dict[str, Any]:
    return {
        "resource_query": "frags/s1", "pool": False,
        "group": BY_CLASS, "meta": BY_SAMPLE_LABEL, **extra,
    }


def test_a_grouped_entry_is_one_track_per_class_of_its_sample(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    # S1's rows give the classes B and T; CCC's empty class is no group,
    # and S3's X is filtered out with the other samples' rows.
    run = parse_fragment_entry(grouped_s1(), repo, genome)

    assert [(t.name, t.group) for t in run.tracks] == [
        ("frags/s1:B", "B"), ("frags/s1:T", "T")]


def test_an_empty_class_read_as_pd_na_is_no_group(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
) -> None:
    # A table read with a nullable dtype holds ``pd.NA``, not NaN, in
    # CCC's empty class cell; it is still missing, so no ``<NA>`` track.
    repo = (
        a_grr()
        .with_resource("frags/s1", a_fragment_score()
                       .with_score("cell", "str")
                       .with_score("count", "int")
                       .with_labels(sample_id="S1")
                       .with_tabix()
                       .with_data(S1_FRAGMENTS))
        .with_resource("meta/cells", a_data_frame()
                       .with_raw_content(CELL_META)
                       .with_parameters({"dtype": "string"}))
    ).build_repo(tmp_path / "nullable")

    run = parse_fragment_entry(grouped_s1(), repo, genome)

    assert [t.group for t in run.tracks] == ["B", "T"]


def test_a_grouped_block_counts_each_class_dropping_unmapped_cells(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    # Columns B, T.  1-10: BBB and AAA.  11-20: AAA only -- CCC, of the
    # empty class, reaches no track.  31-40: DDD is not in the table, so
    # the bin is empty in every column, a dense 0, not a missing row.
    (job,) = parse_fragment_entry(grouped_s1(), repo, genome).jobs

    chr1, chr2 = bin_regions(
        job, [CHR1, BedRegion("chr2", 1, 40)], repo)

    np.testing.assert_array_equal(
        chr1, [[1.0, 1.0], [0.0, 1.0], [0.0, 0.0], [0.0, 0.0]])
    np.testing.assert_array_equal(
        chr2, [[1.0, 0.0], [0.0, 0.0], [0.0, 0.0], [0.0, 0.0]])


def test_a_grouped_mean_is_nan_where_a_class_has_no_fragment(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    block = bin_entry(
        grouped_s1(
            value={"score_id": "count"}, aggregate={"aggregator": "mean"}),
        CHR1, repo, genome)

    np.testing.assert_array_equal(block, [
        [5.0, 2.0], [np.nan, 3.0], [np.nan, np.nan], [np.nan, np.nan]])


def test_a_value_filter_selects_the_rows_of_a_literal(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    # Mapped through S2's rows instead of its own: AAA is a B cell there,
    # and BBB, CCC and DDD are not in them at all.
    meta = {
        "resource_id": "meta/cells",
        "filter": [{"column": "sample_id", "value": "S2"}],
    }

    run = parse_fragment_entry(grouped_s1(meta=meta), repo, genome)
    (block,) = bin_regions(run.jobs[0], [CHR1], repo)

    assert [t.group for t in run.tracks] == ["B", "T"]
    np.testing.assert_array_equal(
        block, [[1.0, 0.0], [1.0, 0.0], [0.0, 0.0], [0.0, 0.0]])


def pooled(**extra: Any) -> dict[str, Any]:
    return {
        "resource_query": "frags/*", "group": BY_CLASS,
        "meta": BY_SAMPLE_LABEL, **extra,
    }


def per_resource_blocks(
    entry: dict[str, Any], regions: list[BedRegion],
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> list[list[np.ndarray]]:
    """The blocks of each resource's own job, the entry unpooled."""
    run = parse_fragment_entry({**entry, "pool": False}, repo, genome)
    assert [job.tracks[0].resource_ids for job in run.jobs] == [
        ("frags/s1",), ("frags/s2",)]
    return [bin_regions(job, regions, repo) for job in run.jobs]


def test_a_pooled_entry_is_one_job_named_by_its_query(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    run = parse_fragment_entry(pooled(), repo, genome)

    assert len(run.jobs) == 1
    assert [(t.name, t.group, t.resource_ids) for t in run.tracks] == [
        ("frags/*:B", "B", ("frags/s1", "frags/s2")),
        ("frags/*:T", "T", ("frags/s1", "frags/s2")),
    ]


@pytest.mark.parametrize("group,expected", [
    (None, "frags/*:all"), ({"group": "bulk"}, "frags/*:bulk")])
def test_a_pooled_entry_without_metadata_is_named_by_its_query(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
    group: dict[str, str] | None, expected: str,
) -> None:
    entry: dict[str, Any] = {"resource_query": "frags/*"}
    if group is not None:
        entry["group"] = group

    run = parse_fragment_entry(entry, repo, genome)

    assert [(t.name, t.resource_ids) for t in run.tracks] == [
        (expected, ("frags/s1", "frags/s2"))]


@pytest.mark.parametrize("extra", [
    {}, {"value": {"score_id": "count"}},
    {"value": {"score_id": "count"}, "aggregate": {"aggregator": "count"}}])
def test_a_pooled_count_or_sum_is_the_sum_of_the_per_resource_blocks(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
    extra: dict[str, Any],
) -> None:
    # frags/s2 has no chr2: its chr2 block is all 0, and the pooled chr2
    # block is frags/s1's alone rather than poisoned.
    entry = pooled(**extra)
    regions = [CHR1, BedRegion("chr2", 1, 40)]
    (job,) = parse_fragment_entry(entry, repo, genome).jobs

    blocks = bin_regions(job, regions, repo)

    s1, s2 = per_resource_blocks(entry, regions, repo, genome)
    for index, block in enumerate(blocks):
        np.testing.assert_array_equal(block, s1[index] + s2[index])
    np.testing.assert_array_equal(s2[1], np.zeros((4, 2)))


def test_a_pooled_mean_is_over_the_merged_fragments(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    # Columns B, T.  1-10: B is S1's BBB (5) and S2's AAA (7); T is S1's
    # AAA (2).  11-20: T is S1's AAA (3) and S2's EEE (1, 4) -- 8 / 3,
    # where a mean of the two samples' means would be 2.75.  31-40: S2's
    # EEE (2).  On chr2 only S1 speaks: its BBB (6).
    (job,) = parse_fragment_entry(
        pooled(value={"score_id": "count"},
               aggregate={"aggregator": "mean"}),
        repo, genome).jobs

    chr1, chr2 = bin_regions(job, [CHR1, BedRegion("chr2", 1, 40)], repo)

    nan = np.nan
    np.testing.assert_allclose(
        chr1, [[6.0, 2.0], [nan, 8 / 3], [nan, nan], [nan, 2.0]])
    np.testing.assert_array_equal(
        chr2, [[6.0, nan], [nan, nan], [nan, nan], [nan, nan]])


@pytest.mark.parametrize("entry,fragments", [
    # A typo must not silently bin with the default.
    ({"resource_query": "frags/s1", "poool": False}, ["'poool'"]),
    ({"resource_query": "frags/s9"},
     ["'frags/s9'", "matches no fragment_score resource"]),
    ({"resource_query": "scores/*"},
     ["matches no fragment_score resource"]),
    ({"resource_query": "frags/s1", "pool": "no"}, ["pool"]),
    # /values is one float64 matrix (D11).
    ({"resource_query": "frags/s1", "value": {"score_id": "cell"}},
     ["binners[0].value", "'cell'", "'str'"]),
    ({"resource_query": "frags/s1", "value": {"score_id": "reads"}},
     ["binners[0].value", "'reads'"]),
    ({"resource_query": "frags/s1", "value": {"score_id": "count"},
      "aggregate": {"aggregator": "mode"}},
     ["aggregator 'mode' does not produce a number", "sum"]),
    ({"resource_query": "frags/s1",
      "aggregate": {"aggregator": "join(,)"}},
     ["aggregator 'join(,)' does not produce a number"]),
    ({"resource_query": "frags/s1", "value": {"value": "two"}},
     ["binners[0].value", "value must be a number", "'two'"]),
    ({"resource_query": "frags/s1", "value": {"value": True}},
     ["binners[0].value", "value must be a number", "True"]),
    # A YAML list where a name belongs is refused, not an unhashable crash.
    ({"resource_query": "frags/s1", "aggregate": {"aggregator": ["sum"]}},
     ["binners[0].aggregate", "aggregator", "['sum']"]),
    ({"resource_query": "frags/s1", "value": {"score_id": ["count"]}},
     ["binners[0].value", "score_id", "['count']"]),
    ({"resource_query": "frags/s1", "value": {"scor": "count"}},
     ["binners[0].value", "'scor'"]),
    ({"resource_query": "frags/s1", "aggregate": {"modus": "sum"}},
     ["binners[0].aggregate", "'modus'"]),
    ({"resource_query": "frags/s1",
      "value": {"score_id": "count", "value": 1}},
     ["binners[0].value", "score_id", "value", "not both"]),
    ({"resource_query": "frags/s1", "value": {}},
     ["binners[0].value", "give one of score_id or value"]),
    ({"resource_query": "frags/s1",
      "group": {"group": "bulk", **BY_CLASS}, "meta": BY_SAMPLE_LABEL},
     ["binners[0].group", "not both"]),
    ({"resource_query": "frags/s1", "group": {"group": ""}}, ["group"]),
    ({"resource_query": "frags/s1", "group": BY_CLASS}, ["meta"]),
    ({"resource_query": "frags/s1", "meta": BY_SAMPLE_LABEL}, ["meta"]),
    ({"resource_query": "frags/s1", "group": BY_CLASS,
      "meta": {"resource_id": "meta/cells", "where": []}},
     ["binners[0].meta", "'where'"]),
    ({"resource_query": "frags/s1", "group": BY_CLASS,
      "meta": {"resource_id": "scores/one"}}, ["'scores/one'"]),
    ({"resource_query": "frags/s1",
      "group": {**BY_CLASS, "group_meta_column": "subclass"},
      "meta": BY_SAMPLE_LABEL}, ["'subclass'"]),
    ({"resource_query": "frags/s1",
      "group": {**BY_CLASS, "cell_score_id": "barcode"},
      "meta": BY_SAMPLE_LABEL}, ["'barcode'"]),
    ({"resource_query": "frags/s1", "group": BY_CLASS,
      "meta": {"resource_id": "meta/cells", "filter": [
          {"column": "sample_id", "value": "S1", "label": "sample_id"}]}},
     ["binners[0].meta.filter[0]", "one of value or label"]),
    ({"resource_query": "frags/s1", "group": BY_CLASS,
      "meta": {"resource_id": "meta/cells", "filter": [
          {"column": "sample_id", "label": "donor"}]}},
     ["'frags/s1'", "'donor'"]),
    # A sample with no rows, or rows with no class, has no track at all.
    ({"resource_query": "frags/s1", "group": BY_CLASS,
      "meta": {"resource_id": "meta/cells", "filter": [
          {"column": "sample_id", "value": "S9"}]}},
     ["frags/s1", "name no group"]),
])
def test_a_malformed_fragment_entry_is_a_parse_error_naming_what_is_wrong(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
    entry: dict[str, Any], fragments: list[str],
) -> None:
    with pytest.raises(RunDefinitionError) as excinfo:
        parse_fragment_entry(entry, repo, genome)

    message = str(excinfo.value)
    assert message.startswith("binners[0]")
    for fragment in fragments:
        assert fragment in message


class ReadLedger:
    """Every starting-in read a binding opens, and whether it ran out.

    Wraps the read at its class, so the ledger sees exactly what the
    binding asked of each score: one entry per read, set ``drained`` only
    when the read's own generator was exhausted.
    """

    def __init__(self) -> None:
        self.reads: list[dict[str, Any]] = []

    def wrap(self, read: Any) -> Any:
        ledger = self

        def counted(
            score: FragmentScore, *args: Any, **kwargs: Any,
        ) -> Iterator[Any]:
            entry = {"resource": score.resource_id, "drained": False}
            ledger.reads.append(entry)
            rows = read(score, *args, **kwargs)

            def drain() -> Iterator[Any]:
                yield from rows
                entry["drained"] = True
            return drain()
        return counted


def test_a_binding_opens_each_resource_once_and_drains_every_read(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
    mocker: pytest_mock.MockerFixture,
) -> None:
    # Three regions of a pooled job over two resources: one open each
    # for the whole bundle, and after every region each read it started
    # has run to its end -- frags/s2 is not read on chr2, which it lacks.
    opens = mocker.spy(FragmentScore, "open")
    ledger = ReadLedger()
    mocker.patch.object(
        FragmentScore, "get_fragment_scores_starting_in_region",
        ledger.wrap(FragmentScore.get_fragment_scores_starting_in_region))
    (job,) = parse_fragment_entry(pooled(), repo, genome).jobs
    regions = [
        BedRegion("chr1", 1, 20), BedRegion("chr1", 21, 40),
        BedRegion("chr2", 1, 40)]
    after_each_region = []

    with discover_binner_kinds()[job.binner].bind(job, repo) as bound:
        for region in regions:
            bound.bin_region(region, BIN_SIZE)
            after_each_region.append(
                [(r["resource"], r["drained"]) for r in ledger.reads])

    assert opens.call_count == 2
    assert after_each_region == [
        [("frags/s1", True), ("frags/s2", True)],
        [("frags/s1", True), ("frags/s2", True)] * 2,
        [("frags/s1", True), ("frags/s2", True)] * 2 + [("frags/s1", True)],
    ]


def test_one_track_under_two_aggregators_carries_each_aggregator(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    # The repetition rule (D10) keys on the name and the group: the
    # repeated B and T tracks each carry their aggregator, while the
    # position-score track beside them keeps its bare id.
    config = {
        "bins": {"bin_size": BIN_SIZE},
        "binners": [
            {KIND: grouped_s1()},
            {"position_score_binner": {"resource_query": "scores/one"}},
            {KIND: grouped_s1(
                value={"score_id": "count"},
                aggregate={"aggregator": "mean"})},
        ],
    }

    run = parse_run_definition(config, repo, genome)

    assert [(t.name, t.group) for t in run.tracks] == [
        ("frags/s1:B:sum", "B"), ("frags/s1:T:sum", "T"),
        ("scores/one", ""),
        ("frags/s1:B:mean", "B"), ("frags/s1:T:mean", "T"),
    ]


def test_one_track_twice_under_one_aggregator_is_refused(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    config = {
        "bins": {"bin_size": BIN_SIZE},
        "binners": [
            {KIND: {"resource_query": "frags/s1", "pool": False}},
            {KIND: {"resource_query": "frags/s1", "pool": False,
                    "value": {"value": 2}}},
        ],
    }

    with pytest.raises(RunDefinitionError) as excinfo:
        parse_run_definition(config, repo, genome)

    message = str(excinfo.value)
    assert "'frags/s1:all:sum'" in message
    assert "binners[0]" in message
    assert "binners[1]" in message


@pytest.mark.parametrize("entry", [
    {"resource_query": "frags/s1", "pool": False},
    {"resource_query": "frags/*", "value": {"score_id": "count"},
     "aggregate": {"mode": "fragment_start", "aggregator": "mean"}},
])
def test_a_fragment_track_carries_its_mode_into_its_chunk_key(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
    entry: dict[str, Any],
) -> None:
    # The parameters key a track's chunks: a mode, or an uncovered value,
    # that differed would be another chunk.
    run = parse_fragment_entry(entry, repo, genome)

    for track in run.tracks:
        assert (track.mode, track.uncovered_value) == ("fragment_start", None)
        parameters = json.loads(track.parameters)
        assert parameters["mode"] == "fragment_start"
        assert parameters["uncovered_value"] is None
