# pylint: disable=C0114,C0116
"""The spec classes of a ``fragment_score_binner`` entry.

A notebook user builds a :class:`FragmentEntry` by hand.  The run
definition builds the same object with ``entry_from_config``.
"""
import dataclasses
from collections.abc import Callable
from typing import Any, cast

import pytest
from gain.binning import (
    Aggregate,
    CellGrouping,
    ConstantContribution,
    ConstantGroup,
    FragmentEntry,
    MetaFilter,
    MetaSource,
    RunDefinitionError,
    ScoreContribution,
    ValueGrouping,
)
from gain.binning.fragment_binner import FragmentScoreBinner

LABEL = "binners[0]"
QUERY = "frags/*"

FORMS: list[tuple[str, dict[str, Any], FragmentEntry]] = [
    ("constant group",
     {"resource_query": QUERY, "group": {"group": "all"}},
     FragmentEntry(QUERY, group=ConstantGroup("all"))),
    ("cell grouping by the convention",
     {"resource_query": QUERY, "group": {}},
     FragmentEntry(QUERY, group=CellGrouping())),
    ("cell grouping with a relative local meta file",
     {"resource_query": QUERY,
      "group": {"group_meta_column": "type"},
      "meta": {"file_name": "cells.tsv",
               "filter": [{"column": "sample", "label": "sample_id"},
                          {"column": "batch", "value": 2}]}},
     FragmentEntry(
         QUERY, group=CellGrouping(group_meta_column="type"),
         meta=MetaSource(
             file_name="cells.tsv",
             filters=(MetaFilter("sample", label="sample_id"),
                      MetaFilter("batch", value="2"))))),
    ("value grouping pooled",
     {"resource_query": QUERY, "group": {"group_score_id": "cell"},
      "value": {"score_id": "count"}},
     FragmentEntry(
         QUERY, group=ValueGrouping("cell"),
         value=ScoreContribution("count"))),
    ("value grouping unpooled",
     {"resource_query": QUERY, "pool": False, "name": "n",
      "group": {"group_score_id": "cell"}, "value": {"value": 2.5}},
     FragmentEntry(
         QUERY, pool=False, name="n", group=ValueGrouping("cell"),
         value=ConstantContribution(2.5))),
    ("no group",
     {"resource_query": QUERY, "search_term": "atac"},
     FragmentEntry(QUERY, search_term="atac")),
    ("coverage_profile aggregate without uncovered_value",
     {"resource_query": QUERY,
      "aggregate": {"mode": "coverage_profile", "aggregator": "mean"}},
     FragmentEntry(
         QUERY, aggregate=Aggregate("coverage_profile", "mean"))),
]


@pytest.mark.parametrize(
    "config,by_hand", [form[1:] for form in FORMS],
    ids=[form[0] for form in FORMS])
def test_a_hand_built_entry_equals_the_parsed_entry(
    config: dict[str, Any], by_hand: FragmentEntry,
) -> None:
    parsed = FragmentScoreBinner.entry_from_config(LABEL, config)

    assert parsed == by_hand


def test_a_coverage_profile_aggregate_leaves_uncovered_bases_at_0() -> None:
    assert Aggregate("coverage_profile").effective_uncovered_value == 0


def entry(**keys: Any) -> dict[str, Any]:
    return {"resource_query": QUERY, **keys}


