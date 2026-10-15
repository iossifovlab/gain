# pylint: disable=W0621,C0114,C0116,W0613
import io
import os
import pathlib
import shutil
import subprocess
import sys
from typing import Any

import h5py
import numpy as np
import pytest
from gain.binning import view_cli
from gain.binning.h5_reader import read_tracks
from gain.binning.view_cli import cli
from gain.genomic_resources.repository import GenomicResourceRepo

from tests.small.binning.test_binning_tool_cli import (
    MIXED_EXPECTED_VALUES,
    MIXED_RUN_DEFINITION,
    binning_tool,
    write_run_definition,
)


@pytest.fixture
def binned(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path,
    tmp_path: pathlib.Path,
) -> pathlib.Path:
    """A file from a real run: two fragment entries, one position-score
    entry, bins on chr1 (1-40) and chr2, 10 bases each."""
    output = tmp_path / "bins.h5"
    definition = write_run_definition(output, MIXED_RUN_DEFINITION)
    binning_tool(definition, grr_dir, output)
    return output


def view(
    capsys: pytest.CaptureFixture[str], *argv: object,
) -> tuple[int, str, str]:
    code = cli([str(arg) for arg in argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_tracks_prints_one_row_per_track(
    binned: pathlib.Path, capsys: pytest.CaptureFixture[str],
) -> None:
    defs = read_tracks(binned)["binner_def"].tolist()

    code, out, err = view(capsys, binned, "--tracks")

    assert (code, err) == (0, "")
    assert out == (
        "label\tbinner_type\tbinner_index\ttrack_index\tresource_ids"
        "\tselector\tbinner_def\n"
        "frags/*:B:sum\tfragment_score\t0\t0\tfrags/s1,frags/s2\tB"
        f"\t{defs[0]}\n"
        "frags/*:T:sum\tfragment_score\t0\t1\tfrags/s1,frags/s2\tT"
        f"\t{defs[1]}\n"
        f"frags/s1:all:sum\tfragment_score\t1\t0\tfrags/s1\tall\t{defs[2]}\n"
        f"scores/one:max\tposition_score\t2\t0\tscores/one\ts\t{defs[3]}\n")


LABELS = [
    "frags/*:B:sum", "frags/*:T:sum", "frags/s1:all:sum", "scores/one:max"]
BIN_ROWS = [
    "chr1\t1\t10", "chr1\t11\t20", "chr1\t21\t30", "chr1\t31\t40",
    "chr2\t1\t10", "chr2\t11\t20", "chr2\t21\t30", "chr2\t31\t40"]
VALUE_ROWS = [
    "\t".join(repr(value) for value in row)
    for row in MIXED_EXPECTED_VALUES]


def lines(*rows: str) -> str:
    return "".join(f"{row}\n" for row in rows)


def test_bins_prints_the_coordinates(
    binned: pathlib.Path, capsys: pytest.CaptureFixture[str],
) -> None:
    code, out, err = view(capsys, binned, "--bins")

    assert (code, err) == (0, "")
    assert out == lines("chrom\tstart\tend", *BIN_ROWS)


def test_matrix_alone_prints_the_values_headed_by_the_track_labels(
    binned: pathlib.Path, capsys: pytest.CaptureFixture[str],
) -> None:
    code, out, err = view(capsys, binned, "--matrix")

    assert (code, err) == (0, "")
    assert out == lines("\t".join(LABELS), *VALUE_ROWS)
    assert "nan" in out


def test_bins_with_matrix_is_one_joined_table(
    binned: pathlib.Path, capsys: pytest.CaptureFixture[str],
) -> None:
    code, out, err = view(capsys, binned, "--bins", "--matrix")

    assert (code, err) == (0, "")
    assert out == lines(
        "chrom\tstart\tend\t" + "\t".join(LABELS),
        *(f"{b}\t{v}" for b, v in zip(BIN_ROWS, VALUE_ROWS, strict=True)))


def test_no_header_drops_the_header_line(
    binned: pathlib.Path, capsys: pytest.CaptureFixture[str],
) -> None:
    _, bins_out, _ = view(capsys, binned, "--bins", "--no-header")
    _, matrix_out, _ = view(capsys, binned, "--matrix", "--no-header")
    _, tracks_out, _ = view(capsys, binned, "--tracks", "--no-header")

    assert bins_out == lines(*BIN_ROWS)
    assert matrix_out == lines(*VALUE_ROWS)
    assert tracks_out.count("\n") == 4
    assert not tracks_out.startswith("label")


def test_header_index_heads_the_matrix_by_track_position(
    binned: pathlib.Path, capsys: pytest.CaptureFixture[str],
) -> None:
    _, out, _ = view(
        capsys, binned, "--bins", "--matrix", "--header", "index")

    assert out.splitlines()[0] == "chrom\tstart\tend\t0\t1\t2\t3"
    assert out.splitlines()[1] == f"{BIN_ROWS[0]}\t{VALUE_ROWS[0]}"


@pytest.mark.parametrize(("argv", "message"), [
    ([], "choose at least one of --tracks, --bins, --matrix"),
    (["--tracks", "--bins"],
     "--tracks cannot go with --bins or --matrix: the axes differ"),
    (["--tracks", "--matrix"],
     "--tracks cannot go with --bins or --matrix: the axes differ"),
    (["--bins", "--track", "scores/one:max"],
     "--track needs --matrix or --tracks"),
    (["--tracks", "--region", "chr1"],
     "--region needs --bins or --matrix"),
])
def test_a_bad_combination_is_one_line_on_stderr(
    binned: pathlib.Path, capsys: pytest.CaptureFixture[str],
    argv: list[str], message: str,
) -> None:
    code, out, err = view(capsys, binned, *argv)

    assert code == 2
    assert out == ""
    assert err == f"binning_tool_view: error: {message}\n"


TEXT = h5py.string_dtype(encoding="utf-8")
TRACK_DTYPE = np.dtype([
    ("binner_type", TEXT), ("binner_index", "<i8"), ("track_index", "<i8"),
    ("resource_ids", TEXT), ("selector", TEXT), ("binner_def", TEXT)])


def write_file(
    path: pathlib.Path, resource_ids: list[str], values: list[list[float]],
    selectors: list[str] | None = None,
) -> pathlib.Path:
    """A hand-made layout 2 file: position-score tracks, chr1 bins of 10."""
    selectors = selectors or [""] * len(resource_ids)
    tracks = np.array([
        ("position_score", i, 0, rid, selectors[i], "{}")
        for i, rid in enumerate(resource_ids)], dtype=TRACK_DTYPE)
    bins = np.array(
        [(b"chr1", 10 * i + 1, 10 * i + 10) for i in range(len(values))],
        dtype=[("chrom", "S4"), ("start", "<i8"), ("end", "<i8")])
    with h5py.File(path, "w") as h5:
        h5.attrs["tracks_layout"] = 2
        h5.create_dataset("tracks", data=tracks)
        h5.create_dataset("bins", data=bins)
        h5.create_dataset("values", data=np.array(values, dtype="<f8"))
    return path


def test_values_print_as_python_float_text(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str],
) -> None:
    path = write_file(tmp_path / "v.h5", ["a", "b"], [
        [0.1, 1.0], [1e-300, float("nan")], [float("inf"), -float("inf")],
        [-0.0, 1.5e300]])

    code, out, err = view(capsys, path, "--matrix")

    assert (code, err) == (0, "")
    assert out == lines(
        "a\tb", "0.1\t1.0", "1e-300\tnan", "inf\t-inf", "-0.0\t1.5e+300")
    assert "np.float64" not in out


def test_tabs_and_newlines_in_a_string_become_a_space(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str],
) -> None:
    path = write_file(
        tmp_path / "v.h5", ["a\tb\nc"], [[1.0]], selectors=["x\ty"])

    _, matrix_out, _ = view(capsys, path, "--matrix")
    _, tracks_out, _ = view(capsys, path, "--tracks", "--no-header")

    assert matrix_out == lines("a b c", "1.0")
    assert tracks_out == lines("a b c\tposition_score\t0\t0\ta b c\tx y\t{}")


