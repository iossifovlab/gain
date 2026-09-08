# pylint: disable=W0621,C0114,C0116,W0212,W0613
import argparse
import pathlib
import shutil
import textwrap
from typing import Any

import h5py
import numpy as np
import numpy.typing as npt
import pytest
import pytest_mock
from gain import __version__
from gain.binning.cli import cli
from gain.genomic_resources import genomic_context as gc_mod
from gain.genomic_resources.genomic_context_base import (
    GC_REFERENCE_GENOME_KEY,
    GenomicContext,
    GenomicContextProvider,
    SimpleGenomicContext,
)
from gain.genomic_resources.reference_genome import (
    build_reference_genome_from_resource,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing.builders import a_position_score

RUN_DEFINITION = textwrap.dedent("""
    input_reference_genome: genome
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
    """Run the tool on the toy GRR.

    The genome is not a command-line argument: every run definition
    here names it, as the tool requires.
    """
    cli([
        str(run_definition), "-o", str(output),
        "--grr-directory", str(grr_dir), "-j", "1",
        *extra,
    ])


def write_run_definition(output: pathlib.Path, text: str) -> pathlib.Path:
    """A run definition beside ``output``, as a user would keep it."""
    run_definition = output.parent / "run.yaml"
    run_definition.write_text(text)
    return run_definition


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


def test_dry_run_reports_the_task_count_under_the_budget(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path,
    run_definition: pathlib.Path, output: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Both toy regions fit one bundle under the default budget: one task
    # per track.  A budget of 0 is one task per (track, region).
    binning_tool(run_definition, grr_dir, output, "--dry-run")
    assert "tasks: 2" in capsys.readouterr().out

    binning_tool(
        run_definition, grr_dir, output, "--dry-run", "--task-budget", "0")
    assert "tasks: 4" in capsys.readouterr().out


# scores/one twice, under its own ``max`` and under ``min``; scores/two
# once.  Only the repeated resource carries its aggregator in its name.
REPEATED_RUN_DEFINITION = textwrap.dedent("""
    input_reference_genome: genome
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
    run_definition = write_run_definition(output, REPEATED_RUN_DEFINITION)

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
    run_definition = write_run_definition(output, REPEATED_RUN_DEFINITION)

    binning_tool(run_definition, grr_dir, output, "--dry-run")

    out = capsys.readouterr().out
    assert "scores/one:max\tscores/one\ts\tmax" in out
    assert "scores/one:min\tscores/one\ts\tmin" in out
    assert "scores/two\tscores/two\tt\tmean" in out


def test_dry_run_reports_a_run_definition_error_and_writes_nothing(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path, output: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # One parse-time refusal stands for all: which message says what is
    # the parser tests' business.  search_term needs the index the toy
    # GRR does not publish.
    run_definition = write_run_definition(output, textwrap.dedent("""
        input_reference_genome: genome
        bins:
          bin_size: 10
        binners:
        - position_score_binner:
            resource_query: "scores/*"
            search_term: one
    """))

    with pytest.raises(SystemExit) as excinfo:
        binning_tool(run_definition, grr_dir, output, "--dry-run")

    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert str(run_definition) in err
    assert "binners[0]" in err
    assert "search_term" in err
    assert not output.exists()
    assert not (output.parent / "bins_work").exists()


def test_a_stray_second_positional_argument_is_refused(
    repo: GenomicResourceRepo, run_definition: pathlib.Path,
    grr_dir: pathlib.Path, output: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The tool takes exactly one positional.  The annotation context
    # provider adds an optional ``pipeline`` positional to every parser
    # it is asked to fill, and argparse matches the leading positionals
    # as one chunk -- so a second argument in this position was bound to
    # a pipeline the tool never reads, rather than reported.
    with pytest.raises(SystemExit) as excinfo:
        cli([
            str(run_definition), "stray.yaml", "-o", str(output),
            "--grr-directory", str(grr_dir), "-j", "1",
        ])

    assert excinfo.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_the_output_defaults_to_the_run_definition_with_an_h5_suffix(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path,
    run_definition: pathlib.Path,
) -> None:
    # run.yaml -> run.h5 beside it, and the work dir beside that is
    # gone after the run as usual.
    cli([
        str(run_definition), "--grr-directory", str(grr_dir), "-j", "1",
    ])

    np.testing.assert_array_equal(
        read_matrix(run_definition.with_suffix(".h5")), EXPECTED_VALUES)
    assert not (run_definition.parent / "run_work").exists()


def read_genome_and_shape(output: pathlib.Path) -> tuple[str, tuple[int, ...]]:
    with h5py.File(output, "r") as h5:
        return str(h5.attrs["input_reference_genome"]), h5["values"].shape


# The toy GRR's second genome: chr1 only, and shorter, so a run that
# resolved it shows in what it accepts and produces.  It is also what
# the test-only context provider below offers.
CONTEXT_GENOME = "genomes/short"


def test_the_genome_named_in_the_run_definition_defines_the_grid(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path, output: pathlib.Path,
) -> None:
    # genomes/short is chr1 only and shorter, so which genome the run
    # resolved is visible in the shape as well as in the recorded id.
    run_definition = write_run_definition(output, textwrap.dedent(f"""
        input_reference_genome: {CONTEXT_GENOME}
        bins:
          bin_size: 10
        binners:
        - position_score_binner:
            resource_query: "scores/*"
    """))

    binning_tool(run_definition, grr_dir, output)

    assert read_genome_and_shape(output) == (CONTEXT_GENOME, (5, 2))


def test_a_run_definition_without_a_genome_is_a_parse_error(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path, output: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_definition = write_run_definition(output, textwrap.dedent("""
        bins:
          bin_size: 10
          regions: ["chr1:1-40"]
        binners:
        - position_score_binner:
            resource_query: "scores/*"
    """))

    with pytest.raises(SystemExit) as excinfo:
        binning_tool(run_definition, grr_dir, output)

    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert str(run_definition) in err
    assert "input_reference_genome is required" in err
    assert not output.exists()


def test_the_genome_is_not_a_command_line_argument(
    run_definition: pathlib.Path, grr_dir: pathlib.Path,
    output: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The grid follows the genome's chromosome lengths, so one run
    # definition must describe one matrix: neither -R nor -G (which this
    # tool never reads at all) is offered.
    for flag, value in (("-R", "genome"), ("-G", "gene_models")):
        with pytest.raises(SystemExit) as excinfo:
            binning_tool(run_definition, grr_dir, output, flag, value)

        assert excinfo.value.code == 2
        assert "unrecognized arguments" in capsys.readouterr().err


class _FallbackGenomeProvider(GenomicContextProvider):
    """A context carrying only a genome, the way a site config might.

    Registered above the CLI provider's priority so the CLI's context is
    consulted first and this one answers only what the CLI left unset.
    """

    def __init__(self, repo: GenomicResourceRepo) -> None:
        super().__init__("FallbackGenomeProvider", 2000)
        self._repo = repo

    def add_argparser_arguments(
        self, parser: argparse.ArgumentParser, **kwargs: Any,
    ) -> None:
        pass

    def init(self, **kwargs: Any) -> GenomicContext | None:
        return SimpleGenomicContext(
            {GC_REFERENCE_GENOME_KEY: build_reference_genome_from_resource(
                self._repo.get_resource(CONTEXT_GENOME))},
            source="FallbackGenomeProvider")


@pytest.fixture
def context_genome(
    repo: GenomicResourceRepo, mocker: pytest_mock.MockerFixture,
) -> None:
    providers = [
        *gc_mod._REGISTERED_CONTEXT_PROVIDERS,
        _FallbackGenomeProvider(repo),
    ]
    mocker.patch.object(gc_mod, "_REGISTERED_CONTEXT_PROVIDERS", providers)


def test_the_run_definition_genome_wins_over_the_context_genome(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path,
    run_definition: pathlib.Path, output: pathlib.Path,
    context_genome: None,
) -> None:
    # A site config offering genomes/short does not change what this run
    # definition bins: it names its own genome, and chr2 -- which
    # genomes/short does not have -- is binned as usual.
    binning_tool(run_definition, grr_dir, output)

    assert read_genome_and_shape(output) == ("genome", (8, 2))


def test_the_context_genome_is_not_a_fallback(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path, output: pathlib.Path,
    context_genome: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A genome the run definition did not name cannot decide the grid,
    # however the ambient context came by it.
    run_definition = write_run_definition(output, textwrap.dedent("""
        bins:
          bin_size: 10
          regions: ["chr1:1-40"]
        binners:
        - position_score_binner:
            resource_query: "scores/*"
    """))

    with pytest.raises(SystemExit) as excinfo:
        binning_tool(run_definition, grr_dir, output)

    assert excinfo.value.code == 1
    assert "input_reference_genome is required" in capsys.readouterr().err
    assert not output.exists()


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


def republish_scores_two_as(grr_dir: pathlib.Path, value: float) -> None:
    """Replace ``scores/two`` with one value over chr1:1-40."""
    resource_dir = grr_dir / "scores" / "two"
    shutil.rmtree(resource_dir)
    a_position_score().with_score("t", "float").with_aggregator("mean") \
        .with_tabix().with_data(f"""
            chrom  pos_begin  pos_end  t
            chr1   1          40       {value}
        """).realize_into(resource_dir)


def test_a_missing_chunk_recomputes_its_whole_bundle(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path,
    run_definition: pathlib.Path, output: pathlib.Path,
) -> None:
    # Both regions of a track are one task under the default budget, so
    # losing scores/two's chr2 chunk recomputes its chr1 chunk too -- the
    # rerun sees the republished value on chr1, where the chunk it lost
    # was chr2's.  scores/one's bundle is untouched and is reused.
    binning_tool(run_definition, grr_dir, output, "--keep-work-dir")
    output.unlink()
    next(output.parent.glob("bins_work/**/scores_two_*_chr2_*.npy")).unlink()
    republish_scores_two_as(grr_dir, 7.0)
    republish_scores_one_as(grr_dir, 9.0)

    binning_tool(run_definition, grr_dir, output, "--keep-work-dir")

    np.testing.assert_array_equal(read_matrix(output), [
        [1.0, 7.0],
        [1.0, 7.0],
        [NAN, 7.0],
        [2.0, 7.0],
        [NAN, NAN],
        [NAN, NAN],
        [NAN, NAN],
        [NAN, NAN],
    ])


def read_everything_but_created(path: pathlib.Path) -> dict[str, Any]:
    with h5py.File(path, "r") as h5:
        attrs = {k: v for k, v in h5.attrs.items() if k != "created"}
        return {
            "values": h5["values"][()], "bins": h5["bins"][()],
            "tracks": h5["tracks"][()], "attrs": attrs,
        }


def test_the_budget_changes_the_tasks_and_nothing_in_the_file(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path,
    run_definition: pathlib.Path, output: pathlib.Path,
) -> None:
    # The budget is how the work is cut, not what is computed: the file
    # written with every region its own task is the file written with
    # both regions in one, dataset for dataset and attribute for
    # attribute, and the chunks in the work directory are the same files
    # under the same names.
    binning_tool(run_definition, grr_dir, output, "--keep-work-dir")
    bundled = read_everything_but_created(output)
    bundled_chunks = sorted(
        p.name for p in output.parent.glob("bins_work/chunks/*.npy"))
    shutil.rmtree(output.parent / "bins_work")
    output.unlink()

    binning_tool(
        run_definition, grr_dir, output, "--keep-work-dir",
        "--task-budget", "0")

    unbundled = read_everything_but_created(output)
    np.testing.assert_array_equal(unbundled["values"], bundled["values"])
    np.testing.assert_array_equal(unbundled["bins"], bundled["bins"])
    # Field by field: a NaN replacement is unequal to itself as a row.
    assert unbundled["tracks"].dtype == bundled["tracks"].dtype
    for field in bundled["tracks"].dtype.names:
        np.testing.assert_array_equal(
            unbundled["tracks"][field], bundled["tracks"][field])
    assert list(unbundled["attrs"]) == list(bundled["attrs"])
    for key, value in bundled["attrs"].items():
        np.testing.assert_array_equal(unbundled["attrs"][key], value)
    assert bundled_chunks == sorted(
        p.name for p in output.parent.glob("bins_work/chunks/*.npy"))
    # The names are the tracer bullet's, quoted 'none' included.
    assert bundled_chunks == [
        "scores_one_s_max_'none'_bs10_chr1_1_40.npy",
        "scores_one_s_max_'none'_bs10_chr2_1_40.npy",
        "scores_two_t_mean_'none'_bs10_chr1_1_40.npy",
        "scores_two_t_mean_'none'_bs10_chr2_1_40.npy",
    ]


def test_a_bundle_of_many_regions_is_one_task_with_a_short_id(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path, output: pathlib.Path,
) -> None:
    # Twenty windows of chr1 and chr2 fit one bundle: one task per track
    # plus the writer, each leaving one status file named by its id --
    # an id that spans the bundle rather than listing its regions, so it
    # stays a file name however many regions there are.
    windows = ", ".join(f'"chr1:{s}-{s + 4}"' for s in range(1, 100, 5))
    run_definition = write_run_definition(output, textwrap.dedent(f"""
        input_reference_genome: genome
        bins:
          bin_size: 10
          regions: [{windows}, chr2]
        binners:
        - position_score_binner:
            resource_query: "scores/*"
    """))

    binning_tool(run_definition, grr_dir, output, "--keep-work-dir")

    flags = sorted(
        p.name for p in (output.parent / "bins_work" / ".task-status")
        .glob("*.flag"))
    assert flags == [
        "bin_scores_one_s_max_'none'_bs10_chr1_1_chr2_40_n21.flag",
        "bin_scores_two_t_mean_'none'_bs10_chr1_1_chr2_40_n21.flag",
        "write_hdf5.flag",
    ]
    assert read_matrix(output).shape == (24, 2)


def test_a_budget_of_zero_runs_one_task_per_track_and_region(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path,
    run_definition: pathlib.Path, output: pathlib.Path,
) -> None:
    # The budget the command line names is the one the graph is cut by:
    # under 0 each of the two regions is its own task per track, four
    # single-region bundles where the default makes two.
    binning_tool(
        run_definition, grr_dir, output, "--keep-work-dir",
        "--task-budget", "0")

    flags = sorted(
        p.name for p in (output.parent / "bins_work" / ".task-status")
        .glob("bin_*.flag"))
    assert flags == [
        "bin_scores_one_s_max_'none'_bs10_chr1_1_chr1_40_n1.flag",
        "bin_scores_one_s_max_'none'_bs10_chr2_1_chr2_40_n1.flag",
        "bin_scores_two_t_mean_'none'_bs10_chr1_1_chr1_40_n1.flag",
        "bin_scores_two_t_mean_'none'_bs10_chr2_1_chr2_40_n1.flag",
    ]


def test_a_negative_budget_is_refused(
    repo: GenomicResourceRepo, run_definition: pathlib.Path,
    grr_dir: pathlib.Path, output: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # 0 is the one special value; below it there is nothing to mean.
    with pytest.raises(SystemExit) as excinfo:
        binning_tool(run_definition, grr_dir, output, "--task-budget", "-1")

    assert excinfo.value.code == 2
    assert "--task-budget" in capsys.readouterr().err


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
        "run.yaml", "-o", "bins.h5", "--grr-directory", "grr", "-j", "1",
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
