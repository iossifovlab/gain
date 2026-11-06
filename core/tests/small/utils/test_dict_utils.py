"""Pin ``recursive_dict_update`` and its in-place twin, which gpf calls."""
from collections.abc import Callable
from typing import Any

import pytest
from gain.utils.dict_utils import (
    recursive_dict_update,
    recursive_dict_update_inplace,
)

Merge = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


def _merge_inplace(
    input_dict: dict[str, Any], updater_dict: dict[str, Any],
) -> dict[str, Any]:
    recursive_dict_update_inplace(input_dict, updater_dict)
    return input_dict


MERGES = pytest.mark.parametrize(
    "merge", [recursive_dict_update, _merge_inplace],
    ids=["copy", "inplace"])


@MERGES
def test_merge_keeps_the_keys_of_both_disjoint_dicts(merge: Merge) -> None:
    assert merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}


@MERGES
def test_merge_overwrites_a_scalar_with_the_updater_value(
    merge: Merge,
) -> None:
    assert merge({"a": 1, "b": 2}, {"a": 3}) == {"a": 3, "b": 2}


@MERGES
def test_merge_merges_nested_dicts_key_by_key(merge: Merge) -> None:
    """Merge a nested dict key by key, at every level."""
    result = merge(
        {"a": {"x": 1, "y": {"p": 1, "q": 2}}},
        {"a": {"y": {"q": 3}, "z": 4}},
    )

    assert result == {"a": {"x": 1, "y": {"p": 1, "q": 3}, "z": 4}}


@MERGES
def test_merge_sets_a_dict_on_a_missing_key(merge: Merge) -> None:
    assert merge({"a": 1}, {"b": {"x": 1}}) == {"a": 1, "b": {"x": 1}}


@MERGES
def test_merge_refuses_a_dict_over_a_non_dict_value(merge: Merge) -> None:
    with pytest.raises(AssertionError):
        merge({"a": 1}, {"a": {"x": 1}})


@MERGES
def test_merge_replaces_a_dict_with_a_non_dict_value(merge: Merge) -> None:
    assert merge({"a": {"x": 1}}, {"a": 5}) == {"a": 5}


def test_recursive_dict_update_leaves_its_input_unchanged() -> None:
    """Return a deep copy and keep the input dict as it was."""
    input_dict: dict[str, Any] = {"a": {"x": 1}, "b": 2, "c": {"k": 1}}

    result = recursive_dict_update(input_dict, {"a": {"y": 2}, "b": 3})
    result["c"]["k"] = 2

    assert input_dict == {"a": {"x": 1}, "b": 2, "c": {"k": 1}}
    assert result == {"a": {"x": 1, "y": 2}, "b": 3, "c": {"k": 2}}
    assert result["a"] is not input_dict["a"]
    assert result["c"] is not input_dict["c"]


def test_recursive_dict_update_inplace_changes_its_input() -> None:
    """Merge into the input dict and keep its nested dict objects."""
    input_dict: dict[str, Any] = {"a": {"x": 1}, "b": 2}
    inner = input_dict["a"]

    recursive_dict_update_inplace(input_dict, {"a": {"y": 2}, "b": 3})

    assert input_dict == {"a": {"x": 1, "y": 2}, "b": 3}
    assert input_dict["a"] is inner
