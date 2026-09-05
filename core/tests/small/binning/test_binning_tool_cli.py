# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pathlib
import shutil
import textwrap

import h5py
import numpy as np
import numpy.typing as npt
import pytest
from gain import __version__
from gain.binning.cli import cli
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing.builders import a_position_score

RUN_DEFINITION = textwrap.dedent("""
    bins:
      bin_size: 10
      regions: ["chr1:1-40", chr2]
    binners:
    - position_score_binner:
        resource_query: "scores/*"
""")

# scores/one (max): 1.0 over chr1:1-20, 2.0 over chr1:31-35, nothing on
# chr2.  scores/two (mean): 4.0 over chr1:1-10, nothing else.  Rows are
# chr1's four bins then chr2's four; columns are the two tracks by id.
NAN = np.nan
EXPECTED_VALUES = [
    [1.0, 4.0],
    [1.0, NAN],
    [NAN, NAN],
    [2.0, NAN],
    [NAN, NAN],
    [NAN, NAN],
    [NAN, NAN],
    [NAN, NAN],
]


@pytest.fixture
def run_definition(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "run.yaml"
    path.write_text(RUN_DEFINITION)
    return path


def binning_tool(
    run_definition: pathlib.Path, grr_dir: pathlib.Path,
    output: pathlib.Path, *extra: str,
) -> None:
    cli([
        str(run_definition), "-o", str(output),
        "--grr-directory", str(grr_dir), "-R", "genome", "-j", "1",
        *extra,
    ])


@pytest.fixture
def output(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / "bins.h5"


@pytest.fixture
def binned(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path,
    run_definition: pathlib.Path, output: pathlib.Path,
) -> pathlib.Path:
    binning_tool(run_definition, grr_dir, output)
    return output


def test_values_is_a_float64_bins_by_tracks_matrix_with_nan_where_uncovered(
    binned: pathlib.Path,
) -> None:
    with h5py.File(binned, "r") as h5:
        values = h5["values"][()]

    assert values.dtype == np.float64
    np.testing.assert_array_equal(values, EXPECTED_VALUES)


def test_values_is_stored_in_gzip_compressed_row_blocks(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path,
    run_definition: pathlib.Path, output: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A row block smaller than the matrix, so that the chunk shape can be
    # told apart from "the whole dataset": three rows, every track.
    monkeypatch.setattr("gain.binning.cli.ROW_BLOCK", 3)

    binning_tool(run_definition, grr_dir, output)

    with h5py.File(output, "r") as h5:
        values = h5["values"]
        assert values.compression == "gzip"
        assert values.chunks == (3, 2)


def test_bins_lists_chrom_start_end_per_row_one_based_inclusive(
    binned: pathlib.Path,
) -> None:
    # Row i of /bins describes row i of /values: chr1's window first, in
    # ascending order, then the whole of chr2.  chrom is fixed-length
    # bytes; start and end are 1-based inclusive grid bins.
    with h5py.File(binned, "r") as h5:
        bins = h5["bins"][()]

    assert bins.dtype == np.dtype(
        [("chrom", "S4"), ("start", "<i8"), ("end", "<i8")])
    assert [tuple(row) for row in bins] == [
        (b"chr1", 1, 10), (b"chr1", 11, 20),
        (b"chr1", 21, 30), (b"chr1", 31, 40),
        (b"chr2", 1, 10), (b"chr2", 11, 20),
        (b"chr2", 21, 30), (b"chr2", 31, 40),
    ]


def test_tracks_describes_each_column_with_its_provenance(
    binned: pathlib.Path,
) -> None:
    # Row j of /tracks describes column j of /values.  Neither entry set a
    # replacement, so the column reads NaN.
    with h5py.File(binned, "r") as h5:
        tracks = h5["tracks"][()]

    assert list(tracks.dtype.names) == [
        "name", "resource_id", "score_id", "aggregator",
        "none_value_replacement"]
    assert [
        (row["name"], row["resource_id"], row["score_id"], row["aggregator"])
        for row in tracks
    ] == [
        (b"scores/one", b"scores/one", b"s", b"max"),
        (b"scores/two", b"scores/two", b"t", b"mean"),
    ]
    np.testing.assert_array_equal(
        tracks["none_value_replacement"], [np.nan, np.nan])


def test_root_attributes_record_what_the_run_was(
    binned: pathlib.Path,
) -> None:
    with h5py.File(binned, "r") as h5:
        attrs = dict(h5.attrs)

    assert attrs["input_reference_genome"] == "genome"
    assert attrs["bin_size"] == 10
    assert list(attrs["regions"]) == ["chr1:1-40", "chr2:1-40"]
    assert attrs["coordinates"] == "1-based-inclusive"
    assert attrs["gain_version"] == __version__
    assert attrs["created"].endswith("+00:00")


def test_a_successful_run_removes_the_work_dir_it_created(
    binned: pathlib.Path,
) -> None:
    assert not (binned.parent / "bins_work").exists()


def test_dry_run_prints_the_tracks_and_counts_and_writes_nothing(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path,
    run_definition: pathlib.Path, output: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    binning_tool(run_definition, grr_dir, output, "--dry-run")

    out = capsys.readouterr().out
    assert "scores/one\tscores/one\ts\tmax" in out
    assert "scores/two\tscores/two\tt\tmean" in out
    assert "regions: 2" in out
    assert "bins: 8" in out
    assert not output.exists()
    assert not (output.parent / "bins_work").exists()


# scores/one twice, under its own ``max`` and under ``min``; scores/two
# once.  Only the repeated resource carries its aggregator in its name.
REPEATED_RUN_DEFINITION = textwrap.dedent("""
    bins:
      bin_size: 10
      regions: ["chr1:1-40"]
    binners:
    - position_score_binner:
        resource_query: "scores/*"
    - position_score_binner:
        resource_query: "scores/one"
        aggregator: min
""")


def test_a_repeated_resource_is_named_by_aggregator_in_the_tracks_table(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path, output: pathlib.Path,
) -> None:
    run_definition = output.parent / "run.yaml"
    run_definition.write_text(REPEATED_RUN_DEFINITION)

    binning_tool(run_definition, grr_dir, output)

    with h5py.File(output, "r") as h5:
        tracks = h5["tracks"][()]
        shape = h5["values"].shape
    assert [row["name"] for row in tracks] == [
        b"scores/one:max", b"scores/two", b"scores/one:min"]
    assert shape == (4, 3)


def test_dry_run_lists_the_suffixed_track_names(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path, output: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_definition = output.parent / "run.yaml"
    run_definition.write_text(REPEATED_RUN_DEFINITION)

    binning_tool(run_definition, grr_dir, output, "--dry-run")

    out = capsys.readouterr().out
    assert "scores/one:max\tscores/one\ts\tmax" in out
    assert "scores/one:min\tscores/one\ts\tmin" in out
    assert "scores/two\tscores/two\tt\tmean" in out


@pytest.mark.parametrize("run_definition_text,fragments", [
    # search_term needs the index the toy GRR does not publish.
    (textwrap.dedent("""
        bins:
          bin_size: 10
        binners:
        - position_score_binner:
            resource_query: "scores/*"
            search_term: one
    """), ["binners[0]", "search_term", "index"]),
    (textwrap.dedent("""
        bins:
          bin_size: 10
          regions: ["chr1:1-20", "chr1:20-30"]
        binners:
        - position_score_binner:
            resource_query: "scores/*"
    """), ["bins.regions[0]", "bins.regions[1]", "overlap"]),
], ids=["search_term_without_index", "overlapping_regions"])
def test_dry_run_reports_a_run_definition_error_and_writes_nothing(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path, output: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    run_definition_text: str, fragments: list[str],
) -> None:
    run_definition = output.parent / "run.yaml"
    run_definition.write_text(run_definition_text)

    with pytest.raises(SystemExit) as excinfo:
        binning_tool(run_definition, grr_dir, output, "--dry-run")

    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert str(run_definition) in err
    for fragment in fragments:
        assert fragment in err
    assert not output.exists()
    assert not (output.parent / "bins_work").exists()


def test_the_output_defaults_to_the_run_definition_with_an_h5_suffix(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path,
    run_definition: pathlib.Path,
) -> None:
    # run.yaml -> run.h5 beside it, and the work dir beside that is
    # gone after the run as usual.
    cli([
        str(run_definition), "--grr-directory", str(grr_dir),
        "-R", "genome", "-j", "1",
    ])

    np.testing.assert_array_equal(
        read_matrix(run_definition.with_suffix(".h5")), EXPECTED_VALUES)
    assert not (run_definition.parent / "run_work").exists()


def test_the_run_definition_may_name_the_genome_itself(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path, output: pathlib.Path,
) -> None:
    run_definition = output.parent / "run.yaml"
    run_definition.write_text(
        "input_reference_genome: genome\n" + RUN_DEFINITION)

    cli([
        str(run_definition), "-o", str(output),
        "--grr-directory", str(grr_dir), "-j", "1",
    ])

    with h5py.File(output, "r") as h5:
        assert h5.attrs["input_reference_genome"] == "genome"
        assert h5["values"].shape == (8, 2)


def read_matrix(path: pathlib.Path) -> npt.NDArray[np.float64]:
    with h5py.File(path, "r") as h5:
        return np.asarray(h5["values"][()], dtype=np.float64)


def republish_scores_one_as(grr_dir: pathlib.Path, value: float) -> None:
    """Replace ``scores/one`` with one value over chr1:1-40."""
    resource_dir = grr_dir / "scores" / "one"
    shutil.rmtree(resource_dir)
    a_position_score().with_score("s", "float").with_aggregator("max") \
        .with_tabix().with_data(f"""
            chrom  pos_begin  pos_end  s
            chr1   1          40       {value}
        """).realize_into(resource_dir)


def test_a_rerun_with_the_same_work_dir_reuses_the_finished_chunks(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path,
    run_definition: pathlib.Path, output: pathlib.Path,
) -> None:
    # Between the runs the resource changes underneath the tool.  The
    # rerun does not notice: its chunks are done, so only the writer runs
    # and the matrix is the first run's, bit for bit.
    binning_tool(run_definition, grr_dir, output, "--keep-work-dir")
    first = read_matrix(output)
    output.unlink()
    republish_scores_one_as(grr_dir, 9.0)

    binning_tool(run_definition, grr_dir, output, "--keep-work-dir")

    np.testing.assert_array_equal(read_matrix(output), first)


def test_an_interrupted_run_resumes_from_its_finished_chunks(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path,
    run_definition: pathlib.Path, output: pathlib.Path,
) -> None:
    # An interruption leaves some chunks written and the output missing.
    # Staged by removing one of scores/two's chunks after a full run; the
    # republished scores/one proves its chunks were reused, not redone.
    binning_tool(run_definition, grr_dir, output, "--keep-work-dir")
    first = read_matrix(output)
    output.unlink()
    next(output.parent.glob("bins_work/**/scores_two_*.npy")).unlink()
    republish_scores_one_as(grr_dir, 9.0)

    binning_tool(run_definition, grr_dir, output, "--keep-work-dir")

    np.testing.assert_array_equal(read_matrix(output), first)


def test_another_run_definition_sharing_the_work_dir_is_not_served_stale_chunks(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path, output: pathlib.Path,
) -> None:
    # Both definitions are older than the first run's chunks and output,
    # so mtimes alone would say "nothing to do".  The chunks are keyed by
    # what decides their values, so the min run computes its own and the
    # writer sees new inputs.
    by_max = output.parent / "max.yaml"
    by_max.write_text(RUN_DEFINITION)
    by_min = output.parent / "min.yaml"
    by_min.write_text(RUN_DEFINITION.replace(
        'resource_query: "scores/*"',
        'resource_query: "scores/*"\n    aggregator: min'))
    binning_tool(by_max, grr_dir, output, "--keep-work-dir")

    binning_tool(by_min, grr_dir, output, "--keep-work-dir")

    with h5py.File(output, "r") as h5:
        aggregators = [row["aggregator"] for row in h5["tracks"][()]]
    # On this toy data min and max agree bin for bin, so the provenance
    # is what tells a rewritten file from a stale one.
    assert aggregators == [b"min", b"min"]


def test_the_process_pool_executor_yields_the_same_matrix(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path,
    run_definition: pathlib.Path, output: pathlib.Path,
) -> None:
    # Every task argument crosses a process boundary: the binner class,
    # the track, the region, the GRR definition and the run definition.
    # argparse takes the last -j.
    binning_tool(run_definition, grr_dir, output, "-j", "2", "--process-pool")

    np.testing.assert_array_equal(read_matrix(output), EXPECTED_VALUES)


def test_relative_paths_are_taken_from_the_launch_directory(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path,
    run_definition: pathlib.Path, output: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The tool runs its tasks inside the work directory; a GRR named
    # relative to where the user typed the command must still be found.
    monkeypatch.chdir(output.parent)

    cli([
        "run.yaml", "-o", "bins.h5", "--grr-directory", "grr",
        "-R", "genome", "-j", "1",
    ])

    np.testing.assert_array_equal(read_matrix(output), EXPECTED_VALUES)


def test_force_recomputes_every_chunk(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path,
    run_definition: pathlib.Path, output: pathlib.Path,
) -> None:
    binning_tool(run_definition, grr_dir, output, "--keep-work-dir")
    republish_scores_one_as(grr_dir, 9.0)

    binning_tool(run_definition, grr_dir, output, "--keep-work-dir", "--force")

    np.testing.assert_array_equal(
        read_matrix(output)[:, 0], [9.0, 9.0, 9.0, 9.0, NAN, NAN, NAN, NAN])
