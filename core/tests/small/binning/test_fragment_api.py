# Fixture-heavy test module: pytest fixtures are injected by name (W0621)
# and tests are named by behavior (C0114, C0116).
# pylint: disable=W0621,C0114,C0116
"""``cell_groups`` and ``fragment_tracks``: an entry without a run.

The toy GRR holds the fragments of two samples, ``frags/s1`` and
``frags/s2`` (the conftest's ``S1_FRAGMENTS`` and ``S2_FRAGMENTS``),
with ``sample_id`` labels and no cell-metadata label, ``conv/s1``, the
S1 fragments under the GRR label convention, and ``meta/cells``, the
conftest's ``CELL_META``.  ``mix/labelled`` and ``mix/plain`` disagree
on the convention label.  The statistics are built, so a value
grouping reads the values of ``cell`` from its histogram.
"""
import functools
import pathlib
from collections.abc import Callable
from typing import Any

import pandas as pd
import pytest
import yaml
from gain.binning import (
    CellGrouping,
    FragmentEntry,
    MetaFilter,
    MetaSource,
    RunDefinitionError,
    cell_groups,
    fragment_tracks,
    read_tracks,
)
from gain.binning.fragment_binner import (
    FragmentBinningJob,
    FragmentScoreBinner,
)
from gain.binning.run_definition import resolve_binners
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing import build_filesystem_test_repository
from gain.genomic_resources.testing.builders import a_grr
from gain.genomic_resources.testing.data_frame_builder import a_data_frame
from gain.genomic_resources.testing.reference_genome_builder import (
    a_reference_genome,
)
from gain.genomic_resources.testing.table_score_builder import a_fragment_score

from tests.small.binning.conftest import (
    CELL_META,
    CHR1_LENGTH,
    S1_FRAGMENTS,
    S2_FRAGMENTS,
)
from tests.small.binning.test_binning_tool_cli import binning_tool

KIND = "fragment_score_binner"
CONVENTION: dict[str, Any] = {"cell_meta_resource_id": "meta/cells"}


def fragments(data: str, **labels: Any) -> Any:
    return (
        a_fragment_score()
        .with_score("cell", "str")
        .with_score("count", "int")
        .with_labels(**labels)
        .with_tabix()
        .with_data(data))


