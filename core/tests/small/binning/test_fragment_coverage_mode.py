# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""``fragment_score_binner`` in ``coverage_profile`` mode, at the binner.

Each track's fragments, clipped to the region, make a coverage profile
-- per position, the sum of the values of the fragments over it -- and
each bin reduces the profile weighted by base pairs; a position the
profile leaves at 0 is uncovered, and adds the ``uncovered_value``,
``0`` unless given.  The toy fragments are the conftest's (see
``test_fragment_length_mode``'s table); ``frags/worked`` holds the two
fragments of the brief's worked example, ``(6, 14)`` and ``(11, 18)``.
"""
import datetime
import json
import pathlib
from typing import Any

import h5py
import numpy as np
import pytest
from gain.binning.binners import RunDefinitionError
from gain.genomic_resources.reference_genome import (
    ReferenceGenome,
    build_reference_genome_from_resource,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing.builders import (
    a_fragment_score,
    a_grr,
    a_reference_genome,
)
from gain.utils.regions import BedRegion

from tests.small.binning.test_binning_tool_cli import (
    binning_tool,
    read_matrix,
    write_run_definition,
)
from tests.small.binning.test_fragment_binner import (
    BY_CLASS,
    BY_SAMPLE_LABEL,
    bin_entry,
    bin_regions,
    parse_fragment_entry,
)
from tests.small.binning.test_fragment_length_mode import (
    CHR1,
    CHR2,
    bound_block,
    length,
    split_run,
)

nan = np.nan
_ABSENT = object()


def coverage(
    *, aggregator: str = "sum", uncovered_value: Any = _ABSENT,
    **entry: Any,
) -> dict[str, Any]:
    """A ``coverage_profile`` entry; unpooled ``frags/s1`` unless given.

    ``uncovered_value`` is left out of the entry unless given.
    """
    aggregate: dict[str, Any] = {
        "mode": "coverage_profile", "aggregator": aggregator}
    if uncovered_value is not _ABSENT:
        aggregate["uncovered_value"] = uncovered_value
    return {
        "resource_query": "frags/s1", "pool": False, **entry,
        "aggregate": aggregate,
    }


@pytest.fixture
def worked_repo(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    return (
        a_grr()
        .with_resource("genome", a_reference_genome()
                       .with_chromosome("chr1", "A" * 30))
        .with_resource("frags/worked", a_fragment_score()
                       .with_score("cell", "str")
                       .with_tabix()
                       .with_data("""
                           chrom  pos_begin  pos_end  cell
                           chr1   6          14       AAA
                           chr1   11         18       BBB
                       """))
    ).build_repo(tmp_path / "worked")


@pytest.fixture
def worked_genome(worked_repo: GenomicResourceRepo) -> ReferenceGenome:
    return build_reference_genome_from_resource(
        worked_repo.get_resource("genome")).open()


@pytest.mark.parametrize("uncovered, expected", [
    ({}, [0.5, 1.2, 0.0]),
    ({"uncovered_value": None}, [1.0, 1.5, nan]),
])
def test_the_worked_example_s_mean(
    worked_repo: GenomicResourceRepo, worked_genome: ReferenceGenome,
    uncovered: dict[str, Any], expected: list[float],
) -> None:
    block = bin_entry(
        coverage(resource_query="frags/worked", aggregator="mean",
                 **uncovered),
        BedRegion("chr1", 1, 30), worked_repo, worked_genome)

    np.testing.assert_array_equal(block[:, 0], expected)


@pytest.mark.parametrize("entry", [
    {"value": {"score_id": "count"}},
    {"resource_query": "frags/*", "pool": True, "group": BY_CLASS,
     "meta": BY_SAMPLE_LABEL},
])
@pytest.mark.parametrize("region", [CHR1, CHR2])
def test_the_sum_is_the_fragment_length_sum(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
    entry: dict[str, Any], region: BedRegion,
) -> None:
    # Integer values: both sums are exact, so they agree exactly.
    profile = bin_entry(coverage(**entry), region, repo, genome)
    lengths = bin_entry(length(**entry), region, repo, genome)

    np.testing.assert_array_equal(profile, lengths)


def test_a_pooled_group_folds_one_profile_of_every_resource_s_fragments(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    # T: S1's AAA (3, 12) and (15, 40) with S2's EEE (12, 20), (16, 22)
    # and (35, 50): the profile is 2 at 12, 3 on 16-20, 2 on 21-22 and on
    # 35-40.  B: S2's AAA (4, 8) and S1's BBB (10, 25) do not overlap.
    # A profile per resource would reach at most 2 in 11-20 and 1 in
    # 31-40.
    block = bin_entry(
        coverage(resource_query="frags/*", pool=True, group=BY_CLASS,
                 meta=BY_SAMPLE_LABEL, aggregator="max"),
        CHR1, repo, genome)

    np.testing.assert_array_equal(
        block, [[1.0, 1.0], [1.0, 3.0], [1.0, 2.0], [0.0, 2.0]])


@pytest.mark.parametrize("entry", [
    coverage(),
    coverage(aggregator="mean"),
    coverage(resource_query="frags/*", pool=True, group=BY_CLASS,
             meta=BY_SAMPLE_LABEL, aggregator="max"),
])
def test_adjacent_regions_bin_a_crossing_fragment_as_their_union_does(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
    entry: dict[str, Any],
) -> None:
    # (10, 25) and (15, 40) of frags/s1, and (16, 22) of frags/s2, cross
    # 20/21: each region profiles its own part of them.
    (job,) = parse_fragment_entry(entry, repo, genome).jobs
    halves = [BedRegion("chr1", 1, 20), BedRegion("chr1", 21, 40)]

    (whole,) = bin_regions(job, [CHR1], repo)
    parts = bin_regions(job, halves, repo)

    np.testing.assert_array_equal(np.vstack(parts), whole)


@pytest.mark.parametrize("uncovered, expected", [
    ({}, [0.0, 0.0, 0.0, 0.0]),
    ({"uncovered_value": None}, [nan, nan, nan, nan]),
])
def test_a_contig_the_resource_lacks_follows_the_uncovered_value(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
    uncovered: dict[str, Any], expected: list[float],
) -> None:
    block = bin_entry(
        coverage(resource_query="frags/s2", aggregator="mean", **uncovered),
        CHR2, repo, genome)

    np.testing.assert_array_equal(block[:, 0], expected)


def test_a_grouped_entry_profiles_each_class_and_counts_the_dropped(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    # B is BBB (10, 25); T is AAA (3, 12) and (15, 40); CCC (no class)
    # and DDD (not in the table) are dropped.  The mean counts each
    # uncovered base as 0.
    block, dropped = bound_block(
        coverage(group=BY_CLASS, meta=BY_SAMPLE_LABEL, aggregator="mean"),
        CHR1, repo, genome)

    np.testing.assert_allclose(
        block, [[0.1, 0.8], [1.0, 0.8], [0.5, 1.0], [0.0, 1.0]])
    assert dropped == {("frags/s1", "chr1:1-40"): 2}


def test_a_raw_value_grouped_entry_profiles_each_value(
    stats_repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    block, dropped = bound_block(
        coverage(group={"group_score_id": "cell"}, aggregator="max"),
        CHR1, stats_repo, genome)

    # AAA, BBB, CCC, DDD.
    np.testing.assert_array_equal(block, [
        [1.0, 1.0, 0.0, 0.0],
        [1.0, 1.0, 1.0, 0.0],
        [1.0, 1.0, 0.0, 0.0],
        [1.0, 0.0, 0.0, 1.0],
    ])
    assert dropped == {("frags/s1", "chr1:1-40"): 0}


@pytest.mark.parametrize("aggregate, expected", [
    ({"mode": "coverage_profile"}, 0.0),
    ({"mode": "coverage_profile", "uncovered_value": None}, None),
    ({"mode": "coverage_profile", "uncovered_value": -2}, -2.0),
    ({"mode": "fragment_length"}, None),
])
def test_an_omitted_uncovered_value_is_0_only_in_coverage_profile_mode(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
    aggregate: dict[str, Any], expected: float | None,
) -> None:
    run = parse_fragment_entry(
        {"resource_query": "frags/s1", "pool": False, "aggregate": aggregate},
        repo, genome)

    (track,) = run.tracks
    assert (track.mode, track.uncovered_value) == (
        aggregate["mode"], expected)
    assert json.loads(track.parameters)["uncovered_value"] == expected


@pytest.mark.parametrize("aggregate, fragments", [
    ({"aggregator": "count"},
     ["aggregator 'count'", "coverage_profile",
      "aggregator: sum with value: {value: 1}"]),
    ({"aggregator": "product"},
     ["aggregator 'product'", "coverage_profile",
      "use one of max, mean, median, min, sum"]),
    ({"uncovered_value": "zero"},
     ["uncovered_value must be a number or null", "'zero'"]),
    ({"uncovered_value": float("nan")},
     ["uncovered_value must be a number or null", "nan"]),
])
def test_a_coverage_aggregate_it_cannot_honour_is_refused(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
    aggregate: dict[str, Any], fragments: list[str],
) -> None:
    with pytest.raises(RunDefinitionError) as excinfo:
        parse_fragment_entry(coverage(**aggregate), repo, genome)

    message = str(excinfo.value)
    assert message.startswith("binners[0].aggregate")
    for fragment in fragments:
        assert fragment in message


@pytest.fixture
def output(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / "bins.h5"


COVERAGE_MEAN = "{mode: coverage_profile, aggregator: mean}"
COVERAGE_MEAN_NULL = (
    "{mode: coverage_profile, aggregator: mean, uncovered_value: null}")
#: chr1 1-40 then chr2 1-40, the mean of frags/s1's profile: in chr1
#: 1-10, 3-9 is 1 and 10 is 2, so 9 / 10 with the default and 9 / 8
#: without it; chr2 has (5, 9) only.
COVERAGE_EXPECTED = [
    [0.9, 9 / 8], [2.2, 22 / 10], [1.5, 1.5], [1.2, 1.2],
    [0.5, 1.0], [0.0, nan], [0.0, nan], [0.0, nan],
]


@pytest.mark.parametrize("budget", [(), ("--task-budget", "0")])
def test_a_coverage_file_is_byte_for_byte_the_same_whatever_the_budget(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path, output: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch, budget: tuple[str, ...],
) -> None:
    class Frozen(datetime.datetime):
        @classmethod
        def now(cls, tz: Any = None) -> "Frozen":
            return cls(2026, 10, 7, tzinfo=tz)

    monkeypatch.setattr("gain.binning.cli.datetime.datetime", Frozen)
    definition = write_run_definition(
        output, split_run(COVERAGE_MEAN, COVERAGE_MEAN_NULL))
    binning_tool(definition, grr_dir, output, "--task-budget", "1")
    cut = output.read_bytes()
    output.unlink()

    binning_tool(definition, grr_dir, output, *budget)

    assert output.read_bytes() == cut
    np.testing.assert_allclose(read_matrix(output), COVERAGE_EXPECTED)


def test_tracks_records_a_coverage_track_s_mode_and_uncovered_value(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path, output: pathlib.Path,
) -> None:
    binning_tool(
        write_run_definition(
            output, split_run(COVERAGE_MEAN, COVERAGE_MEAN_NULL)),
        grr_dir, output)

    with h5py.File(output, "r") as h5:
        tracks = h5["tracks"][()]
    assert [row["mode"] for row in tracks] == [
        b"coverage_profile", b"coverage_profile"]
    np.testing.assert_array_equal(tracks["uncovered_value"], [0.0, nan])


def test_a_rerun_after_a_change_to_coverage_profile_bins_again(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path, output: pathlib.Path,
) -> None:
    # The work directory is kept between the runs; the second's chunks
    # must be its own, as a fresh run in another directory computes them.
    first = "{mode: fragment_length, aggregator: mean}"
    definition = write_run_definition(output, split_run(first))
    binning_tool(definition, grr_dir, output, "--keep-work-dir")
    before = read_matrix(output)
    output.unlink()
    fresh = output.parent / "fresh" / "bins.h5"
    fresh.parent.mkdir()
    binning_tool(
        write_run_definition(fresh, split_run(COVERAGE_MEAN)), grr_dir, fresh)

    binning_tool(
        write_run_definition(output, split_run(COVERAGE_MEAN)), grr_dir,
        output, "--keep-work-dir")

    np.testing.assert_array_equal(read_matrix(output), read_matrix(fresh))
    assert not np.array_equal(read_matrix(output), before)
