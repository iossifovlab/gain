# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""``fragment_score_binner`` grouped by the raw value of a ``str`` score.

``group: {group_score_id: S}`` gives one track per distinct value of
``S``, the set read from the score's full categorical histogram.  The
toy GRR is the conftest's, with its statistics built (``stats_repo``):
``frags/s1``'s ``cell`` values are AAA, BBB, CCC and DDD, and
``frags/s2``'s are AAA and EEE.  Bins are 10 wide; summing ``count``
over chr1:1-40, ``frags/s1`` bins to

=====  ====  ====  ====  ====
bin    AAA   BBB   CCC   DDD
=====  ====  ====  ====  ====
1-10   2     5     0     0
11-20  3     0     1     0
21-30  0     0     0     0
31-40  0     0     0     4
=====  ====  ====  ====  ====

and ``frags/s2`` to AAA ``[7, 0, 0, 0]`` and EEE ``[0, 5, 0, 2]``.
"""
import datetime
import gzip
import json
import logging
import pathlib
import textwrap
from typing import Any

import h5py
import numpy as np
import pytest
from gain.binning.binners import BinningJob, discover_binner_kinds
from gain.binning.fragment_binner import FragmentScoreBinding
from gain.binning.run_definition import (
    RunDefinition,
    RunDefinitionError,
    parse_run_definition,
)
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.reference_genome import ReferenceGenome
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing import build_filesystem_test_repository
from gain.genomic_resources.testing.builders import (
    a_fragment_score,
    a_grr,
    a_reference_genome,
)
from gain.utils.regions import BedRegion

from tests.small.binning.conftest import S1_FRAGMENTS
from tests.small.binning.test_binning_tool_cli import binning_tool

BIN_SIZE = 10
KIND = "fragment_score_binner"
CHR1 = BedRegion("chr1", 1, 40)
BY_CELL = {"group_score_id": "cell"}
S1_BY_CELL = [
    [2.0, 5.0, 0.0, 0.0],
    [3.0, 0.0, 1.0, 0.0],
    [0.0, 0.0, 0.0, 0.0],
    [0.0, 0.0, 0.0, 4.0],
]


def parse(
    entry: dict[str, Any], repo: GenomicResourceRepo,
    genome: ReferenceGenome,
) -> RunDefinition:
    return parse_run_definition({
        "bins": {"bin_size": BIN_SIZE},
        "binners": [{KIND: entry}],
    }, repo, genome)


def bin_job(
    job: BinningJob, region: BedRegion, repo: GenomicResourceRepo,
) -> np.ndarray:
    with discover_binner_kinds()[job.binner].bind(job, repo) as bound:
        return bound.bin_region(region, BIN_SIZE)


def test_an_unpooled_entry_is_one_track_per_value_in_sorted_order(
    stats_repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    run = parse({"resource_query": "frags/s1", "pool": False,
                 "group": BY_CELL}, stats_repo, genome)

    assert [(t.name, t.group) for t in run.tracks] == [
        ("frags/s1:AAA", "AAA"), ("frags/s1:BBB", "BBB"),
        ("frags/s1:CCC", "CCC"), ("frags/s1:DDD", "DDD"),
    ]
    (job,) = run.jobs
    np.testing.assert_array_equal(
        bin_job(job, CHR1, stats_repo), S1_BY_CELL)


def test_a_pooled_entry_prefixes_each_group_with_its_sample(
    stats_repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    # AAA recurs in both samples, and is two tracks.
    run = parse({"resource_query": "frags/*", "group": BY_CELL},
                stats_repo, genome)

    assert [(t.name, t.group) for t in run.tracks] == [
        ("frags/*:S1:AAA", "S1:AAA"), ("frags/*:S1:BBB", "S1:BBB"),
        ("frags/*:S1:CCC", "S1:CCC"), ("frags/*:S1:DDD", "S1:DDD"),
        ("frags/*:S2:AAA", "S2:AAA"), ("frags/*:S2:EEE", "S2:EEE"),
    ]
    assert {t.resource_ids for t in run.tracks} == {("frags/s1", "frags/s2")}


def test_a_pooled_block_is_the_per_resource_blocks_side_by_side(
    stats_repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    (pooled,) = parse({"resource_query": "frags/*", "group": BY_CELL},
                      stats_repo, genome).jobs
    s1, s2 = parse({"resource_query": "frags/*", "pool": False,
                    "group": BY_CELL}, stats_repo, genome).jobs

    for region in (CHR1, BedRegion("chr2", 1, 40)):
        np.testing.assert_array_equal(
            bin_job(pooled, region, stats_repo),
            np.hstack([bin_job(s1, region, stats_repo),
                       bin_job(s2, region, stats_repo)]))
    np.testing.assert_array_equal(
        bin_job(s2, CHR1, stats_repo), [[7, 0], [0, 5], [0, 0], [0, 2]])


def test_a_track_s_parameters_record_the_grouping_score(
    stats_repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    # A raw-value track AAA and a constant track AAA differ in their
    # parameters, and so never share a chunk.
    by_value = parse({"resource_query": "frags/s1", "group": BY_CELL,
                      "pool": False}, stats_repo, genome).tracks[0]
    constant = parse({"resource_query": "frags/s1",
                      "group": {"group": "AAA"}}, stats_repo, genome).tracks[0]

    assert by_value.name == constant.name == "frags/s1:AAA"
    assert json.loads(by_value.parameters) == {"group_score_id": "cell"}
    assert by_value.parameters != constant.parameters


def refusal(
    entry: dict[str, Any], repo: GenomicResourceRepo,
    genome: ReferenceGenome,
) -> str:
    with pytest.raises(RunDefinitionError) as excinfo:
        parse(entry, repo, genome)
    message = str(excinfo.value)
    assert message.startswith("binners[0]")
    return message


@pytest.mark.parametrize("group,fragments", [
    # An unknown score names the resource's scores.
    ({"group_score_id": "barcode"},
     ["binners[0].group", "'frags/s1'", "no score 'barcode'"]),
    # Not a str score: count, aggregated by a constant here.
    ({"group_score_id": "count"},
     ["binners[0].group", "'frags/s1'", "'count'", "'int'"]),
    # Mixed with the constant or the metadata form.
    ({"group_score_id": "cell", "group": "all"},
     ["binners[0].group", "group_score_id", "not both"]),
    ({"group_score_id": "cell", "group_meta_column": "class"},
     ["binners[0].group", "group_score_id", "not both"]),
])
def test_a_malformed_value_grouping_is_refused_naming_what_is_wrong(
    stats_repo: GenomicResourceRepo, genome: ReferenceGenome,
    group: dict[str, Any], fragments: list[str],
) -> None:
    message = refusal({"resource_query": "frags/s1", "group": group,
                       "aggregate": {"value": 1}}, stats_repo, genome)

    for fragment in fragments:
        assert fragment in message


def test_grouping_by_the_aggregated_score_is_refused(
    stats_repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    # count is no str score either; the refusal says the first thing
    # wrong with it.
    message = refusal({"resource_query": "frags/s1",
                       "group": {"group_score_id": "count"},
                       "aggregate": {"score": "count"}}, stats_repo, genome)

    assert "binners[0].group" in message
    assert "'frags/s1'" in message
    assert "'count' is the aggregated score" in message


@pytest.mark.parametrize("aggregate", [None, {"value": 1}])
def test_meta_with_a_value_grouping_is_refused(
    stats_repo: GenomicResourceRepo, genome: ReferenceGenome,
    aggregate: dict[str, Any] | None,
) -> None:
    entry: dict[str, Any] = {
        "resource_query": "frags/s1", "group": BY_CELL,
        "meta": {"resource_id": "meta/cells"}}
    if aggregate is not None:
        entry["aggregate"] = aggregate

    message = refusal(entry, stats_repo, genome)

    assert "meta" in message
    assert "group_score_id" in message
    assert "'frags/s1'" in message


def stats_grr(
    tmp_path: pathlib.Path, *resources: tuple[str, Any],
) -> GenomicResourceRepo:
    """A GRR of the ``(resource id, builder)`` pairs, its statistics built."""
    root = tmp_path / "custom"
    grr = a_grr()
    for resource_id, builder in resources:
        grr = grr.with_resource(resource_id, builder)
    grr.build_repo(root)
    cli_manage(["repo-stats", "-R", str(root), "-j", "1"])
    return build_filesystem_test_repository(root)


def cells(**labels: Any) -> Any:
    return (
        a_fragment_score().with_score("cell", "str")
        .with_score("count", "int").with_labels(**labels)
        .with_tabix().with_data(S1_FRAGMENTS))


#: 150 fragments of 150 distinct cells: past the 100 distinct values the
#: default categorical histogram keeps.
MANY_CELLS = "chrom pos_begin pos_end cell count\n" + "\n".join(
    f"chr1 {i + 1} {i + 2} c{i:03d} 1" for i in range(150))


def many_cells(*, categorical: bool) -> Any:
    builder = a_fragment_score().with_score("cell", "str")
    if categorical:
        builder = builder.with_histogram(
            {"type": "categorical"}, score_id="cell")
    return (
        builder.with_score("count", "int").with_labels(sample_id="S1")
        .with_tabix().with_data(MANY_CELLS))


@pytest.mark.parametrize("resources,fragments", [
    # One resource of a pool without the label, or every one.
    ((("frags/a", cells(sample_id="S1")), ("frags/b", cells())),
     ["'frags/b' do not", "'sample_id'", "pool: false"]),
    ((("frags/a", cells()),), ["'frags/a' do not", "'sample_id'"]),
    # A list label is not one value: no "['S1', 'S2']:" prefix.
    ((("frags/a", cells(sample_id="S1")),
      ("frags/b", cells(sample_id=["S1", "S2"]))),
     ["'frags/b' do not", "'sample_id'", "one value"]),
])
def test_a_pooled_resource_without_its_sample_id_label_is_refused(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
    resources: tuple[tuple[str, Any], ...], fragments: list[str],
) -> None:
    repo = stats_grr(tmp_path, *resources)

    message = refusal(
        {"resource_query": "frags/*", "group": BY_CELL}, repo, genome)

    for fragment in fragments:
        assert fragment in message


def test_a_pooled_sample_id_with_a_colon_is_refused(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
) -> None:
    # ``S1`` with value ``A:B`` and ``S1:A`` with value ``B`` would both
    # be the group ``S1:A:B``: the ``:`` must split one way only.
    repo = stats_grr(
        tmp_path, ("frags/a", cells(sample_id="S1")),
        ("frags/b", cells(sample_id="S1:A")))

    message = refusal(
        {"resource_query": "frags/*", "group": BY_CELL}, repo, genome)

    assert "'frags/b'" in message
    assert "'S1:A'" in message
    assert "':'" in message


def test_a_sample_id_shared_by_pooled_resources_is_refused(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
) -> None:
    # Both would prefix their barcode ``AAA`` as ``S1:AAA``, merging two
    # resources' cells into one track.
    repo = stats_grr(
        tmp_path, ("frags/a", cells(sample_id="S1")),
        ("frags/b", cells(sample_id="S1")),
        ("frags/c", cells(sample_id="S2")))

    message = refusal(
        {"resource_query": "frags/*", "group": BY_CELL}, repo, genome)

    assert "'frags/a', 'frags/b'" in message
    assert "'S1'" in message
    assert "'frags/c'" not in message
    assert "pool: false" in message


def test_unpooled_resources_may_share_a_sample_id(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
) -> None:
    repo = stats_grr(
        tmp_path, ("frags/a", cells(sample_id="S1")),
        ("frags/b", cells(sample_id="S1")))

    run = parse({"resource_query": "frags/*", "pool": False,
                 "group": BY_CELL}, repo, genome)

    assert [t.name for t in run.tracks][:2] == ["frags/a:AAA", "frags/a:BBB"]
    assert len(run.tracks) == 8


def test_an_unpooled_resource_without_a_sample_id_label_has_bare_groups(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
) -> None:
    repo = stats_grr(tmp_path, ("frags/a", cells()))

    run = parse({"resource_query": "frags/a", "pool": False,
                 "group": BY_CELL}, repo, genome)

    assert [t.group for t in run.tracks] == ["AAA", "BBB", "CCC", "DDD"]


def test_a_resource_without_statistics_is_refused_saying_how_to_build_them(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    message = refusal({"resource_query": "frags/s1", "group": BY_CELL},
                      repo, genome)

    assert "'frags/s1'" in message
    assert "Histogram file not found." in message
    assert "dvc pull" in message
    assert "repo-stats" in message


def test_a_score_past_the_default_value_cap_is_refused_saying_to_declare_it(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
) -> None:
    repo = stats_grr(tmp_path, ("frags/many", many_cells(categorical=False)))

    message = refusal({"resource_query": "frags/many", "group": BY_CELL},
                      repo, genome)

    assert "'frags/many'" in message
    assert "Too many unique values" in message
    assert "histogram: {type: categorical}" in message


def store_null_histogram(
    tmp_path: pathlib.Path, resource_id: str, reason: str,
) -> None:
    """Overwrite the built ``cell`` histogram with a stored null one."""
    path = (tmp_path / "custom" / resource_id
            / "statistics/histogram_cell.json")
    path.write_text(json.dumps(
        {"config": {"type": "null", "reason": reason}}))


def test_the_cap_is_recognised_however_its_reason_is_worded(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
) -> None:
    # A default config stores a null histogram only when the scan passes
    # the cap; the decision does not read the reason's wording.
    repo = stats_grr(tmp_path, ("frags/s1", cells(sample_id="S1")))
    store_null_histogram(tmp_path, "frags/s1", "a reworded reason")

    message = refusal({"resource_query": "frags/s1", "group": BY_CELL},
                      repo, genome)

    assert "'a reworded reason'" in message
    assert "histogram: {type: categorical}" in message


def test_a_declared_categorical_score_with_a_null_histogram_is_rebuilt(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
) -> None:
    # A declared categorical histogram has no cap, so a stored null one
    # is a broken file, whatever its reason says.
    declared = (
        a_fragment_score().with_score("cell", "str")
        .with_histogram({"type": "categorical"}, score_id="cell")
        .with_score("count", "int").with_labels(sample_id="S1")
        .with_tabix().with_data(S1_FRAGMENTS))
    repo = stats_grr(tmp_path, ("frags/s1", declared))
    store_null_histogram(
        tmp_path, "frags/s1",
        "Too many unique values 101 for categorical histogram.")

    message = refusal({"resource_query": "frags/s1", "group": BY_CELL},
                      repo, genome)

    assert "'frags/s1'" in message
    assert "grr_manage repo-stats" in message
    assert "histogram: {type: categorical}" not in message


def test_a_declared_categorical_score_past_the_cap_groups_by_every_value(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
) -> None:
    repo = stats_grr(tmp_path, ("frags/many", many_cells(categorical=True)))

    run = parse({"resource_query": "frags/many", "group": BY_CELL},
                repo, genome)

    assert [t.group for t in run.tracks] == [
        f"S1:c{i:03d}" for i in range(150)]


def test_a_score_whose_histogram_is_annulled_is_refused_saying_to_declare_it(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
) -> None:
    # Annulled by the definition: no statistics rebuild can list values.
    annulled = (
        a_fragment_score().with_score("cell", "str")
        .with_histogram({"type": "null", "reason": "annulled"},
                        score_id="cell")
        .with_score("count", "int").with_labels(sample_id="S1")
        .with_tabix().with_data(S1_FRAGMENTS))
    repo = stats_grr(tmp_path, ("frags/s1", annulled))

    message = refusal({"resource_query": "frags/s1", "group": BY_CELL},
                      repo, genome)

    assert "'frags/s1'" in message
    assert "annul" in message
    assert "histogram: {type: categorical}" in message
    assert "dvc pull" not in message


FULL_HISTOGRAM = "statistics/histogram_cell.json.gz"
SIDECAR = "statistics/truncated/histogram_cell.json"


def test_a_histogram_whose_full_file_is_not_pulled_is_refused(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
) -> None:
    # The manifest lists the sidecar and the full file; only the sidecar
    # is on disk, as when its DVC blob is not pulled.
    repo = stats_grr(tmp_path, ("frags/many", many_cells(categorical=True)))
    (tmp_path / "custom" / "frags/many" / FULL_HISTOGRAM).unlink()

    message = refusal({"resource_query": "frags/many", "group": BY_CELL},
                      repo, genome)

    assert "'frags/many'" in message
    assert "is absent while its truncated sidecar exists" in message
    assert "dvc pull" in message


def test_a_truncated_histogram_is_refused(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
) -> None:
    # The full file holding only what the sidecar holds.
    repo = stats_grr(tmp_path, ("frags/many", many_cells(categorical=True)))
    resource_dir = tmp_path / "custom" / "frags/many"
    (resource_dir / FULL_HISTOGRAM).write_bytes(
        gzip.compress((resource_dir / SIDECAR).read_bytes()))

    message = refusal({"resource_query": "frags/many", "group": BY_CELL},
                      repo, genome)

    assert "'frags/many'" in message
    assert "histogram is truncated" in message


def test_a_value_outside_the_histogram_is_dropped_and_counted(
    tmp_path: pathlib.Path, genome: ReferenceGenome,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Statistics older than the data: the histogram lacks DDD, whose one
    # chr1 fragment then reaches no track.
    repo = stats_grr(tmp_path, ("frags/s1", cells(sample_id="S1")))
    path = tmp_path / "custom" / "frags/s1" / "statistics/histogram_cell.json"
    histogram = json.loads(path.read_text())
    del histogram["values"]["DDD"]
    path.write_text(json.dumps(histogram))
    (job,) = parse({"resource_query": "frags/s1", "group": BY_CELL},
                   repo, genome).jobs

    with caplog.at_level(logging.INFO, logger="gain.binning"), \
            discover_binner_kinds()[job.binner].bind(job, repo) as bound:
        block = bound.bin_region(CHR1, BIN_SIZE)

    assert isinstance(bound, FragmentScoreBinding)

    assert [t.group for t in job.tracks] == ["S1:AAA", "S1:BBB", "S1:CCC"]
    np.testing.assert_array_equal(
        block, [row[:3] for row in S1_BY_CELL])
    assert bound.dropped == {("frags/s1", "chr1:1-40"): 1}
    (logged,) = [r.getMessage() for r in caplog.records
                 if "dropped" in r.getMessage()]
    assert "1 fragments dropped" in logged
    assert "'cell' value" in logged
    # Names both causes: no value at all, and one the histogram lacks.
    assert "missing or not among" in logged
    assert "cell metadata" not in logged


#: A sample whose cell values carry a ``/`` and a ``:``.
ODD_FRAGMENTS = """
    chrom  pos_begin  pos_end  cell   count
    chr1   3          12       T/1:x  2
    chr1   15         40       AAA    3
    chr2   5          9        T/1:x  6