def region_rows(*indexes: int) -> list[str]:
    return [BIN_ROWS[i] for i in indexes]


@pytest.mark.parametrize(("regions", "selected"), [
    (["chr2"], [4, 5, 6, 7]),
    (["chr1:21"], [2, 3]),
    (["chr1:11-20"], [1]),
    # Overlap is unclipped: a region inside a bin selects the whole bin.
    (["chr1:12-13"], [1]),
    (["chr1:10-11"], [0, 1]),
    # Bins next to the region, sharing no base, are not selected.
    (["chr1:21-30"], [2]),
    (["chr1:1_0-1,1"], [0, 1]),
    (["chr2:31-100"], [7]),
    # The union: each bin once, in file order.
    (["chr2:1-10", "chr1:1-10"], [0, 4]),
    (["chr1:1-20", "chr1:11-30"], [0, 1, 2]),
])
def test_region_selects_the_bins_that_overlap_it(
    binned: pathlib.Path, capsys: pytest.CaptureFixture[str],
    regions: list[str], selected: list[int],
) -> None:
    argv = [a for region in regions for a in ("--region", region)]

    code, out, err = view(capsys, binned, "--bins", *argv)

    assert (code, err) == (0, "")
    assert out == lines("chrom\tstart\tend", *region_rows(*selected))


