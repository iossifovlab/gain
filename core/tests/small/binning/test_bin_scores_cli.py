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


def bin_scores(
    run_definition: pathlib.Path, grr_dir: pathlib.Path,
    output: pathlib.Path, *extra: str,
) -> None:
    cli([
        str(run_definition), "-o", str(output),
        "--grr-directory", str(grr_dir), "-R", "genome", "-j", "1",
        *extra,
    ])


@pytest.fixture
def binned(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path,
    run_definition: pathlib.Path, tmp_path: pathlib.Path,
) -> pathlib.Path:
    output = tmp_path / "bins.h5"
    bin_scores(run_definition, grr_dir, output)
    return output


def test_values_is_a_float64_bins_by_tracks_matrix_with_nan_where_uncovered(
    binned: pathlib.Path,
) -> None:
    with h5py.File(binned, "r") as h5:
        values = h5["values"][()]

    assert values.dtype == np.float64
    np.testing.assert_array_equal(values, EXPECTED_VALUES)


def test_values_is_stored_in_gzip_compressed_row_blocks(
    binned: pathlib.Path,
) -> None:
    with h5py.File(binned, "r") as h5:
        values = h5["values"]
        compression = values.compression
        chunks = values.chunks

    assert compression == "gzip"
    assert chunks == (8, 2)


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
    run_definition: pathlib.Path, tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "bins.h5"

    bin_scores(run_definition, grr_dir, output, "--dry-run")

    out = capsys.readouterr().out
    assert "scores/one\tscores/one\ts\tmax" in out
    assert "scores/two\tscores/two\tt\tmean" in out
    assert "regions: 2" in out
    assert "bins: 8" in out
    assert not output.exists()
    assert not (tmp_path / "bins_work").exists()


def test_the_output_flag_is_required(
    run_definition: pathlib.Path,
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli([str(run_definition)])

    assert excinfo.value.code == 2


def test_the_run_definition_may_name_the_genome_itself(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path, tmp_path: pathlib.Path,
) -> None:
    run_definition = tmp_path / "run.yaml"
    run_definition.write_text(
        "input_reference_genome: genome\n" + RUN_DEFINITION)
    output = tmp_path / "bins.h5"

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
    run_definition: pathlib.Path, tmp_path: pathlib.Path,
) -> None:
    # Between the runs the resource changes underneath the tool.  The
    # rerun does not notice: its chunks are done, so only the writer runs
    # and the matrix is the first run's, bit for bit.
    output = tmp_path / "bins.h5"
    bin_scores(run_definition, grr_dir, output, "--keep-work-dir")
    first = read_matrix(output)
    output.unlink()
    republish_scores_one_as(grr_dir, 9.0)

    bin_scores(run_definition, grr_dir, output, "--keep-work-dir")

    np.testing.assert_array_equal(read_matrix(output), first)


def test_force_recomputes_every_chunk(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path,
    run_definition: pathlib.Path, tmp_path: pathlib.Path,
) -> None:
    output = tmp_path / "bins.h5"
    bin_scores(run_definition, grr_dir, output, "--keep-work-dir")
    republish_scores_one_as(grr_dir, 9.0)

    bin_scores(run_definition, grr_dir, output, "--keep-work-dir", "--force")

    np.testing.assert_array_equal(
        read_matrix(output)[:, 0], [9.0, 9.0, 9.0, 9.0, NAN, NAN, NAN, NAN])