"""


@pytest.fixture
def cli_grr_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """A GRR for the tool: a genome, ``frags/odd`` and ``frags/s1``."""
    path = tmp_path / "cli_grr"
    (
        a_grr()
        .with_resource("genome", a_reference_genome()
                       .with_chromosome("chr1", "A" * 100)
                       .with_chromosome("chr2", "C" * 40))
        .with_resource("frags/odd", a_fragment_score()
                       .with_score("cell", "str").with_score("count", "int")
                       .with_labels(sample_id="S9")
                       .with_tabix().with_data(ODD_FRAGMENTS))
        .with_resource("frags/s1", cells(sample_id="S1"))
    ).build_repo(path)
    cli_manage(["repo-stats", "-R", str(path), "-j", "1"])
    return path


def tool_run_definition(directory: pathlib.Path, *entries: str) -> pathlib.Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "run.yaml"
    path.write_text(textwrap.dedent("""
        input_reference_genome: genome
        bins:
          bin_size: 10
          regions: ["chr1:1-40", chr2]
        binners:
    """) + "".join(
        f"- fragment_score_binner: {entry}\n" for entry in entries))
    return path


BY_CELL_ENTRY = "{resource_query: %s, group: {group_score_id: cell}}"


def test_dry_run_reports_the_histogram_s_group_count(
    tmp_path: pathlib.Path, cli_grr_dir: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    definition = tool_run_definition(
        tmp_path / "defs", BY_CELL_ENTRY % "frags/s1",
        BY_CELL_ENTRY % '"frags/*"')

    binning_tool(definition, cli_grr_dir, tmp_path / "bins.h5", "--dry-run")

    out = capsys.readouterr().out
    assert (
        "binners[0]: fragment_score_binner\n"
        "    resources: frags/s1\n"
        "    tables: none\n"
        "    groups: 4\n"
        "    tracks: 4\n") in out
    assert (
        "binners[1]: fragment_score_binner\n"
        "    resources: frags/odd, frags/s1\n"
        "    tables: none\n"
        "    groups: 6\n"
        "    tracks: 6\n") in out
    assert not (tmp_path / "bins.h5").exists()


def test_a_value_with_a_slash_or_colon_is_quoted_only_in_its_chunk_name(
    tmp_path: pathlib.Path, cli_grr_dir: pathlib.Path,
) -> None:
    definition = tool_run_definition(
        tmp_path / "defs", BY_CELL_ENTRY % "frags/odd")
    output = tmp_path / "bins.h5"

    binning_tool(definition, cli_grr_dir, output, "--keep-work-dir")

    with h5py.File(output, "r") as h5:
        tracks = h5["tracks"][()]
        values = h5["values"][()]
    assert [(row["name"], row["group"]) for row in tracks] == [
        (b"frags/odd:S9:AAA", b"S9:AAA"),
        (b"frags/odd:S9:T/1:x", b"S9:T/1:x"),
    ]
    np.testing.assert_array_equal(values[:4], [
        [0, 2], [3, 0], [0, 0], [0, 0]])
    chunks = sorted(
        path.name for path in (tmp_path / "bins_work" / "chunks").glob(
            "*.npy"))
    assert len(chunks) == 4
    assert sum("_gS9%3AT%2F1%3Ax_p" in name for name in chunks) == 2


@pytest.mark.parametrize("budget", [(), ("--task-budget", "0")])
def test_a_value_grouped_file_is_byte_for_byte_the_same_whatever_the_budget(
    tmp_path: pathlib.Path, cli_grr_dir: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch, budget: tuple[str, ...],
) -> None:
    class Frozen(datetime.datetime):
        @classmethod
        def now(cls, tz: Any = None) -> "Frozen":
            return cls(2026, 10, 2, tzinfo=tz)

    monkeypatch.setattr("gain.binning.cli.datetime.datetime", Frozen)
    definition = tool_run_definition(
        tmp_path / "defs", BY_CELL_ENTRY % '"frags/*"',
        '{resource_query: "frags/*", pool: false,'
        " group: {group_score_id: cell}}")
    output = tmp_path / "bins.h5"
    binning_tool(definition, cli_grr_dir, output, "--task-budget", "1")
    cut = output.read_bytes()
    output.unlink()

    binning_tool(definition, cli_grr_dir, output, *budget)

    assert output.read_bytes() == cut