def test_region_selects_the_matrix_rows_of_the_bins(
    binned: pathlib.Path, capsys: pytest.CaptureFixture[str],
) -> None:
    _, out, _ = view(
        capsys, binned, "--bins", "--matrix", "--no-header",
        "--region", "chr2:1-10", "--region", "chr1:31-40")

    assert out == lines(
        f"{BIN_ROWS[3]}\t{VALUE_ROWS[3]}", f"{BIN_ROWS[4]}\t{VALUE_ROWS[4]}")


@pytest.mark.parametrize(("region", "message"), [
    ("chrX", "--region 'chrX': chromosome 'chrX' is not in /bins"),
    ("chrX:1-5", "--region 'chrX:1-5': chromosome 'chrX' is not in /bins"),
    ("chr1:20-10", "--region 'chr1:20-10': the start 20 is above the end 10"),
    ("chr1:0-10", "--region 'chr1:0-10': the start must be 1 or more"),
    ("chr1:a-b", "--region 'chr1:a-b': cannot read the coordinates 'a-b'"),
    ("chr1:", "--region 'chr1:': cannot read the coordinates ''"),
])
def test_a_bad_region_is_an_error_naming_it(
    binned: pathlib.Path, capsys: pytest.CaptureFixture[str],
    region: str, message: str,
) -> None:
    code, out, err = view(capsys, binned, "--bins", "--region", region)

    assert code == 2
    assert out == ""
    assert err == f"binning_tool_view: error: {message}\n"


def test_track_selects_the_exact_label_in_the_matrix(
    binned: pathlib.Path, capsys: pytest.CaptureFixture[str],
) -> None:
    code, out, err = view(
        capsys, binned, "--matrix", "--track", "scores/one:max")

    assert (code, err) == (0, "")
    assert out == lines(
        "scores/one:max", *(row.split("\t")[3] for row in VALUE_ROWS))


def test_a_repeated_track_comes_out_in_file_order_not_option_order(
    binned: pathlib.Path, capsys: pytest.CaptureFixture[str],
) -> None:
    _, out, _ = view(
        capsys, binned, "--matrix", "--header", "index",
        "--track", "scores/one:max", "--track", "frags/*:T:sum",
        "--track", "scores/one:max")

    # The position stays the file position: the filter does not renumber.
    assert out.splitlines()[0] == "1\t3"
    assert out.splitlines()[1] == "\t".join(
        VALUE_ROWS[0].split("\t")[i] for i in (1, 3))


def test_track_filters_the_tracks_table_too(
    binned: pathlib.Path, capsys: pytest.CaptureFixture[str],
) -> None:
    _, out, _ = view(
        capsys, binned, "--tracks", "--no-header",
        "--track", "scores/one:max", "--track", "frags/*:B:sum")

    assert [line.split("\t")[0] for line in out.splitlines()] == [
        "frags/*:B:sum", "scores/one:max"]


