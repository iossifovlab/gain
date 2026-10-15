# Fixture-heavy test module: pytest fixtures are injected by name (W0621,
# W0613) and tests are named by behavior (C0114, C0116).
# pylint: disable=W0621,C0114,C0116,W0613
import json
import pathlib
from typing import Any

import h5py
import numpy as np
import pandas as pd
import pytest
from gain.binning.h5_reader import (
    read_bins,
    read_tracks,
    read_values,
    track_labels,
)
from gain.genomic_resources.repository import GenomicResourceRepo

from tests.small.binning.test_binning_tool_cli import (
    MIXED_RUN_DEFINITION,
    binning_tool,
    write_run_definition,
)


@pytest.fixture
def output(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / "bins.h5"


@pytest.fixture
def binned(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path,
    output: pathlib.Path,
) -> pathlib.Path:
    """A file from a real run: a pooled and an unpooled fragment entry
    and one position-score entry."""
    definition = write_run_definition(output, MIXED_RUN_DEFINITION)
    binning_tool(definition, grr_dir, output)
    return output


def test_read_tracks_returns_one_row_per_track_from_a_path(
    binned: pathlib.Path,
) -> None:
    tracks = read_tracks(binned)

    assert list(tracks.columns) == [
        "binner_type", "binner_index", "track_index", "resource_ids",
        "selector", "binner_def"]
    assert tracks["binner_type"].tolist() == [
        "fragment_score", "fragment_score", "fragment_score",
        "position_score"]
    assert tracks["selector"].tolist() == ["B", "T", "all", "s"]
    assert tracks["resource_ids"].tolist() == [
        "frags/s1,frags/s2", "frags/s1,frags/s2", "frags/s1",
        "scores/one"]
    assert tracks["binner_index"].tolist() == [0, 0, 1, 2]
    assert tracks["track_index"].tolist() == [0, 1, 0, 0]
    assert tracks["binner_def"].iloc[3] == (
        '{"aggregator":"max","name":null,"none_value_replacement":null}')


def test_read_bins_returns_one_row_per_bin_with_str_chrom(
    binned: pathlib.Path,
) -> None:
    bins = read_bins(binned)

    assert list(bins.columns) == ["chrom", "start", "end"]
    assert bins["chrom"].tolist() == ["chr1"] * 4 + ["chr2"] * 4
    assert all(isinstance(chrom, str) for chrom in bins["chrom"])
    assert bins["start"].tolist() == [1, 11, 21, 31] * 2
    assert bins["end"].tolist() == [10, 20, 30, 40] * 2
    assert bins["start"].dtype == np.dtype("<i8")


def test_the_index_is_the_position_in_values(binned: pathlib.Path) -> None:
    for table in (read_tracks(binned), read_bins(binned)):
        assert isinstance(table.index, pd.RangeIndex)
        assert list(table.index) == list(range(len(table)))


def test_an_open_file_is_read_and_stays_open(binned: pathlib.Path) -> None:
    with h5py.File(binned, "r") as h5:
        tracks = read_tracks(h5)
        bins = read_bins(h5)

        assert h5.id.valid
        assert len(tracks) == 4
        assert len(bins) == 8
        assert np.asarray(h5["values"]).shape == (8, 4)


def test_a_path_is_closed_after_the_call(binned: pathlib.Path) -> None:
    read_tracks(binned)
    read_bins(str(binned))

    # An open handle would make this exclusive append open fail.
    with h5py.File(binned, "a") as h5:
        assert "tracks" in h5


TEXT = h5py.string_dtype(encoding="utf-8")


def write_table(
    path: pathlib.Path, name: str, dtype: np.dtype[Any],
    rows: list[tuple[Any, ...]], layout: int | None = None,
) -> pathlib.Path:
    with h5py.File(path, "w") as h5:
        h5.create_dataset(name, data=np.array(rows, dtype=dtype))
        if layout is not None:
            h5.attrs["tracks_layout"] = layout
    return path


def test_numbers_are_kept_as_stored_and_empty_strings_stay_empty(
    tmp_path: pathlib.Path,
) -> None:
    dtype = np.dtype([("x", "<f8"), ("n", "<i8"), ("s", TEXT)])
    path = write_table(
        tmp_path / "t.h5", "bins", dtype,
        [(float("nan"), 3, ""), (1.5, -2, "é")])

    bins = read_bins(path)

    assert bins["x"].isna().tolist() == [True, False]
    assert bins["x"].iloc[1] == 1.5
    assert bins["n"].tolist() == [3, -2]
    assert bins["s"].tolist() == ["", "é"]
    assert bins["x"].dtype == np.dtype("<f8")
    assert bins["n"].dtype == np.dtype("<i8")


def test_a_column_the_reader_does_not_know_arrives_in_the_frame(
    tmp_path: pathlib.Path,
) -> None:
    dtype = np.dtype([
        ("binner_type", TEXT), ("resource_ids", TEXT),
        ("binner_def", TEXT), ("extra_column", "<f4"),
        ("extra_text", TEXT)])
    path = write_table(
        tmp_path / "t.h5", "tracks", dtype,
        [("position_score", "a/b", "{}", 0.5, "hello")], layout=2)

    tracks = read_tracks(path)

    assert list(tracks.columns) == [
        "binner_type", "resource_ids", "binner_def", "extra_column",
        "extra_text"]
    assert tracks["extra_column"].tolist() == [0.5]
    assert tracks["extra_text"].tolist() == ["hello"]


def test_a_file_with_the_2026_9_layout_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    dtype = np.dtype([("name", TEXT), ("resource_id", TEXT)])
    path = write_table(tmp_path / "old.h5", "tracks", dtype, [("a", "b")])

    with pytest.raises(
            ValueError,
            match=r"old\.h5.*no tracks_layout.*2026\.9 layout"):
        read_tracks(path)


def test_a_file_with_the_layout_after_2026_9_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    dtype = np.dtype([("name", TEXT), ("resource_ids", TEXT)])
    path = write_table(tmp_path / "mid.h5", "tracks", dtype, [("a", "b")])

    with pytest.raises(
            ValueError,
            match=r"mid\.h5.*no tracks_layout.*layout after 2026\.9"):
        read_tracks(path)


def test_a_file_with_an_unknown_older_layout_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    dtype = np.dtype([("name", TEXT)])
    path = write_table(tmp_path / "odd.h5", "tracks", dtype, [("a",)])

    with pytest.raises(ValueError, match=r"odd\.h5.*no tracks_layout"):
        read_tracks(path)


def test_an_open_file_without_a_layout_attribute_is_refused_and_stays_open(
    tmp_path: pathlib.Path,
) -> None:
    dtype = np.dtype([("name", TEXT), ("resource_id", TEXT)])
    path = write_table(tmp_path / "old.h5", "tracks", dtype, [("a", "b")])

    with h5py.File(path, "r") as h5:
        with pytest.raises(
                ValueError,
                match=r"old\.h5.*no tracks_layout.*2026\.9 layout"):
            read_tracks(h5)

        assert h5.id.valid


def test_a_newer_tracks_layout_is_refused(tmp_path: pathlib.Path) -> None:
    dtype = np.dtype([("name", TEXT)])
    path = write_table(
        tmp_path / "new.h5", "tracks", dtype, [("a",)], layout=3)

    with pytest.raises(ValueError, match=r"new\.h5.*tracks_layout 3"):
        read_tracks(path)


def test_a_missing_dataset_names_the_file_and_the_dataset(
    tmp_path: pathlib.Path,
) -> None:
    dtype = np.dtype([("chrom", TEXT)])
    path = write_table(tmp_path / "nobins.h5", "other", dtype, [("a",)])

    with pytest.raises(
            ValueError, match=r"nobins\.h5.*dataset 'bins'.*no such"):
        read_bins(path)


def test_a_group_instead_of_a_dataset_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    path = tmp_path / "group.h5"
    with h5py.File(path, "w") as h5:
        h5.create_group("bins").create_dataset("chrom", data=[1, 2])

    with pytest.raises(
            ValueError, match=r"group\.h5.*dataset 'bins'.*not a dataset"):
        read_bins(path)


def test_a_plain_array_dataset_is_refused(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "plain.h5"
    with h5py.File(path, "w") as h5:
        h5.create_dataset("bins", data=np.zeros((3, 2)))

    with pytest.raises(
            ValueError, match=r"plain\.h5.*dataset 'bins'.*not compound"):
        read_bins(path)


def test_an_open_file_error_names_the_file(tmp_path: pathlib.Path) -> None:
    path = write_table(
        tmp_path / "open.h5", "other", np.dtype([("a", "<i8")]), [(1,)])

    with h5py.File(path, "r") as h5, pytest.raises(
            ValueError, match=r"open\.h5.*dataset 'bins'"):
        read_bins(h5)


def test_a_missing_path_is_h5py_s_own_error(tmp_path: pathlib.Path) -> None:
    with pytest.raises(OSError):
        read_bins(tmp_path / "nowhere.h5")


def test_a_non_hdf5_path_is_h5py_s_own_error(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "text.h5"
    path.write_text("not hdf5")

    with pytest.raises(OSError, match="signature not found"):
        read_bins(path)


def test_a_missing_tracks_dataset_is_reported_before_the_layout(
    tmp_path: pathlib.Path,
) -> None:
    path = write_table(
        tmp_path / "notracks.h5", "bins", np.dtype([("a", "<i8")]), [(1,)])

    with pytest.raises(ValueError, match=r"notracks\.h5.*dataset 'tracks'"):
        read_tracks(path)


def test_read_values_returns_the_matrix_as_a_float64_array(
    binned: pathlib.Path,
) -> None:
    values = read_values(binned)

    assert isinstance(values, np.ndarray)
    assert values.dtype == np.dtype("float64")
    assert values.shape == (len(read_bins(binned)), len(read_tracks(binned)))
    with h5py.File(binned, "r") as h5:
        stored = np.asarray(h5["values"])
    np.testing.assert_array_equal(values, stored)


def test_read_values_keeps_nan_and_the_row_and_column_order(
    tmp_path: pathlib.Path,
) -> None:
    path = tmp_path / "values.h5"
    matrix = np.array([[1.0, np.nan], [0.5, 2.0], [np.nan, np.nan]])
    with h5py.File(path, "w") as h5:
        h5.create_dataset("values", data=matrix)

    values = read_values(path)

    np.testing.assert_array_equal(values, matrix)
    assert np.isnan(values[0, 1])
    assert values[1, 0] == 0.5


def test_read_values_reads_an_open_file_and_leaves_it_open(
    binned: pathlib.Path,
) -> None:
    with h5py.File(binned, "r") as h5:
        values = read_values(h5)

        assert h5.id.valid
    assert values.shape == (8, 4)


def test_read_values_closes_a_path_after_the_call(
    binned: pathlib.Path,
) -> None:
    read_values(binned)
    read_values(str(binned))

    # An open handle would make this exclusive append open fail.
    with h5py.File(binned, "a") as h5:
        assert "values" in h5


def test_read_values_returns_an_in_memory_matrix_that_owns_its_data(
    binned: pathlib.Path,
) -> None:
    values = read_values(binned)

    # The file is closed by now, so the matrix must not be a view of it.
    assert values.flags.owndata


def test_a_missing_values_dataset_names_the_file_and_the_dataset(
    tmp_path: pathlib.Path,
) -> None:
    path = write_table(
        tmp_path / "novalues.h5", "bins", np.dtype([("a", "<i8")]), [(1,)])

    with pytest.raises(
            ValueError, match=r"novalues\.h5.*dataset 'values'.*no such"):
        read_values(path)


def test_a_values_group_is_refused(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "group.h5"
    with h5py.File(path, "w") as h5:
        h5.create_group("values").create_dataset("x", data=[1.0])

    with pytest.raises(
            ValueError, match=r"group\.h5.*dataset 'values'.*not a dataset"):
        read_values(path)


def test_a_one_dimensional_values_dataset_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    path = tmp_path / "flat.h5"
    with h5py.File(path, "w") as h5:
        h5.create_dataset("values", data=np.zeros(5))

    with pytest.raises(
            ValueError, match=r"flat\.h5.*dataset 'values'.*not a 2-D"):
        read_values(path)


def test_an_open_file_values_error_names_the_file(
    tmp_path: pathlib.Path,
) -> None:
    path = write_table(
        tmp_path / "open.h5", "other", np.dtype([("a", "<i8")]), [(1,)])

    with h5py.File(path, "r") as h5, pytest.raises(
            ValueError, match=r"open\.h5.*dataset 'values'"):
        read_values(h5)


def a_track(
    binner_type: str, resource_ids: str, selector: str, **definition: Any,
) -> dict[str, Any]:
    return {
        "binner_type": binner_type, "resource_ids": resource_ids,
        "selector": selector, "binner_def": json.dumps(definition)}


def labels_of(*tracks: dict[str, Any]) -> list[str]:
    return track_labels(pd.DataFrame(list(tracks)))


def test_a_position_score_label_is_the_resource_and_the_aggregator() -> None:
    assert labels_of(
        a_track("position_score", "hg38/phyloP100way", "phyloP100way",
                aggregator="mean", name=None),
    ) == ["hg38/phyloP100way:mean"]


def test_a_position_score_name_prefixes_the_resource() -> None:
    assert labels_of(
        a_track("position_score", "hg38/phyloP100way", "phyloP100way",
                aggregator="mean", name="cons"),
    ) == ["cons/hg38/phyloP100way:mean"]


def test_a_pooled_fragment_label_is_the_query_the_group_and_aggregator(
) -> None:
    assert labels_of(
        a_track("fragment_score", "frags/s1,frags/s2", "B",
                aggregator="sum", pool=True, resource_query="frags/*",
                name=None),
    ) == ["frags/*:B:sum"]


def test_a_pooled_fragment_name_replaces_the_query() -> None:
    assert labels_of(
        a_track("fragment_score", "frags/s1,frags/s2", "B",
                aggregator="sum", pool=True, resource_query="frags/*",
                name="cells"),
    ) == ["cells:B:sum"]


def test_an_unpooled_fragment_label_is_the_resource_the_group_and_aggregator(
) -> None:
    assert labels_of(
        a_track("fragment_score", "frags/s1", "all", aggregator="sum",
                pool=False, resource_query="frags/s1", name=None),
    ) == ["frags/s1:all:sum"]


def test_an_unpooled_fragment_name_prefixes_the_resource() -> None:
    assert labels_of(
        a_track("fragment_score", "frags/s1", "all", aggregator="sum",
                pool=False, resource_query="frags/s1", name="mine"),
    ) == ["mine/frags/s1:all:sum"]


def test_a_missing_aggregator_leaves_no_trailing_separator() -> None:
    assert labels_of(
        a_track("position_score", "scores/one", "s"),
        a_track("fragment_score", "frags/s1", "all", pool=False),
    ) == ["scores/one", "frags/s1:all"]


def test_equal_labels_all_get_their_position_appended() -> None:
    same = a_track("position_score", "scores/one", "s", aggregator="max")
    other = a_track("position_score", "scores/two", "s", aggregator="max")

    assert labels_of(same, other, same, same) == [
        "scores/one:max#0", "scores/two:max", "scores/one:max#2",
        "scores/one:max#3"]


def test_a_binner_type_with_no_installed_kind_still_gets_a_label() -> None:
    assert labels_of(
        a_track("gone_plugin", "some/res", "sel", aggregator="mean"),
    ) == ["some/res:sel:mean"]


def test_the_suffix_is_the_position_in_the_frame_not_the_index() -> None:
    same = a_track("position_score", "scores/one", "s", aggregator="max")
    frame = pd.DataFrame([same, same], index=[7, 9])

    assert track_labels(frame) == ["scores/one:max#0", "scores/one:max#1"]


def test_a_pooled_fragment_with_no_name_or_query_uses_the_resources() -> None:
    assert labels_of(
        a_track("fragment_score", "frags/s1,frags/s2", "B",
                aggregator="sum", pool=True, name=None),
    ) == ["frags/s1,frags/s2:B:sum"]


def test_an_unknown_binner_type_with_an_empty_selector_has_no_separator(
) -> None:
    assert labels_of(
        a_track("gone_plugin", "some/res", "", aggregator="mean"),
    ) == ["some/res:mean"]


def test_read_values_docstring_names_no_missing_tool() -> None:
    doc = read_values.__doc__ or ""

    assert "binning_tool_view" not in doc
