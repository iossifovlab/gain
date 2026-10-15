# pylint: disable=W0621,C0114,C0116,W0212,W0613
# Same header as test_fragment_raw_value_grouping.py: fixtures shadow names
# (W0621, W0613), tests need no docstrings (C0114, C0116) and reach into
# private binner names (W0212).
"""``CellGrouping``: a filter column becomes text once per call."""
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest
from gain.binning import fragment_binner
from gain.binning.fragment_binner import (
    CellGrouping,
    DuplicateBarcodeError,
    MetaFilter,
    MetaTable,
)

# The metadata table is stubbed, so no test reads the repository.
NO_GRR: Any = None
KINDS = ["a", 2, 2.0, 2.5, float("nan"), None, "", pd.NA, "2", "b"]


def a_resource(**labels: Any) -> Any:
    return SimpleNamespace(
        resource_id="r", get_labels=lambda: labels)


def a_grouping(*filters: MetaFilter) -> CellGrouping:
    return CellGrouping(
        cell_score_id="cell", cell_meta_column="barcode",
        group_meta_column="group", table=MetaTable(resource_id="meta"),
        filters=filters)


def a_table() -> pd.DataFrame:
    return pd.DataFrame({
        "kind": pd.Series(KINDS, dtype=object),
        "barcode": [f"B{i}" for i in range(len(KINDS))],
        "group": [f"G{i}" for i in range(len(KINDS))],
    })


def expected(operand: str | None) -> dict[str, str]:
    return {
        f"B{i}": f"G{i}" for i, cell in enumerate(KINDS)
        if fragment_binner._as_text(cell) == operand}


@pytest.mark.parametrize("operand", ["a", "2", "2.5", "b", "zzz", None])
def test_groups_of_selects_rows_by_text_of_cell(operand: str | None) -> None:
    grouping = a_grouping(MetaFilter("kind", value=operand))

    result = grouping.groups_of(a_table(), a_resource())

    assert result == expected(operand)


def test_groups_of_selects_missing_cells_for_none_operand_label() -> None:
    grouping = a_grouping(MetaFilter("kind", label="absent"))

    result = grouping.groups_of(a_table(), a_resource())

    assert result == {"B4": "G4", "B5": "G5", "B6": "G6", "B7": "G7"}


def test_groups_of_matches_integral_float_to_int_operand() -> None:
    grouping = a_grouping(MetaFilter("kind", label="n"))

    result = grouping.groups_of(a_table(), a_resource(n=2))

    assert result == {"B1": "G1", "B2": "G2", "B8": "G8"}


def test_track_maps_matches_per_resource_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(MetaTable, "load", lambda *_: a_table())
    grouping = a_grouping(MetaFilter("kind", label="n"))
    resources = [a_resource(n=2), a_resource(n="a"), a_resource()]
    index_of = {f"G{i}": i for i in range(len(KINDS))}

    result = grouping.track_maps(NO_GRR, resources, index_of)

    assert result == [
        {"B1": 1, "B2": 2, "B8": 8}, {"B0": 0},
        {"B4": 4, "B5": 5, "B6": 6, "B7": 7}]


def test_track_maps_converts_filter_cells_once_not_per_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table = pd.DataFrame({
        "kind": [f"k{i % 5}" for i in range(50)],
        "barcode": [f"B{i}" for i in range(50)],
        "group": ["G"] * 50,
    })
    monkeypatch.setattr(MetaTable, "load", lambda *_: table)
    seen: list[Any] = []
    original = fragment_binner._as_text

    def counting(value: Any) -> str | None:
        seen.append(value)
        return original(value)

    monkeypatch.setattr(fragment_binner, "_as_text", counting)
    grouping = a_grouping(MetaFilter("kind", value="k1"))
    resources = [a_resource() for _ in range(10)]

    grouping.track_maps(NO_GRR, resources, {"G": 0})

    assert sum(1 for v in seen if str(v).startswith("k")) == 50