def test_a_track_label_that_matches_nothing_is_an_error_naming_it(
    binned: pathlib.Path, capsys: pytest.CaptureFixture[str],
) -> None:
    code, out, err = view(capsys, binned, "--matrix", "--track", "scores/one")

    assert code == 2
    assert out == ""
    assert err == (
        "binning_tool_view: error: --track 'scores/one': "
        "no track has this label\n")


def test_a_track_label_that_matches_two_tracks_is_an_error_naming_it(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str],
) -> None:
    # "x" twice becomes x#0 and x#1, and a third track is named "x#1".
    path = write_file(tmp_path / "d.h5", ["x", "x", "x#1"], [[1.0, 2.0, 3.0]])

    code, out, err = view(capsys, path, "--matrix", "--track", "x#1")

    assert code == 2
    assert out == ""
    assert err == (
        "binning_tool_view: error: --track 'x#1': 2 tracks have this label\n")


def test_a_file_of_an_older_layout_is_an_error_message(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str],
) -> None:
    path = write_file(tmp_path / "old.h5", ["a"], [[1.0]])
    with h5py.File(path, "a") as h5:
        del h5.attrs["tracks_layout"]

    code, out, err = view(capsys, path, "--matrix")

    assert code == 2
    assert out == ""
    assert err.startswith("binning_tool_view: error: ")
    assert "only tracks_layout 2 is read" in err
    assert err.count("\n") == 1
    assert "Traceback" not in err


def test_a_missing_file_is_an_error_message(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str],
) -> None:
    code, out, err = view(capsys, tmp_path / "none.h5", "--tracks")

    assert code == 2
    assert out == ""
    assert err.startswith("binning_tool_view: error: ")
    assert err.count("\n") == 1