#: Each refusal that needs no resource: the config that the parser
#: refuses, the label that its message starts with, and the constructor
#: call that refuses the same thing.
REFUSALS: list[tuple[str, dict[str, Any], str, Callable[[], object]]] = [
    # group
    ("constant group name",
     entry(group={"group": ""}), "binners[0].group",
     lambda: ConstantGroup("")),
    ("value grouping score",
     entry(group={"group_score_id": 3}), "binners[0].group",
     lambda: ValueGrouping(cast(Any, 3))),
    ("cell grouping score",
     entry(group={"cell_score_id": ""}), "binners[0].group",
     lambda: CellGrouping(cell_score_id="")),
    ("cell grouping barcode column",
     entry(group={"cell_meta_column": None}), "binners[0].group",
     lambda: CellGrouping(cell_meta_column=cast(Any, None))),
    ("cell grouping group column",
     entry(group={"group_meta_column": ""}), "binners[0].group",
     lambda: CellGrouping(group_meta_column="")),
    # aggregate
    ("unknown mode",
     entry(aggregate={"mode": "fragment_end"}), "binners[0].aggregate",
     lambda: Aggregate("fragment_end")),
    ("aggregator not a string",
     entry(aggregate={"aggregator": 1}), "binners[0].aggregate",
     lambda: Aggregate(aggregator=cast(Any, 1))),
    ("aggregator not a number",
     entry(aggregate={"aggregator": "join"}), "binners[0].aggregate",
     lambda: Aggregate(aggregator="join")),
    ("uncovered_value in fragment_start mode",
     entry(aggregate={"uncovered_value": 0}), "binners[0].aggregate",
     lambda: Aggregate(uncovered_value=0)),
    ("null uncovered_value in fragment_start mode",
     entry(aggregate={"uncovered_value": None}), "binners[0].aggregate",
     lambda: Aggregate(uncovered_value=None)),
    ("count in a length mode",
     entry(aggregate={"mode": "fragment_length", "aggregator": "count"}),
     "binners[0].aggregate",
     lambda: Aggregate("fragment_length", "count")),
    ("power aggregator in a length mode",
     entry(aggregate={"mode": "coverage_profile", "aggregator": "product"}),
     "binners[0].aggregate",
     lambda: Aggregate("coverage_profile", "product")),
    ("NaN uncovered_value",
     entry(aggregate={"mode": "coverage_profile",
                      "uncovered_value": float("nan")}),
     "binners[0].aggregate",
     lambda: Aggregate("coverage_profile", uncovered_value=float("nan"))),
    ("boolean uncovered_value",
     entry(aggregate={"mode": "fragment_length", "uncovered_value": True}),
     "binners[0].aggregate",
     lambda: Aggregate("fragment_length", uncovered_value=True)),
    # meta
    ("no meta source",
     entry(group={}, meta={"filter": []}), "binners[0].meta",
     MetaSource),
    ("two meta sources",
     entry(group={}, meta={"resource_id": "m", "file_name": "m.csv"}),
     "binners[0].meta",
     lambda: MetaSource(resource_id="m", file_name="m.csv")),
    ("file key with a resource",
     entry(group={}, meta={"resource_id": "m", "file_format": "csv"}),
     "binners[0].meta",
     lambda: MetaSource(resource_id="m", file_format="csv")),
    ("empty resource_id",
     entry(group={}, meta={"resource_id": ""}), "binners[0].meta",
     lambda: MetaSource(resource_id="")),
    ("resource_label not a string",
     entry(group={}, meta={"resource_label": ["a"]}), "binners[0].meta",
     lambda: MetaSource(resource_label=cast(Any, ["a"]))),
    ("empty file_name",
     entry(group={}, meta={"file_name": ""}), "binners[0].meta",
     lambda: MetaSource(file_name="")),
    ("empty file_format",
     entry(group={}, meta={"file_name": "m", "file_format": ""}),
     "binners[0].meta",
     lambda: MetaSource(file_name="m", file_format="")),
    ("unknown file_format",
     entry(group={}, meta={"file_name": "m", "file_format": "parquet"}),
     "binners[0].meta",
     lambda: MetaSource(file_name="m", file_format="parquet")),
    ("separator of an excel file",
     entry(group={}, meta={"file_name": "m.xlsx", "file_separator": ";"}),
     "binners[0].meta",
     lambda: MetaSource(file_name="m.xlsx", file_separator=";")),
    ("empty file_separator",
     entry(group={}, meta={"file_name": "m.csv", "file_separator": ""}),
     "binners[0].meta",
     lambda: MetaSource(file_name="m.csv", file_separator="")),
    # meta filter
    ("filter column",
     entry(group={}, meta={"resource_id": "m",
                           "filter": [{"column": "", "value": "a"}]}),
     "binners[0].meta.filter[0]",
     lambda: MetaFilter("", value="a")),
    ("filter value and label",
     entry(group={}, meta={"resource_id": "m", "filter": [
         {"column": "c", "value": "a", "label": "b"}]}),
     "binners[0].meta.filter[0]",
     lambda: MetaFilter("c", value="a", label="b")),
    ("filter label",
     entry(group={}, meta={"resource_id": "m",
                           "filter": [{"column": "c", "label": ""}]}),
     "binners[0].meta.filter[0]",
     lambda: MetaFilter("c", label="")),
    ("filter without an operand",
     entry(group={}, meta={"resource_id": "m",
                           "filter": [{"column": "c"}]}),
     "binners[0].meta.filter[0]",
     lambda: MetaFilter("c")),
    ("filter column with two operands",
     entry(group={}, meta={"resource_id": "m", "filter": [
         {"column": "", "value": "a", "label": "b"}]}),
     "binners[0].meta.filter[0]",
     lambda: MetaFilter("", value="a", label="b")),
    ("empty filter value",
     entry(group={}, meta={"resource_id": "m",
                           "filter": [{"column": "c", "value": ""}]}),
     "binners[0].meta.filter[0]",
     lambda: MetaFilter("c", value="")),
    # value
    ("constant not a number",
     entry(value={"value": "1"}), "binners[0].value",
     lambda: ConstantContribution(cast(Any, "1"))),
    ("boolean constant",
     entry(value={"value": True}), "binners[0].value",
     lambda: ConstantContribution(value=True)),
    ("contribution score",
     entry(value={"score_id": ""}), "binners[0].value",
     lambda: ScoreContribution("")),
    # entry
    ("meta with a value grouping",
     entry(group={"group_score_id": "cell"}, meta={"resource_id": "m"}),
     "binners[0]",
     lambda: FragmentEntry(
         QUERY, group=ValueGrouping("cell"),
         meta=MetaSource(resource_id="m"))),
    ("meta with a constant group",
     entry(group={"group": "all"}, meta={"resource_id": "m"}),
     "binners[0]",
     lambda: FragmentEntry(
         QUERY, group=ConstantGroup("all"),
         meta=MetaSource(resource_id="m"))),
    ("pool not a boolean",
     entry(pool="yes"), "binners[0]",
     lambda: FragmentEntry(QUERY, pool=cast(Any, "yes"))),
    ("missing resource_query",
     {"group": {}}, "binners[0]",
     lambda: FragmentEntry(cast(Any, None))),
    ("search_term not a string",
     entry(search_term=3), "binners[0]",
     lambda: FragmentEntry(QUERY, search_term=cast(Any, 3))),
]