def test_resolve_grouping_converts_filter_cells_once_not_per_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table = pd.DataFrame({
        "kind": [f"k{i % 5}" for i in range(50)],
        "barcode": [f"B{i}" for i in range(50)],
        "group": ["G"] * 50,
    })
    meta_table = MetaTable(resource_id="meta")
    tables = fragment_binner._Tables("job", NO_GRR)
    tables.loaded[meta_table] = table
    monkeypatch.setattr(
        fragment_binner, "_score_type", lambda *_: "float")
    seen: list[Any] = []
    original = fragment_binner._as_text

    def counting(value: Any) -> str | None:
        seen.append(value)
        return original(value)

    monkeypatch.setattr(fragment_binner, "_as_text", counting)
    meta = fragment_binner._MetaSpec(
        filters=(MetaFilter("kind", value="k1"),), table=meta_table)
    resources = [a_resource() for _ in range(10)]

    fragment_binner._resolve_grouping(
        "job", {
            "cell_score_id": "cell", "cell_meta_column": "barcode",
            "group_meta_column": "group"},
        meta, resources, tables)

    assert sum(1 for v in seen if str(v).startswith("k")) == 50


def a_two_column_table() -> pd.DataFrame:
    return pd.DataFrame({
        "kind": ["a", "a", "b", "b", "a"],
        "tag": ["x", "y", "x", "y", "x"],
        "barcode": [f"B{i}" for i in range(5)],
        "group": [f"G{i}" for i in range(5)],
    })


def test_groups_of_intersects_conjuncts_on_different_columns() -> None:
    grouping = a_grouping(
        MetaFilter("kind", value="a"), MetaFilter("tag", value="x"))

    result = grouping.groups_of(a_two_column_table(), a_resource())

    assert result == {"B0": "G0", "B4": "G4"}


def test_groups_of_intersects_conjuncts_on_the_same_column() -> None:
    table = a_table()
    both = a_grouping(
        MetaFilter("kind", value="2"), MetaFilter("kind", label="n"))

    selected = both.groups_of(table, a_resource(n=2))
    disjoint = both.groups_of(table, a_resource(n="a"))

    assert selected == {"B1": "G1", "B2": "G2", "B8": "G8"}
    assert len(disjoint) == 0


def test_track_maps_converts_a_column_once_for_repeated_conjuncts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table = pd.DataFrame({
        "kind": [f"k{i % 5}" for i in range(50)],
        "barcode": [f"B{i}" for i in range(50)],
        "group": ["G"] * 50,
    })
    monkeypatch.setattr(MetaTable, "load", lambda *_: table)
    seen: list[Any] = []
    original = fragment_binner._as_text

    def counting(value: Any) -> str | None:
        seen.append(value)
        return original(value)

    monkeypatch.setattr(fragment_binner, "_as_text", counting)
    grouping = a_grouping(
        MetaFilter("kind", value="k1"), MetaFilter("kind", value="k1"))

    grouping.track_maps(NO_GRR, [a_resource()], {"G": 0})

    assert sum(1 for v in seen if str(v).startswith("k")) == 50


def a_table_with_repeated_barcode() -> pd.DataFrame:
    return pd.DataFrame({
        "kind": ["a", "a", "b"],
        "barcode": ["B0", "B0", "B2"],
        "group": ["G0", "G1", "G2"],
    })


def test_groups_of_raises_on_duplicate_barcode_with_prepared() -> None:
    table = a_table_with_repeated_barcode()
    grouping = a_grouping(MetaFilter("kind", value="a"))
    prepared = grouping.prepare_filters(table)

    with pytest.raises(DuplicateBarcodeError, match="B0"):
        grouping.groups_of(table, a_resource(), prepared)


def test_groups_of_ignores_duplicate_barcode_outside_the_filter() -> None:
    table = a_table_with_repeated_barcode()
    grouping = a_grouping(MetaFilter("kind", value="b"))
    prepared = grouping.prepare_filters(table)

    result = grouping.groups_of(table, a_resource(), prepared)

    assert result == {"B2": "G2"}


def test_track_maps_raises_on_duplicate_barcode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        MetaTable, "load", lambda *_: a_table_with_repeated_barcode())
    grouping = a_grouping(MetaFilter("kind", value="a"))

    with pytest.raises(DuplicateBarcodeError, match="B0"):
        grouping.track_maps(NO_GRR, [a_resource()], {"G0": 0, "G1": 1})