def test_the_matrix_is_read_in_row_blocks_never_in_one_piece(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [[float(i), float(-i)] for i in range(10)]
    path = write_file(tmp_path / "s.h5", ["a", "b"], rows)
    monkeypatch.setattr(view_cli, "ROW_BLOCK", 3)
    reads: list[tuple[str, Any]] = []
    original = h5py.Dataset.__getitem__

    def spy(self: h5py.Dataset, key: Any, *args: Any, **kw: Any) -> Any:
        reads.append((self.name, key))
        return original(self, key, *args, **kw)

    monkeypatch.setattr(h5py.Dataset, "__getitem__", spy)

    _, out, _ = view(capsys, path, "--matrix", "--no-header")

    assert out == lines(*(f"{float(i)!r}\t{float(-i)!r}" for i in range(10)))
    spans = [
        key[0] for name, key in reads
        if name == "/values" and isinstance(key, tuple)]
    assert spans == [
        slice(0, 3), slice(3, 6), slice(6, 9), slice(9, 10)]
    assert [name for name, _ in reads].count("/values") == 4


def test_track_reads_only_the_selected_columns_of_each_block(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [[float(i), float(-i), float(10 * i)] for i in range(4)]
    path = write_file(tmp_path / "c.h5", ["a", "b", "c"], rows)
    reads: list[Any] = []
    original = h5py.Dataset.__getitem__

    def spy(self: h5py.Dataset, key: Any, *args: Any, **kw: Any) -> Any:
        if self.name == "/values":
            reads.append(key)
        return original(self, key, *args, **kw)

    monkeypatch.setattr(h5py.Dataset, "__getitem__", spy)

    _, out, _ = view(
        capsys, path, "--matrix", "--no-header", "--track", "b")

    assert out == lines(*(f"{float(-i)!r}" for i in range(4)))
    assert reads
    assert all(
        list(np.atleast_1d(key[1])) == [1] for key in reads)


def test_a_closed_pipe_leaks_no_descriptor_in_process(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(args: Any, out: Any) -> None:
        raise BrokenPipeError

    monkeypatch.setattr(view_cli, "_run", broken)
    monkeypatch.setattr(view_cli, "_check_options", lambda _: None)
    with (tmp_path / "out.txt").open("w") as stand_in:
        monkeypatch.setattr(sys, "stdout", stand_in)
        before = len(os.listdir("/proc/self/fd"))
        code = cli(["x.h5"])
        after = len(os.listdir("/proc/self/fd"))

    assert code == 0
    assert after == before


def test_a_closed_pipe_with_a_replaced_stdout_still_returns_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(args: Any, out: Any) -> None:
        raise BrokenPipeError

    monkeypatch.setattr(view_cli, "_run", broken)
    monkeypatch.setattr(view_cli, "_check_options", lambda _: None)
    monkeypatch.setattr(sys, "stdout", io.StringIO())

    assert cli(["x.h5"]) == 0


def test_a_closed_pipe_ends_the_tool_quietly(
    tmp_path: pathlib.Path,
) -> None:
    # The output is small, so it stays in the stdout buffer until the
    # flush.  The reader has left by then: the flush fails, and the
    # data is still buffered when Python flushes again at exit.
    path = write_file(tmp_path / "p.h5", ["a"], [[1.0], [2.0]])
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    program = (
        "import sys, time\n"
        "from gain.binning.view_cli import cli\n"
        "time.sleep(1)\n"
        "sys.exit(cli(sys.argv[1:]))\n")
    command = (
        f"'{sys.executable}' -c '{program}' '{path}' --matrix "
        "| head -n 0")

    script = f"{command}; echo ${{PIPESTATUS[0]}}"
    done = subprocess.run(
        [shutil.which("bash") or "bash", "-c", script],
        capture_output=True, text=True, env=env, check=False, timeout=120)

    assert done.stdout.splitlines() == ["0"]
    assert done.stderr == ""


def test_track_columns_follow_the_file_even_when_labels_sort_differently(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str],
) -> None:
    path = write_file(tmp_path / "o.h5", ["b", "c", "a"], [[1.0, 2.0, 3.0]])

    _, out, _ = view(
        capsys, path, "--matrix", "--track", "a", "--track", "b")

    assert out == lines("b\ta", "1.0\t3.0")


def _drop_values(h5: h5py.File) -> None:
    del h5["values"]


def _values_as_group(h5: h5py.File) -> None:
    del h5["values"]
    h5.create_group("values")


def _values_one_dimensional(h5: h5py.File) -> None:
    del h5["values"]
    h5.create_dataset("values", data=np.zeros(3))


def _values_too_few_columns(h5: h5py.File) -> None:
    del h5["values"]
    h5.create_dataset("values", data=np.zeros((3, 1)))


def _values_too_few_rows(h5: h5py.File) -> None:
    del h5["values"]
    h5.create_dataset("values", data=np.zeros((2, 2)))


@pytest.mark.parametrize(("damage", "message"), [
    (_drop_values, "/values is missing"),
    (_values_as_group, "/values is not a dataset"),
    (_values_one_dimensional, "/values is not a 2-D matrix"),
    (_values_too_few_columns,
     "/values has 1 columns but /tracks has 2 tracks"),
    (_values_too_few_rows, "/values has 2 rows but /bins has 3 bins"),
])
def test_a_damaged_values_table_is_an_error_before_any_output(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str],
    damage: Any, message: str,
) -> None:
    path = write_file(
        tmp_path / "d.h5", ["a", "b"], [[1.0, 2.0]] * 3)
    with h5py.File(path, "a") as h5:
        damage(h5)

    code, out, err = view(capsys, path, "--bins", "--matrix")

    assert code == 2
    assert out == ""
    assert err == f"binning_tool_view: error: {message}\n"


def test_bins_alone_does_not_need_the_values_table(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str],
) -> None:
    path = write_file(tmp_path / "d.h5", ["a"], [[1.0]])
    with h5py.File(path, "a") as h5:
        del h5["values"]

    code, out, err = view(capsys, path, "--bins", "--no-header")

    assert (code, err) == (0, "")
    assert out == lines("chr1\t1\t10")


def test_matrix_with_a_region_and_no_bins_prints_the_selected_rows(
    binned: pathlib.Path, capsys: pytest.CaptureFixture[str],
) -> None:
    _, out, _ = view(
        capsys, binned, "--matrix", "--no-header", "--region", "chr2:11-20")

    assert out == lines(VALUE_ROWS[5])