#: The words of each refusal of :data:`REFUSALS`, as the parser gave them
#: before the spec classes: a change of the words fails here.
WORDS: dict[str, str] = {
    "constant group name":
        "group must be a non-empty string, not ''",
    "value grouping score":
        "group_score_id must be a non-empty string, not 3",
    "cell grouping score":
        "cell_score_id must be a non-empty string, not ''",
    "cell grouping barcode column":
        "cell_meta_column must be a non-empty string, not None",
    "cell grouping group column":
        "group_meta_column must be a non-empty string, not ''",
    "unknown mode":
        "mode 'fragment_end' is not supported; use one of "
        "fragment_start, fragment_length, coverage_profile",
    "aggregator not a string":
        "aggregator must be a non-empty string, not 1",
    "aggregator not a number":
        "aggregator 'join' does not produce a number; use one of "
        "count, max, mean, median, min, product, sum",
    "uncovered_value in fragment_start mode":
        "uncovered_value does not apply in fragment_start mode, where"
        " a bin reduces only the fragments starting in it; give mode:"
        " fragment_length or coverage_profile, or drop "
        "uncovered_value",
    "null uncovered_value in fragment_start mode":
        "uncovered_value does not apply in fragment_start mode, where"
        " a bin reduces only the fragments starting in it; give mode:"
        " fragment_length or coverage_profile, or drop "
        "uncovered_value",
    "count in a length mode":
        "aggregator 'count' would count base pairs, not fragments, in"
        " fragment_length mode; for the covered base pairs give "
        "aggregator: sum with value: {value: 1}",
    "power aggregator in a length mode":
        "aggregator 'product' would weigh a value by its overlap as a"
        " power in coverage_profile mode; use one of max, mean, "
        "median, min, sum",
    "NaN uncovered_value":
        "uncovered_value must be a number or null, not nan",
    "boolean uncovered_value":
        "uncovered_value must be a number or null, not True",
    "no meta source":
        "give exactly one of resource_id, resource_label or "
        "file_name; got none",
    "two meta sources":
        "give exactly one of resource_id, resource_label or "
        "file_name; got file_name, resource_id",
    "file key with a resource":
        "file_format and file_separator go only with file_name",
    "empty resource_id":
        "resource_id must be a non-empty string, not ''",
    "resource_label not a string":
        "resource_label must be a non-empty string, not ['a']",
    "empty file_name":
        "file_name must be a non-empty string, not ''",
    "empty file_format":
        "file_format must be a non-empty string, not ''",
    "unknown file_format":
        "file_format must be one of csv, tsv, excel, not 'parquet'",
    "separator of an excel file":
        "file_separator does not apply to an excel file",
    "empty file_separator":
        "file_separator must be a non-empty string, not ''",
    "filter column":
        "column must be a non-empty string, not ''",
    "filter value and label":
        "give exactly one of value or label",
    "filter label":
        "label must be a non-empty string, not ''",
    "filter without an operand":
        "give exactly one of value or label",
    "filter column with two operands":
        "column must be a non-empty string, not ''",
    "empty filter value":
        "value must not be empty",
    "constant not a number":
        "value must be a number, not '1'",
    "boolean constant":
        "value must be a number, not True",
    "contribution score":
        "score_id must be a non-empty string, not ''",
    "meta with a value grouping":
        "meta maps cells to groups, and is not given with a group of "
        "group_score_id, whose values are the groups",
    "meta with a constant group":
        "meta maps cells to groups, and is given only with a group of"
        " cell_score_id, cell_meta_column and group_meta_column, or "
        "with no group on a resource labelled 'cell_meta_resource_id'",
    "pool not a boolean":
        "pool must be true or false, not 'yes'",
    "missing resource_query":
        "resource_query is required and must be a string",
    "search_term not a string":
        "search_term must be a string, not 3",
}


