# Fixture-heavy test module: pytest fixtures are injected by name (W0621,
# W0613) and tests are named by behavior (C0114, C0116); same set as
# test_binners.py.
# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pathlib
import textwrap
from dataclasses import replace
from typing import Any, cast

import h5py
import numpy as np
import pytest
from gain.binning.binners import (
    Binner,
    BinningJob,
    PositionScoreBinner,
    PositionScoreDef,
    load_track_def,
)
from gain.binning.cli import _bin_chunks, _chunk_path, _write_hdf5
from gain.binning.fragment_binner import FragmentScoreBinner
from gain.binning.fragment_def import FragmentScoreDef
from gain.binning.run_definition import RunDefinition
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.utils.regions import BedRegion

from tests.small.binning.test_binning_tool_cli import (
    MIXED_RUN_DEFINITION,
    TRACK,
    StubBinding,
    a_stub_binner,
    binning_tool,
    one_bin_of,
    write_run_definition,
)


@pytest.fixture
def output(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / "bins.h5"


@pytest.fixture
def mixed_run_definition(output: pathlib.Path) -> pathlib.Path:
    return write_run_definition(output, MIXED_RUN_DEFINITION)


# tracks_layout 2: every column means the same for every kind, and what
# is specific to a kind is one canonical JSON definition.


def read_tracks(path: pathlib.Path) -> tuple[Any, dict[str, Any]]:
    with h5py.File(path, "r") as h5:
        return np.asarray(h5["tracks"]), dict(h5.attrs)


def test_tracks_layout_2_has_the_same_columns_for_every_kind(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path,
    mixed_run_definition: pathlib.Path, output: pathlib.Path,
) -> None:
    binning_tool(mixed_run_definition, grr_dir, output)

    tracks, attrs = read_tracks(output)

    assert attrs["tracks_layout"] == 2
    assert tracks.dtype.names == (
        "binner_type", "binner_index", "track_index", "resource_ids",
        "selector", "binner_def")
    text = h5py.string_dtype(encoding="utf-8")
    assert [tracks.dtype[name] for name in tracks.dtype.names] == [
        text, np.dtype("<i8"), np.dtype("<i8"), text, text, text]
    assert [
        (row["binner_type"], row["binner_index"], row["track_index"],
         row["resource_ids"], row["selector"])
        for row in tracks
    ] == [
        (b"fragment_score", 0, 0, b"frags/s1,frags/s2", b"B"),
        (b"fragment_score", 0, 1, b"frags/s1,frags/s2", b"T"),
        (b"fragment_score", 1, 0, b"frags/s1", b"all"),
        (b"position_score", 2, 0, b"scores/one", b"s"),
    ]


def test_binner_def_is_the_canonical_json_of_each_kind(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path,
    mixed_run_definition: pathlib.Path, output: pathlib.Path,
) -> None:
    binning_tool(mixed_run_definition, grr_dir, output)

    tracks, _ = read_tracks(output)

    defs = [row["binner_def"] for row in tracks]
    assert defs[0] == (
        b'{"aggregator":"sum","mode":"fragment_start","name":null,'
        b'"pool":true,"resource_query":"frags/*","score_id":null,'
        b'"uncovered_value":null}')
    assert defs[2] == (
        b'{"aggregator":"sum","mode":"fragment_start","name":null,'
        b'"pool":false,"resource_query":"frags/s1","score_id":null,'
        b'"uncovered_value":null}')
    assert defs[3] == (
        b'{"aggregator":"max","name":null,"none_value_replacement":null}')


def test_a_definition_survives_to_json_then_from_json() -> None:
    fragment = FragmentScoreDef(
        aggregator="mean", mode="coverage_profile", uncovered_value=0.5,
        score_id="count", resource_query="frags/*", pool=False, name="x")
    position = PositionScoreDef(
        aggregator="min", none_value_replacement=-1.5, name="y")

    assert FragmentScoreDef.from_json(fragment.to_json()) == fragment
    assert PositionScoreDef.from_json(position.to_json()) == position


def test_a_binner_type_with_no_installed_kind_reads_as_a_plain_dict() -> None:
    text = '{"b":1,"a":[null]}'

    assert load_track_def("gone_plugin", text, kinds={}) == {
        "a": [None], "b": 1}


def test_an_installed_kind_reads_its_binner_def_as_its_definition() -> None:
    kinds = cast(dict[str, type[Binner]], {
        "position_score_binner": PositionScoreBinner,
        "fragment_score_binner": FragmentScoreBinner})
    position = PositionScoreDef(aggregator="max")

    assert load_track_def(
        "position_score", position.to_json(), kinds) == position


def test_a_kind_without_load_def_runs_and_reads_as_a_plain_dict(
    tmp_path: pathlib.Path,
) -> None:
    stub = a_stub_binner(StubBinding(one_bin_of(1.0)))
    assert not hasattr(stub, "load_def")
    region = BedRegion("chr1", 1, 10)
    run = RunDefinition(
        reference_genome="genome", bin_size=10, regions=[region],
        jobs=[BinningJob(binner="stub_binner", tracks=(TRACK,))])
    chunk = _chunk_path(str(tmp_path), TRACK, region, 10)
    _bin_chunks(
        stub, run.jobs[0], [region], 10, cast(Any, None), str(tmp_path))
    output = tmp_path / "out.h5"

    _write_hdf5(str(output), run, [[chunk]])

    tracks, _ = read_tracks(output)
    assert load_track_def(
        tracks[0]["binner_type"].decode(), tracks[0]["binner_def"].decode(),
        {"stub_binner": stub}) == {
            "aggregator": "max", "name": None,
            "none_value_replacement": None}


def test_binner_index_and_track_index_are_unique_and_follow_output_order(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path,
    mixed_run_definition: pathlib.Path, output: pathlib.Path,
) -> None:
    binning_tool(mixed_run_definition, grr_dir, output)

    tracks, _ = read_tracks(output)

    pairs = [(row["binner_index"], row["track_index"]) for row in tracks]
    assert len(set(pairs)) == len(pairs)
    # The grouped, pooled entry 0 holds two tracks: track_index counts
    # within the entry, not across the file.
    assert pairs == [(0, 0), (0, 1), (1, 0), (2, 0)]


def test_the_reference_genome_attribute_replaces_the_old_name(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path,
    mixed_run_definition: pathlib.Path, output: pathlib.Path,
) -> None:
    binning_tool(mixed_run_definition, grr_dir, output)

    _, attrs = read_tracks(output)

    assert attrs["reference_genome"] == "genome"
    assert "input_reference_genome" not in attrs


def test_a_nan_in_a_definition_is_refused_and_does_not_reach_the_file(
    tmp_path: pathlib.Path,
) -> None:
    nan_track = replace(
        TRACK,
        definition=PositionScoreDef(
            aggregator="max", none_value_replacement=float("nan")))
    run = RunDefinition(
        reference_genome="genome", bin_size=10,
        regions=[BedRegion("chr1", 1, 10)],
        jobs=[BinningJob(binner="stub_binner", tracks=(nan_track,))])
    chunk = _chunk_path(str(tmp_path), nan_track, run.regions[0], 10)
    np.save(chunk, np.array([1.0]))
    output = tmp_path / "out.h5"

    with pytest.raises(ValueError, match="not JSON compliant"):
        _write_hdf5(str(output), run, [[chunk]])

    assert not output.exists()


def test_binner_def_pins_every_field_of_a_fully_set_fragment_entry(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path, output: pathlib.Path,
) -> None:
    # Every nullable field is set, the name is not ASCII, and the pooled
    # entry matches one resource: `pool` is what tells it from an
    # unpooled entry of the same query.
    definition = write_run_definition(output, textwrap.dedent("""
        reference_genome: genome
        bins:
          bin_size: 10
          regions: ["chr1:1-40"]
        binners:
        - fragment_score_binner:
            name: kläss-é
            resource_query: frags/s1
            pool: true
            value: {score_id: count}
            aggregate:
              mode: coverage_profile
              aggregator: mean
              uncovered_value: 0.5
    """))

    binning_tool(definition, grr_dir, output)

    tracks, _ = read_tracks(output)
    assert [row["binner_def"] for row in tracks] == [(
        '{"aggregator":"mean","mode":"coverage_profile","name":"kläss-é",'
        '"pool":true,"resource_query":"frags/s1","score_id":"count",'
        '"uncovered_value":0.5}').encode()]