def test_bins_matrix_and_track_print_the_joined_selected_column(
    binned: pathlib.Path, capsys: pytest.CaptureFixture[str],
) -> None:
    _, out, _ = view(
        capsys, binned, "--bins", "--matrix", "--track", "scores/one:max",
        "--region", "chr1:1-10")

    value = VALUE_ROWS[0].split("\t")[3]
    assert out == lines(
        "chrom\tstart\tend\tscores/one:max", f"chr1\t1\t10\t{value}")


def test_a_region_with_a_break_keeps_each_matrix_row_with_its_bin(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [[float(i)] for i in range(6)]
    path = write_file(tmp_path / "r.h5", ["a"], rows)
    monkeypatch.setattr(view_cli, "ROW_BLOCK", 2)

    _, out, _ = view(
        capsys, path, "--bins", "--matrix", "--no-header",
        "--region", "chr1:1-20", "--region", "chr1:41-60")

    assert out == lines(
        "chr1\t1\t10\t0.0", "chr1\t11\t20\t1.0",
        "chr1\t41\t50\t4.0", "chr1\t51\t60\t5.0")


def test_an_empty_selection_prints_the_header_only(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str],
) -> None:
    path = write_file(tmp_path / "e.h5", ["a"], [[1.0]])
    with h5py.File(path, "a") as h5:
        del h5["bins"]
        h5.create_dataset("bins", data=np.array(
            [(b"chr1", 1, 10), (b"chr2", 1, 10)],
            dtype=[("chrom", "S4"), ("start", "<i8"), ("end", "<i8")]))
        del h5["values"]
        h5.create_dataset("values", data=np.zeros((2, 1)))

    code, out, err = view(
        capsys, path, "--bins", "--matrix", "--region", "chr1:11-20")

    assert (code, err) == (0, "")
    assert out == lines("chrom\tstart\tend\ta")


def test_a_chromosome_name_with_a_colon_is_read_whole(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str],
) -> None:
    path = write_file(tmp_path / "c.h5", ["a"], [[1.0], [2.0]])
    with h5py.File(path, "a") as h5:
        del h5["bins"]
        h5.create_dataset("bins", data=np.array(
            [(b"HLA:1", 1, 10), (b"HLA:1", 11, 20)],
            dtype=[("chrom", "S8"), ("start", "<i8"), ("end", "<i8")]))

    _, whole, _ = view(
        capsys, path, "--bins", "--no-header", "--region", "HLA:1")
    _, part, _ = view(
        capsys, path, "--bins", "--no-header", "--region", "HLA:1:11-20")

    assert whole == lines("HLA:1\t1\t10", "HLA:1\t11\t20")
    assert part == lines("HLA:1\t11\t20")


def test_a_large_matrix_piped_to_head_ends_the_tool_quietly(
    tmp_path: pathlib.Path,
) -> None:
    # The output is far over the pipe buffer, so the write fails
    # in the middle of the stream, not at the final flush.
    rows = [[float(i)] for i in range(30000)]
    path = write_file(tmp_path / "big.h5", ["a"], rows)
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    program = (
        "import sys\n"
        "from gain.binning.view_cli import cli\n"
        "sys.exit(cli(sys.argv[1:]))\n")
    command = (
        f"'{sys.executable}' -c '{program}' '{path}' --bins --matrix "
        "| head -n 1")

    script = f"{command}; echo ${{PIPESTATUS[0]}}"
    done = subprocess.run(
        [shutil.which("bash") or "bash", "-c", script],
        capture_output=True, text=True, env=env, check=False, timeout=120)

    assert done.stdout.splitlines() == ["chrom\tstart\tend\ta", "0"]
    assert done.stderr == ""


def test_an_argument_syntax_error_prints_the_argparse_usage(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str],
) -> None:
    path = write_file(tmp_path / "run.h5", ["a"], [[1.0]])

    with pytest.raises(SystemExit) as raised:
        cli([str(path), "--no-such-option"])

    captured = capsys.readouterr()
    assert raised.value.code == 2
    assert captured.out == ""
    assert captured.err.startswith("usage: ")
    assert captured.err.count("\n") > 1