@pytest.mark.parametrize(
    "name,construct", [(refusal[0], refusal[3]) for refusal in REFUSALS],
    ids=[refusal[0] for refusal in REFUSALS])
def test_a_constructor_refuses_with_the_words_of_today(
    name: str, construct: Callable[[], object],
) -> None:
    with pytest.raises(RunDefinitionError) as built:
        construct()

    assert str(built.value) == WORDS[name]


@pytest.mark.parametrize(
    "config,label,construct", [refusal[1:] for refusal in REFUSALS],
    ids=[refusal[0] for refusal in REFUSALS])
def test_a_constructor_refuses_with_the_words_of_the_parser(
    config: dict[str, Any], label: str, construct: Callable[[], object],
) -> None:
    with pytest.raises(RunDefinitionError) as parsed:
        FragmentScoreBinner.entry_from_config(LABEL, config)
    with pytest.raises(RunDefinitionError) as built:
        construct()

    assert str(parsed.value) == f"{label}: {built.value}"


def test_a_constructor_refusal_names_no_label() -> None:
    with pytest.raises(RunDefinitionError) as built:
        Aggregate("fragment_end")

    assert str(built.value) == (
        "mode 'fragment_end' is not supported; use one of "
        "fragment_start, fragment_length, coverage_profile")


@pytest.mark.parametrize("key", ["resource_id", "resource_label", "file_name"])
def test_a_null_meta_source_is_refused_by_its_key(key: str) -> None:
    with pytest.raises(RunDefinitionError) as parsed:
        FragmentScoreBinner.entry_from_config(
            LABEL, entry(group={}, meta={key: None}))

    assert str(parsed.value) == (
        f"binners[0].meta: {key} must be a non-empty string, not None")


def test_a_filter_without_an_operand_is_refused() -> None:
    with pytest.raises(RunDefinitionError) as built:
        MetaFilter("sample")

    assert str(built.value) == "give exactly one of value or label"


def test_a_filter_value_that_is_not_text_is_refused() -> None:
    # A cell matches by its text, so a number would never match.  The
    # parser turns a YAML number into its text: see the test below.
    with pytest.raises(RunDefinitionError) as built:
        MetaFilter("batch", value=2)  # type: ignore[arg-type]

    assert str(built.value) == "value must be a non-empty string, not 2"


def test_a_parsed_filter_value_that_is_a_number_becomes_text() -> None:
    parsed = FragmentScoreBinner.entry_from_config(LABEL, entry(group={}, meta={
        "resource_id": "meta/cells",
        "filter": [{"column": "batch", "value": 2}]}))

    assert parsed.meta == MetaSource(
        resource_id="meta/cells", filters=(MetaFilter("batch", value="2"),))


def test_a_null_filter_label_is_refused_by_its_key() -> None:
    with pytest.raises(RunDefinitionError) as parsed:
        FragmentScoreBinner.entry_from_config(LABEL, entry(group={}, meta={
            "resource_id": "meta/cells",
            "filter": [{"column": "sample", "label": None}]}))

    assert str(parsed.value) == (
        "binners[0].meta.filter[0]: label must be a non-empty string, "
        "not None")


def test_a_replaced_aggregate_takes_the_default_of_its_mode() -> None:
    replaced = dataclasses.replace(
        Aggregate("coverage_profile"), mode="fragment_length")

    assert replaced == Aggregate("fragment_length")
    assert replaced.effective_uncovered_value is None


def test_a_replaced_fragment_start_aggregate_is_not_refused() -> None:
    replaced = dataclasses.replace(Aggregate(), aggregator="max")

    assert replaced == Aggregate(aggregator="max")


def test_a_replaced_aggregate_leaves_uncovered_bases_at_0() -> None:
    replaced = dataclasses.replace(Aggregate(), mode="coverage_profile")

    assert replaced.effective_uncovered_value == 0


def test_a_replaced_meta_file_takes_the_format_of_its_suffix() -> None:
    replaced = dataclasses.replace(
        MetaSource(file_name="a.csv"), file_name="b.tsv")

    assert replaced == MetaSource(file_name="b.tsv")
    assert replaced.effective_file_format == "tsv"


def test_a_blank_search_term_equals_none() -> None:
    parsed = FragmentScoreBinner.entry_from_config(
        LABEL, entry(search_term=" "))

    assert parsed == FragmentEntry(QUERY)
