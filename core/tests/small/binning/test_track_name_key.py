"""An entry's ``name`` key sets the base of its tracks' names (F30)."""
# pylint: disable=W0621,C0116
from typing import Any

import pytest
from gain.binning.run_definition import (
    RunDefinition,
    RunDefinitionError,
    parse_run_definition,
)
from gain.genomic_resources.reference_genome import ReferenceGenome
from gain.genomic_resources.repository import GenomicResourceRepo

FRAGMENT = "fragment_score_binner"
POSITION = "position_score_binner"


def parse_entries(
    entries: list[dict[str, Any]], repo: GenomicResourceRepo,
    genome: ReferenceGenome,
) -> RunDefinition:
    return parse_run_definition({
        "bins": {"bin_size": 10},
        "binners": entries,
    }, repo, genome)


def track_names(
    entries: list[dict[str, Any]], repo: GenomicResourceRepo,
    genome: ReferenceGenome,
) -> list[str]:
    return [t.name for t in parse_entries(entries, repo, genome).tracks]


def test_a_named_pooled_fragment_entry_names_its_tracks_by_name_and_group(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    names = track_names([{FRAGMENT: {
        "resource_query": "frags/*", "group": {"group": "bulk"},
        "name": "atac"}}], repo, genome)

    assert names == ["atac:bulk"]


def test_a_named_unpooled_fragment_entry_prefixes_each_resource_id(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    names = track_names([{FRAGMENT: {
        "resource_query": "frags/*", "group": {"group": "bulk"},
        "pool": False, "name": "atac"}}], repo, genome)

    assert names == ["atac/frags/s1:bulk", "atac/frags/s2:bulk"]


def test_a_named_position_score_entry_prefixes_each_resource_id(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    names = track_names([{POSITION: {
        "resource_query": "scores/*", "name": "cons"}}], repo, genome)

    assert names == ["cons/scores/one", "cons/scores/two"]


@pytest.mark.parametrize("entry,expected", [
    ({FRAGMENT: {"resource_query": "frags/*",
                 "group": {"group": "bulk"}}},
     ["frags/*:bulk"]),
    ({FRAGMENT: {"resource_query": "frags/*", "group": {"group": "bulk"},
                 "pool": False}},
     ["frags/s1:bulk", "frags/s2:bulk"]),
    ({POSITION: {"resource_query": "scores/*"}},
     ["scores/one", "scores/two"]),
], ids=["pooled-fragment", "unpooled-fragment", "position-score"])
def test_an_entry_without_name_keeps_the_kind_s_own_names(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
    entry: dict[str, Any], expected: list[str],
) -> None:
    assert track_names([entry], repo, genome) == expected


def test_two_entries_differing_only_in_name_are_two_tracks(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    entry = {"resource_query": "scores/one", "aggregator": "max"}

    run = parse_entries([
        {POSITION: {**entry, "name": "a"}},
        {POSITION: {**entry, "name": "b"}},
    ], repo, genome)

    assert [(t.name, t.resource_ids, t.aggregator) for t in run.tracks] == [
        ("a/scores/one", ("scores/one",), "max"),
        ("b/scores/one", ("scores/one",), "max"),
    ]


def test_one_track_from_two_entries_is_refused_suggesting_name(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    entry = {"resource_query": "frags/s1", "group": {"group": "bulk"},
             "pool": False}

    with pytest.raises(RunDefinitionError) as excinfo:
        parse_entries([{FRAGMENT: entry}, {FRAGMENT: entry}], repo, genome)

    message = str(excinfo.value)
    assert "binners[0]" in message
    assert "binners[1]" in message
    assert "add name to one of them" in message


@pytest.mark.parametrize("name", [3, ["a"], "", None])
@pytest.mark.parametrize("kind,query", [
    (FRAGMENT, "frags/*"), (POSITION, "scores/*")])
def test_a_name_that_is_not_a_non_empty_string_is_refused_naming_the_entry(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
    kind: str, query: str, name: Any,
) -> None:
    entries = [
        {POSITION: {"resource_query": "scores/one"}},
        {kind: {"resource_query": query, "name": name}},
    ]

    with pytest.raises(RunDefinitionError) as excinfo:
        parse_entries(entries, repo, genome)

    message = str(excinfo.value)
    assert message.startswith("binners[1]")
    assert "name must be a non-empty string" in message
