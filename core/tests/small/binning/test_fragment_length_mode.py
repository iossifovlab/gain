# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""``fragment_score_binner`` in ``fragment_length`` mode, at the binner.

A fragment adds its value to every bin it overlaps, weighted by the base
pairs of the overlap.  The toy fragments are the conftest's; bins are 10
wide, so on chr1 the fragments of ``frags/s1`` cover each bin as follows
(``value: {value: 1}``, so a bin's ``sum`` is its covered base pairs):

=========  ====  =====  =====  =====  =====  ======================
fragment   cell  1-10   11-20  21-30  31-40  class (S1 rows)
=========  ====  =====  =====  =====  =====  ======================
(3, 12)    AAA   8      2                    T
(10, 25)   BBB   1      10     5             B
(11, 14)   CCC          4                    empty: no group
(15, 40)   AAA          6      10     10     T
(33, 34)   DDD                        2      not in the table
=========  ====  =====  =====  =====  =====  ======================

On chr2 ``frags/s1`` has one BBB fragment, (5, 9); ``frags/s2`` has no
chr2.  On chr1 ``frags/s2`` has AAA (class B in S2) at (4, 8) and EEE
(class T) at (12, 20), (16, 22) and (35, 50).
"""
import datetime
import pathlib
import textwrap
from typing import Any

import h5py
import numpy as np
import pytest
from gain.binning.binners import discover_binner_kinds
from gain.binning.fragment_binner import FragmentScoreBinding
from gain.genomic_resources.reference_genome import ReferenceGenome
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.utils.regions import BedRegion

from tests.small.binning.test_binning_tool_cli import (
    binning_tool,
    read_matrix,
    write_run_definition,
)
from tests.small.binning.test_fragment_binner import (
    BIN_SIZE,
    BY_CLASS,
    BY_SAMPLE_LABEL,
    bin_entry,
    bin_regions,
    parse_fragment_entry,
)

CHR1 = BedRegion("chr1", 1, 40)
CHR2 = BedRegion("chr2", 1, 40)
nan = np.nan


def length(
    *, aggregator: str = "sum", uncovered_value: float | None = None,
    **entry: Any,
) -> dict[str, Any]:
    """A ``fragment_length`` entry; unpooled ``frags/s1`` unless given."""
    return {
        "resource_query": "frags/s1", "pool": False, **entry,
        "aggregate": {"mode": "fragment_length", "aggregator": aggregator,
                      "uncovered_value": uncovered_value},
    }


def test_each_bin_sums_the_base_pairs_its_fragments_overlap(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    block = bin_entry(length(), CHR1, repo, genome)

    np.testing.assert_array_equal(block[:, 0], [9.0, 22.0, 15.0, 12.0])


@pytest.mark.parametrize("entry", [
    length(),
    length(aggregator="mean", uncovered_value=0),
    length(resource_query="frags/*", pool=True, group=BY_CLASS,
           meta=BY_SAMPLE_LABEL, aggregator="max"),
])
def test_adjacent_regions_bin_a_crossing_fragment_as_their_union_does(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
    entry: dict[str, Any],
) -> None:
    # (10, 25) and (15, 40) of frags/s1, and (16, 22) of frags/s2, cross
    # 20/21: each region adds its own part of them, and no more.
    (job,) = parse_fragment_entry(entry, repo, genome).jobs
    union = BedRegion("chr1", 1, 40)
    halves = [BedRegion("chr1", 1, 20), BedRegion("chr1", 21, 40)]

    (whole,) = bin_regions(job, [union], repo)
    parts = bin_regions(job, halves, repo)

    np.testing.assert_array_equal(np.vstack(parts), whole)


def test_a_fragment_reaching_out_of_the_region_adds_only_its_inner_part(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    # 21-30 of frags/s1: (10, 25) adds 21-25, (15, 40) 21-30.
    block = bin_entry(length(), BedRegion("chr1", 21, 30), repo, genome)

    np.testing.assert_array_equal(block[:, 0], [15.0])


@pytest.mark.parametrize("uncovered_value, expected", [
    (None, [nan, nan, nan, nan]),
    (0, [0.0, 0.0, 0.0, 0.0]),
    (-1.5, [-1.5, -1.5, -1.5, -1.5]),
])
def test_a_contig_the_resource_lacks_is_uncovered_throughout(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
    uncovered_value: float | None, expected: list[float],
) -> None:
    # frags/s2 has no chr2: without an uncovered value its bins are
    # empty, NaN for mean; with one, every base of chr2 holds it.
    block = bin_entry(
        length(resource_query="frags/s2", aggregator="mean",
               uncovered_value=uncovered_value),
        CHR2, repo, genome)

    np.testing.assert_array_equal(block[:, 0], expected)


@pytest.mark.parametrize("uncovered_value, expected", [
    # B: S1's BBB (5, 9) covers 5 bp of 1-10; T: no fragment on chr2.
    (None, [[1.0, nan], [nan, nan], [nan, nan], [nan, nan]]),
    (0, [[0.5, 0.0], [0.0, 0.0], [0.0, 0.0], [0.0, 0.0]]),
])
def test_a_pooled_contig_one_resource_lacks_holds_the_other_s_fragments(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
    uncovered_value: float | None, expected: list[list[float]],
) -> None:
    block = bin_entry(
        length(resource_query="frags/*", pool=True, group=BY_CLASS,
               meta=BY_SAMPLE_LABEL, aggregator="mean",
               uncovered_value=uncovered_value),
        CHR2, repo, genome)

    np.testing.assert_array_equal(block, expected)


def bound_block(
    entry: dict[str, Any], region: BedRegion,
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> tuple[np.ndarray, dict[tuple[str, str], int]]:
    """The block of a one-job entry over ``region``, and its dropped counts."""
    (job,) = parse_fragment_entry(entry, repo, genome).jobs
    with discover_binner_kinds()[job.binner].bind(job, repo) as bound:
        assert isinstance(bound, FragmentScoreBinding)
        block = bound.bin_region(region, BIN_SIZE)
        return block, bound.dropped


def test_a_grouped_entry_weighs_each_class_and_counts_the_dropped(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    # B is BBB (10, 25); T is AAA (3, 12) and (15, 40); CCC (no class)
    # and DDD (not in the table) are dropped.
    block, dropped = bound_block(
        length(group=BY_CLASS, meta=BY_SAMPLE_LABEL), CHR1, repo, genome)

    np.testing.assert_array_equal(
        block, [[1.0, 8.0], [10.0, 8.0], [5.0, 10.0], [0.0, 10.0]])
    assert dropped == {("frags/s1", "chr1:1-40"): 2}


def test_a_pooled_entry_weighs_the_fragments_of_every_resource(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    # S2 adds AAA (4, 8) to B, and EEE (12, 20), (16, 22) and (35, 50),
    # clipped to 35-40, to T.
    block, dropped = bound_block(
        length(resource_query="frags/*", pool=True, group=BY_CLASS,
               meta=BY_SAMPLE_LABEL), CHR1, repo, genome)

    np.testing.assert_array_equal(
        block, [[6.0, 8.0], [10.0, 22.0], [5.0, 12.0], [0.0, 16.0]])
    assert dropped == {
        ("frags/s1", "chr1:1-40"): 2, ("frags/s2", "chr1:1-40"): 0}


def test_a_raw_value_grouped_entry_weighs_each_value(
    stats_repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    block, dropped = bound_block(
        length(group={"group_score_id": "cell"}), CHR1, stats_repo, genome)

    # AAA, BBB, CCC, DDD.
    np.testing.assert_array_equal(block, [
        [8.0, 1.0, 0.0, 0.0],
        [8.0, 10.0, 4.0, 0.0],
        [10.0, 5.0, 0.0, 0.0],
        [10.0, 0.0, 0.0, 2.0],
    ])
    assert dropped == {("frags/s1", "chr1:1-40"): 0}


@pytest.fixture
def output(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / "bins.h5"


def split_run(*aggregates: str) -> str:
    """A run of one unpooled ``frags/s1`` entry per aggregate.

    chr1 is two adjacent regions, split where (10, 25) and (15, 40)
    cross, and chr2 a third.
    """
    return textwrap.dedent("""
        input_reference_genome: genome
        bins:
          bin_size: 10
          regions: ["chr1:1-20", "chr1:21-40", chr2]
        binners:
    """) + "".join(
        f"- fragment_score_binner: {{resource_query: frags/s1, "
        f"pool: false, name: e{index}, aggregate: {aggregate}}}\n"
        for index, aggregate in enumerate(aggregates))


LENGTH_SUM = "{mode: fragment_length}"
LENGTH_MEAN_0 = "{mode: fragment_length, aggregator: mean, uncovered_value: 0}"
#: chr1 1-40 then chr2 1-40: the fragment base pairs per bin, and the
#: mean of their value 1 and an uncovered base's 0 -- in chr1 1-10, 9 bp
#: of fragments and 2 uncovered bases, 1 and 2.
LENGTH_EXPECTED = [
    [9.0, 9 / 11], [22.0, 1.0], [15.0, 1.0], [12.0, 1.0],
    [5.0, 0.5], [0.0, 0.0], [0.0, 0.0], [0.0, 0.0],
]


@pytest.mark.parametrize("budget", [(), ("--task-budget", "0")])
def test_a_length_file_is_byte_for_byte_the_same_whatever_the_budget(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path, output: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch, budget: tuple[str, ...],
) -> None:
    class Frozen(datetime.datetime):
        @classmethod
        def now(cls, tz: Any = None) -> "Frozen":
            return cls(2026, 10, 7, tzinfo=tz)

    monkeypatch.setattr("gain.binning.cli.datetime.datetime", Frozen)
    definition = write_run_definition(
        output, split_run(LENGTH_SUM, LENGTH_MEAN_0))
    binning_tool(definition, grr_dir, output, "--task-budget", "1")
    cut = output.read_bytes()
    output.unlink()

    binning_tool(definition, grr_dir, output, *budget)

    assert output.read_bytes() == cut
    np.testing.assert_allclose(read_matrix(output), LENGTH_EXPECTED)


def test_tracks_records_a_length_track_s_mode_and_uncovered_value(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path, output: pathlib.Path,
) -> None:
    binning_tool(
        write_run_definition(output, split_run(LENGTH_SUM, LENGTH_MEAN_0)),
        grr_dir, output)

    with h5py.File(output, "r") as h5:
        tracks = h5["tracks"][()]
    assert [row["mode"] for row in tracks] == [
        b"fragment_length", b"fragment_length"]
    np.testing.assert_array_equal(tracks["uncovered_value"], [nan, 0.0])


@pytest.mark.parametrize("first, second", [
    ("{mode: fragment_start}", LENGTH_SUM),
    (LENGTH_SUM, "{mode: fragment_length, uncovered_value: 1}"),
    ("{mode: fragment_length, uncovered_value: 1}",
     "{mode: fragment_length, uncovered_value: 2}"),
])
def test_a_rerun_after_a_change_of_mode_or_uncovered_value_bins_again(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path, output: pathlib.Path,
    first: str, second: str,
) -> None:
    # The work directory is kept between the runs; the second's chunks
    # must be its own, as a fresh run in another directory computes them.
    definition = write_run_definition(output, split_run(first))
    binning_tool(definition, grr_dir, output, "--keep-work-dir")
    before = read_matrix(output)
    output.unlink()
    fresh = output.parent / "fresh" / "bins.h5"
    fresh.parent.mkdir()
    binning_tool(
        write_run_definition(fresh, split_run(second)), grr_dir, fresh)

    binning_tool(
        write_run_definition(output, split_run(second)), grr_dir, output,
        "--keep-work-dir")

    np.testing.assert_array_equal(read_matrix(output), read_matrix(fresh))
    assert not np.array_equal(read_matrix(output), before)


def test_dry_run_reports_a_length_entry_s_mode_and_uncovered_value(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path, output: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    binning_tool(
        write_run_definition(output, split_run(LENGTH_SUM, LENGTH_MEAN_0)),
        grr_dir, output, "--dry-run")

    entries = capsys.readouterr().out.split("binners[")[1:]
    assert len(entries) == 2
    assert (
        "    mode: fragment_length\n"
        "    uncovered_value: null\n") in entries[0]
    assert (
        "    mode: fragment_length\n"
        "    uncovered_value: 0.0\n") in entries[1]


def test_entries_differing_only_in_mode_or_uncovered_value_bin_apart(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path, output: pathlib.Path,
) -> None:
    # The tracks agree on resource, group, value and aggregator, so only
    # the mode and the uncovered value keep their chunks apart.
    binning_tool(
        write_run_definition(output, split_run(
            "{mode: fragment_start}", LENGTH_SUM,
            "{mode: fragment_length, uncovered_value: 1}")),
        grr_dir, output)

    values = read_matrix(output)
    np.testing.assert_array_equal(values[:4, 0], [2.0, 2.0, 0.0, 1.0])
    np.testing.assert_array_equal(values[:4, 1], [9.0, 22.0, 15.0, 12.0])
    np.testing.assert_array_equal(values[:4, 2], [11.0, 22.0, 15.0, 12.0])
