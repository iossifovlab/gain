# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""``binning_tool`` on convention-resolved fragment entries.

What the dry run reports per fragment entry, and what a run records of
the local metadata files it read.  The toy GRR is this module's own: a
labelled fragment resource, ``frags/lab``, an unlabelled single-cell
one, ``frags/bare``, and the conftest's ``CELL_META`` as ``meta/cells``.
"""
import datetime
import os
import pathlib
import textwrap

import h5py
import numpy as np
import pytest
from gain.binning.fragment_binner import (
    CELL_META_RESOURCE_ID_LABEL,
    CELL_SCORE,
    COUNT_SCORE,
    SAMPLE_ID_COLUMN,
    SAMPLE_ID_LABEL,
)
from gain.genomic_resources.testing.builders import (
    a_fragment_score,
    a_grr,
    a_reference_genome,
)
from gain.genomic_resources.testing.data_frame_builder import a_data_frame

from tests.small.binning.conftest import CELL_META, S1_FRAGMENTS
from tests.small.binning.test_binning_tool_cli import binning_tool


def single_cell(**labels: str) -> object:
    return (
        a_fragment_score()
        .with_score(CELL_SCORE, "str").with_score(COUNT_SCORE, "int")
        .with_labels(**labels).with_tabix().with_data(S1_FRAGMENTS))


@pytest.fixture
def grr_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "grr"
    (
        a_grr()
        .with_resource("genome", a_reference_genome()
                       .with_chromosome("chr1", "A" * 100)
                       .with_chromosome("chr2", "C" * 40))
        .with_resource("meta/cells", a_data_frame()
                       .with_raw_content(CELL_META))
        .with_resource("frags/lab", single_cell(**{
            SAMPLE_ID_LABEL: "S1",
            CELL_META_RESOURCE_ID_LABEL: "meta/cells"}))
        .with_resource("frags/bare", single_cell(**{SAMPLE_ID_LABEL: "S1"}))
    ).build_repo(path)
    return path


def run_definition(directory: pathlib.Path, *entries: str) -> pathlib.Path:
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


LOCAL_META = (
    "{resource_query: frags/bare, group: {}, meta: {file_name: cells.csv, "
    f"filter: [{{column: {SAMPLE_ID_COLUMN}, label: {SAMPLE_ID_LABEL}}}]}}}}")
SHARED_META = (
    "{resource_query: frags/bare, group: {}, meta: {resource_id: meta/cells, "
    f"filter: [{{column: {SAMPLE_ID_COLUMN}, label: {SAMPLE_ID_LABEL}}}]}}}}")


def test_dry_run_reports_each_fragment_entry_and_writes_nothing(
    tmp_path: pathlib.Path, grr_dir: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    definition = run_definition(
        tmp_path / "defs",
        "{resource_query: frags/lab}",
        '{resource_query: "frags/*", pool: false,'
        ' aggregate: {score: count, aggregator: mean}}',
        "{resource_query: frags/bare}")
    output = tmp_path / "defs" / "bins.h5"

    binning_tool(definition, grr_dir, output, "--dry-run")

    out = capsys.readouterr().out
    assert (
        "binners[0]: fragment_score_binner\n"
        "    resources: frags/lab\n"
        "    tables: meta/cells\n"
        "    groups: 2\n"
        "    tracks: 2\n") in out
    assert (
        "binners[1]: fragment_score_binner\n"
        "    resources: frags/bare, frags/lab\n"
        "    tables: meta/cells\n"
        "    groups: 3\n"
        "    tracks: 3\n") in out
    assert (
        "binners[2]: fragment_score_binner\n"
        "    resources: frags/bare\n"
        "    tables: none\n"
        "    groups: 1\n"
        "    tracks: 1\n"
        "    warning: resource 'frags/bare' has") in out
    assert f"no {CELL_META_RESOURCE_ID_LABEL!r} label" in out
    assert "binned as the single 'all' track" in out
    assert out.count("warning:") == 2
    assert not output.exists()
    assert not (tmp_path / "defs" / "bins_work").exists()


def test_a_relative_local_table_is_read_from_the_run_definition_s_directory(
    tmp_path: pathlib.Path, grr_dir: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Launched from elsewhere, with the run definition named relative to
    # the launch directory: cells.csv sits beside the run definition.
    shared = run_definition(tmp_path / "shared", SHARED_META)
    binning_tool(shared, grr_dir, tmp_path / "shared.h5")
    run_definition(tmp_path / "defs", LOCAL_META)
    (tmp_path / "defs" / "cells.csv").write_text(CELL_META)
    (tmp_path / "elsewhere").mkdir()
    monkeypatch.chdir(tmp_path / "elsewhere")

    binning_tool(
        pathlib.Path("../defs/run.yaml"), grr_dir,
        pathlib.Path("local.h5"))

    with h5py.File(tmp_path / "shared.h5", "r") as h5:
        expected = h5["values"][()]
    with h5py.File(tmp_path / "elsewhere" / "local.h5", "r") as h5:
        np.testing.assert_array_equal(h5["values"][()], expected)


def test_a_run_records_each_local_table_s_path_size_and_mtime(
    tmp_path: pathlib.Path, grr_dir: pathlib.Path,
) -> None:
    definition = run_definition(
        tmp_path / "defs", LOCAL_META,
        LOCAL_META.replace("cells.csv", "other/cells.csv").replace(
            "frags/bare", "frags/lab"))
    first = tmp_path / "defs" / "cells.csv"
    second = tmp_path / "defs" / "other" / "cells.csv"
    second.parent.mkdir()
    first.write_text(CELL_META)
    second.write_text(CELL_META + "S9,ZZZ,Z\n")
    os.utime(first, (1_700_000_000, 1_700_000_000))
    output = tmp_path / "bins.h5"

    binning_tool(definition, grr_dir, output)

    with h5py.File(output, "r") as h5:
        attrs = dict(h5.attrs)
    assert list(attrs["metadata_files"]) == [str(first), str(second)]
    assert list(attrs["metadata_file_sizes"]) == [
        first.stat().st_size, second.stat().st_size]
    assert list(attrs["metadata_file_mtimes"]) == [
        datetime.datetime.fromtimestamp(path.stat().st_mtime, datetime.UTC)
        .isoformat()
        for path in (first, second)]
    assert attrs["metadata_file_mtimes"][0].startswith("2023-11-14T22:13:20")


def test_dry_run_warns_a_local_table_is_not_reproducible_elsewhere(
    tmp_path: pathlib.Path, grr_dir: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    definition = run_definition(tmp_path / "defs", LOCAL_META)
    (tmp_path / "defs" / "cells.csv").write_text(CELL_META)

    binning_tool(definition, grr_dir, tmp_path / "bins.h5", "--dry-run")

    out = capsys.readouterr().out
    local = tmp_path / "defs" / "cells.csv"
    assert f"    tables: {local}\n" in out
    assert (
        f"    warning: the cell metadata table {str(local)!r} is a local "
        f"file; the run is not reproducible elsewhere") in out
    assert not (tmp_path / "bins.h5").exists()
