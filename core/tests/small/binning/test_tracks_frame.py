# Fixture-heavy test module: pytest fixtures are injected by name (W0621)
# and tests are named by behavior (C0114, C0116).
# pylint: disable=W0621,C0114,C0116
"""``tracks_frame``: the ``/tracks`` rows of a list of jobs."""
import pathlib
import types

import gain.binning
import pandas as pd
import yaml
from gain.binning import tracks_frame
from gain.binning.h5_reader import read_tracks
from gain.binning.run_definition import resolve_binners
from gain.genomic_resources.repository import GenomicResourceRepo

from tests.small.binning.test_binning_tool_cli import (
    MIXED_RUN_DEFINITION,
    binning_tool,
    write_run_definition,
)


def test_tracks_frame_equals_the_tracks_table_of_the_file(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    output = tmp_path / "bins.h5"
    binning_tool(
        write_run_definition(output, MIXED_RUN_DEFINITION), grr_dir, output)
    jobs = resolve_binners(
        yaml.safe_load(MIXED_RUN_DEFINITION)["binners"], repo)

    frame = tracks_frame(jobs)

    pd.testing.assert_frame_equal(frame, read_tracks(output))


def test_the_public_names_of_gain_binning_are_its_all() -> None:
    public = {
        name for name, value in vars(gain.binning).items()
        if not name.startswith("_")
        and not isinstance(value, types.ModuleType)}

    assert sorted(gain.binning.__all__) == [
        "Aggregate", "CellGrouping", "ConstantContribution", "ConstantGroup",
        "FragmentEntry", "MetaFilter", "MetaSource", "RunDefinitionError",
        "ScoreContribution", "ValueGrouping", "resolve_binners",
        "tracks_frame"]
    assert public == set(gain.binning.__all__)