@pytest.fixture(scope="module")
def grr_dir(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    return tmp_path_factory.mktemp("fragment_api") / "grr"


@pytest.fixture(scope="module")
def repo(grr_dir: pathlib.Path) -> GenomicResourceRepo:
    """The toy GRR, built once: no test of the module writes to it."""
    (
        a_grr()
        .with_resource("genome", a_reference_genome()
                       .with_chromosome("chr1", "A" * CHR1_LENGTH))
        .with_resource("frags/s1", fragments(S1_FRAGMENTS, sample_id="S1"))
        .with_resource("frags/s2", fragments(S2_FRAGMENTS, sample_id="S2"))
        .with_resource("conv/s1", fragments(
            S1_FRAGMENTS, sample_id="S1", **CONVENTION))
        .with_resource("mix/labelled", fragments(
            S1_FRAGMENTS, sample_id="S1", **CONVENTION))
        .with_resource("mix/plain", fragments(S2_FRAGMENTS, sample_id="S2"))
        .with_resource("meta/cells", a_data_frame()
                       .with_raw_content(CELL_META))
        .build_repo(grr_dir))
    cli_manage(["repo-stats", "-R", str(grr_dir), "-j", "1"])
    return build_filesystem_test_repository(grr_dir)


def run_tool(
    entry: dict[str, Any], grr_dir: pathlib.Path, work: pathlib.Path,
) -> pathlib.Path:
    """Bin ``entry`` alone with ``binning_tool``; return the output file.

    The run definition sits in ``work``, beside the local cell metadata
    file ``cells.csv`` that an entry may name.
    """
    work.mkdir()
    (work / "cells.csv").write_text(CELL_META)
    run_definition = work / "run.yaml"
    run_definition.write_text(yaml.safe_dump({
        "reference_genome": "genome",
        "bins": {"bin_size": 10, "regions": ["chr1:1-40"]},
        "binners": [{KIND: entry}],
    }))
    output = work / "bins.h5"
    binning_tool(run_definition, grr_dir, output)
    return output


BY_SAMPLE = [{"column": "sample_id", "label": "sample_id"}]
CELL_KEYS = {
    "cell_score_id": "cell", "cell_meta_column": "barcode",
    "group_meta_column": "class"}
#: An entry as a dict, and as the hand-built object it reads as.
BY_CELL_CLASS: dict[str, Any] = {
    "resource_query": "frags/*", "group": {},
    "meta": {"resource_id": "meta/cells", "filter": BY_SAMPLE}}
BY_CELL_CLASS_ENTRY = FragmentEntry(
    "frags/*", group=CellGrouping(), meta=MetaSource(
        resource_id="meta/cells",
        filters=(MetaFilter("sample_id", label="sample_id"),)))
#: Each case: the entry that ``binning_tool`` runs, and the entry that
#: the functions get.
PARITY_CASES: dict[str, tuple[dict[str, Any], Any]] = {
    "constant group": (
        {"resource_query": "frags/s1", "group": {"group": "x"}}, None),
    "cell grouping by its keys, from a resource": (
        {"resource_query": "frags/*", "group": CELL_KEYS,
         "meta": {"resource_id": "meta/cells", "filter": BY_SAMPLE}}, None),
    "cell grouping by its defaults, from a local file": (
        {"resource_query": "frags/s1", "group": {},
         "meta": {"file_name": "cells.csv", "filter": BY_SAMPLE}}, None),
    "pooled value grouping": (
        {"resource_query": "frags/*",
         "group": {"group_score_id": "cell"}}, None),
    "unpooled value grouping": (
        {"resource_query": "frags/*", "pool": False,
         "group": {"group_score_id": "cell"}}, None),
    "no group on a labelled resource": ({"resource_query": "conv/s1"}, None),
    "no group on unlabelled resources": (
        {"resource_query": "frags/*"}, None),
    "hand-built entry": (BY_CELL_CLASS, BY_CELL_CLASS_ENTRY),
}


#: The cases whose tracks are groups of keys; the others are one track.
GROUPED_CASES = set(PARITY_CASES) - {
    "constant group", "no group on unlabelled resources"}


def given_entry(case: str) -> Any:
    config, entry = PARITY_CASES[case]
    return config if entry is None else entry


def test_the_hand_built_entry_reads_as_its_dict() -> None:
    assert FragmentScoreBinner.entry_from_config(
        "binners[0]", BY_CELL_CLASS) == BY_CELL_CLASS_ENTRY


@pytest.mark.parametrize("case", list(PARITY_CASES))
def test_fragment_tracks_equals_the_tracks_table_of_a_binning_tool_run(
    case: str, repo: GenomicResourceRepo, grr_dir: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    output = run_tool(PARITY_CASES[case][0], grr_dir, tmp_path / "run")

    frame = fragment_tracks(
        repo, given_entry(case), base_dir=str(tmp_path / "run"))

    pd.testing.assert_frame_equal(frame, read_tracks(output))


def bound_groups(
    config: dict[str, Any], repo: GenomicResourceRepo, base_dir: str,
) -> list[tuple[str, str, str]]:
    """Each (resource id, key, group) that the track maps of ``bind`` hold.

    The rows come in the order of the resources, then of the keys.
    """
    rows: list[tuple[str, str, str]] = []
    for job in resolve_binners([{KIND: config}], repo, base_dir=base_dir):
        assert isinstance(job, FragmentBinningJob)
        track_maps = FragmentScoreBinner.bind(job, repo).track_maps
        if track_maps is None:
            continue
        for resource_id, track_map in zip(
                job.resource_ids, track_maps, strict=True):
            rows.extend(sorted(
                (resource_id, key, job.tracks[index].group)
                for key, index in track_map.items()))
    return rows


@pytest.mark.parametrize("case", list(PARITY_CASES))
def test_cell_groups_maps_each_key_to_the_track_that_bind_sends_it_to(
    case: str, repo: GenomicResourceRepo, tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "cells.csv").write_text(CELL_META)
    expected = bound_groups(PARITY_CASES[case][0], repo, str(tmp_path))
    assert bool(expected) == (case in GROUPED_CASES)

    frame = cell_groups(repo, given_entry(case), base_dir=str(tmp_path))

    assert list(frame.columns) == ["resource_id", "score_value", "group"]
    assert list(frame.itertuples(index=False, name=None)) == expected


def test_a_constant_group_gives_an_empty_frame_with_the_three_columns(
    repo: GenomicResourceRepo,
) -> None:
    frame = cell_groups(
        repo, {"resource_query": "frags/*", "group": {"group": "x"}})

    assert frame.empty
    assert list(frame.columns) == ["resource_id", "score_value", "group"]


def test_fragment_tracks_gives_each_row_the_binner_index(
    repo: GenomicResourceRepo,
) -> None:
    frame = fragment_tracks(
        repo, {"resource_query": "frags/*", "pool": False}, binner_index=2)

    assert frame["binner_index"].tolist() == [2, 2]
    assert frame["track_index"].tolist() == [0, 1]


def test_fragment_tracks_equals_its_rows_in_a_run_with_a_sibling_entry(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """A sibling with the same name and group leaves the rows unchanged.

    ``binning_tool`` names the tracks of all entries together, and the
    two entries of the run carry ``:<aggregator>``.  The ``/tracks``
    rows hold no name, so the row of the second entry is the row that
    ``fragment_tracks`` gives for that entry alone.
    """
    by_max = {"resource_query": "frags/s1", "group": {"group": "x"},
              "aggregate": {"aggregator": "max"}}
    work = tmp_path / "pair"
    work.mkdir()
    run_definition = work / "run.yaml"
    run_definition.write_text(yaml.safe_dump({
        "reference_genome": "genome",
        "bins": {"bin_size": 10, "regions": ["chr1:1-40"]},
        "binners": [
            {KIND: {**by_max, "aggregate": {"aggregator": "sum"}}},
            {KIND: by_max}],
    }))
    binning_tool(run_definition, grr_dir, work / "bins.h5")
    pair = read_tracks(work / "bins.h5")

    frame = fragment_tracks(repo, by_max, binner_index=1)

    pd.testing.assert_frame_equal(
        frame, pair[pair["binner_index"] == 1].reset_index(drop=True))


REFUSALS: dict[str, dict[str, Any]] = {
    "no matching resource": {"resource_query": "nothing/*"},
    "a contribution score that is not numeric": {
        "resource_query": "frags/s1", "value": {"score_id": "cell"}},
    "pooled resources that disagree on the label": {
        "resource_query": "mix/*"},
    "metadata rows with no group": {
        "resource_query": "frags/s1", "group": {},
        "meta": {"resource_id": "meta/cells",
                 "filter": [{"column": "sample_id", "value": "S9"}]}},
}
#: Each function, called as a notebook user calls it, and the label of
#: the entry that it passes on.
CALLS: dict[str, tuple[Callable[..., Any], str]] = {
    "cell_groups": (cell_groups, "binners[0]"),
    "fragment_tracks": (
        functools.partial(fragment_tracks, binner_index=2), "binners[2]"),
}


@pytest.mark.parametrize("call", list(CALLS))
@pytest.mark.parametrize("refusal", list(REFUSALS))
def test_a_refusal_is_the_one_of_parse_entry_for_the_same_label(
    refusal: str, call: str, repo: GenomicResourceRepo,
) -> None:
    function, label = CALLS[call]
    config = REFUSALS[refusal]
    with pytest.raises(RunDefinitionError) as expected:
        FragmentScoreBinner.parse_entry(label, config, repo)

    with pytest.raises(RunDefinitionError) as refused:
        function(repo, config)

    assert str(refused.value) == str(expected.value)
    assert str(refused.value).startswith(label)
