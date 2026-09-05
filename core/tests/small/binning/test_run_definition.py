# pylint: disable=W0621,C0114,C0116,W0212,W0613
from typing import Any

import pytest
from gain.binning.binners import Track
from gain.binning.run_definition import (
    RunDefinition,
    RunDefinitionError,
    parse_run_definition,
)
from gain.genomic_resources.reference_genome import ReferenceGenome
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.utils.regions import BedRegion

from .conftest import CHR1_LENGTH, CHR2_LENGTH


def parse_one_entry(
    entry: dict[str, Any], repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> RunDefinition:
    return parse_run_definition({
        "bins": {"bin_size": 10},
        "binners": [{"position_score_binner": entry}],
    }, repo, genome)


def test_an_exact_id_entry_becomes_one_track_named_by_the_resource(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    run = parse_one_entry({"resource_query": "scores/one"}, repo, genome)

    assert run.tracks == [
        Track(
            name="scores/one", resource_id="scores/one", score_id="s",
            aggregator="max", none_value_replacement=None,
            binner="position_score_binner"),
    ]


def test_a_glob_entry_expands_to_its_matches_sorted_by_resource_id(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    # The glob is a repository search: it must not reach the genome or the
    # score outside the ``scores/`` prefix, and the order is by id, not
    # whatever the repository yields.
    run = parse_one_entry({"resource_query": "scores/*"}, repo, genome)

    assert [(t.name, t.score_id, t.aggregator) for t in run.tracks] == [
        ("scores/one", "s", "max"),
        ("scores/two", "t", "mean"),
    ]


def test_an_entry_matching_nothing_is_a_parse_error_naming_the_entry(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    # The one deliberate departure from the prototype, which silently
    # produced no column: a typo must not shrink the matrix.
    config = {
        "bins": {"bin_size": 10},
        "binners": [
            {"position_score_binner": {"resource_query": "scores/one"}},
            {"position_score_binner": {"resource_query": "scoers/*"}},
        ],
    }

    with pytest.raises(RunDefinitionError) as excinfo:
        parse_run_definition(config, repo, genome)

    assert "scoers/*" in str(excinfo.value)
    assert "binners[1]" in str(excinfo.value)


def test_an_entry_overrides_the_aggregator_and_replacement_for_every_match(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    run = parse_one_entry({
        "resource_query": "scores/*",
        "aggregator": "min",
        "none_value_replacement": 0.0,
    }, repo, genome)

    assert [
        (t.name, t.aggregator, t.none_value_replacement) for t in run.tracks
    ] == [
        ("scores/one", "min", 0.0),
        ("scores/two", "min", 0.0),
    ]


def test_search_term_narrows_the_query_on_an_indexed_repository(
    indexed_repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    # The full-text index holds the resource id among its fields, so a
    # term naming one id narrows the glob to that resource.
    run = parse_one_entry(
        {"resource_query": "scores/*", "search_term": "one"},
        indexed_repo, genome)

    assert [track.resource_id for track in run.tracks] == ["scores/one"]


def test_a_search_term_that_eliminates_every_match_names_the_term(
    indexed_repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    # The id matches; the term is what left nothing, and the message
    # must say so rather than blame the query.
    with pytest.raises(RunDefinitionError) as excinfo:
        parse_one_entry(
            {"resource_query": "scores/one", "search_term": "zzzz"},
            indexed_repo, genome)

    message = str(excinfo.value)
    assert "binners[0]" in message
    assert "'zzzz'" in message
    assert "'scores/one'" in message


def test_a_malformed_search_term_is_a_parse_error_naming_the_entry(
    indexed_repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    # A term the full-text engine cannot parse is rejected on the first
    # draw of the search, like the missing index; both belong to the
    # entry, not to a traceback.
    with pytest.raises(RunDefinitionError) as excinfo:
        parse_one_entry(
            {"resource_query": "scores/*", "search_term": "one AND"},
            indexed_repo, genome)

    message = str(excinfo.value)
    assert "binners[0]" in message
    assert "one AND" in message


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_search_term_is_no_search_term(
    repo: GenomicResourceRepo, genome: ReferenceGenome, blank: str,
) -> None:
    # What a shell substitutes for an unset variable; the repository
    # treats it as unset, so no index is demanded for it.
    run = parse_one_entry(
        {"resource_query": "scores/*", "search_term": blank}, repo, genome)

    assert [t.resource_id for t in run.tracks] == ["scores/one", "scores/two"]


def test_omitted_regions_mean_every_chromosome_in_genome_order(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    run = parse_one_entry({"resource_query": "scores/one"}, repo, genome)

    assert run.regions == [
        BedRegion("chr1", 1, CHR1_LENGTH),
        BedRegion("chr2", 1, CHR2_LENGTH),
    ]


def test_regions_are_parsed_in_gain_notation_and_kept_in_listed_order(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    # A bare chromosome name is the whole chromosome; a window keeps its
    # inclusive bounds; the listed order is the row order.
    config = {
        "bins": {"bin_size": 10, "regions": ["chr2", "chr1:11-35"]},
        "binners": [
            {"position_score_binner": {"resource_query": "scores/one"}},
        ],
    }

    run = parse_run_definition(config, repo, genome)

    assert run.regions == [
        BedRegion("chr2", 1, CHR2_LENGTH),
        BedRegion("chr1", 11, 35),
    ]


@pytest.mark.parametrize("notation", [
    "chr1:90-101",   # runs past the chromosome
    "chr1:101-110",  # lies wholly beyond it
    "chr2:0-10",     # starts before position 1
])
def test_a_region_outside_its_chromosome_is_a_parse_error_naming_it(
    repo: GenomicResourceRepo, genome: ReferenceGenome, notation: str,
) -> None:
    config = {
        "bins": {"bin_size": 10, "regions": ["chr1:1-10", notation]},
        "binners": [
            {"position_score_binner": {"resource_query": "scores/one"}},
        ],
    }

    with pytest.raises(RunDefinitionError) as excinfo:
        parse_run_definition(config, repo, genome)

    message = str(excinfo.value)
    assert "bins.regions[1]" in message
    assert notation in message
    assert str(genome.get_chrom_length(notation.split(":")[0])) in message


def test_a_window_ending_before_it_starts_is_a_parse_error_naming_it(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    config = {
        "bins": {"bin_size": 10, "regions": ["chr1:1-10", "chr2:20-10"]},
        "binners": [
            {"position_score_binner": {"resource_query": "scores/one"}},
        ],
    }

    with pytest.raises(RunDefinitionError) as excinfo:
        parse_run_definition(config, repo, genome)

    message = str(excinfo.value)
    assert "bins.regions[1]" in message
    assert "chr2:20-10" in message


@pytest.mark.parametrize("regions", [
    ["chr1:1-20", "chr2", "chr1:20-30"],   # windows sharing position 20
    ["chr1:1-20", "chr2", "chr1"],          # the bare chromosome covers it
    ["chr1:5-10", "chr2", "chr1:1-20"],     # one inside the other
])
def test_overlapping_regions_are_a_parse_error_naming_both(
    repo: GenomicResourceRepo, genome: ReferenceGenome, regions: list[str],
) -> None:
    config = {
        "bins": {"bin_size": 10, "regions": regions},
        "binners": [
            {"position_score_binner": {"resource_query": "scores/one"}},
        ],
    }

    with pytest.raises(RunDefinitionError) as excinfo:
        parse_run_definition(config, repo, genome)

    message = str(excinfo.value)
    assert "bins.regions[0]" in message
    assert "bins.regions[2]" in message
    # The quoted form: a bare 'chr1' is a substring of 'chr1:1-20', so
    # only the repr tells the two notations apart in the message.
    assert repr(regions[0]) in message
    assert repr(regions[2]) in message


def test_adjacent_regions_do_not_overlap(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    config = {
        "bins": {"bin_size": 10, "regions": ["chr1:11-20", "chr1:1-10"]},
        "binners": [
            {"position_score_binner": {"resource_query": "scores/one"}},
        ],
    }

    run = parse_run_definition(config, repo, genome)

    assert run.regions == [BedRegion("chr1", 11, 20), BedRegion("chr1", 1, 10)]


def test_an_unknown_binner_kind_is_a_parse_error_listing_the_known_kinds(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    # Kinds come from the ``gain.binning.binners`` entry-point group; the
    # message names what IS registered so a typo is a one-look fix.
    config = {
        "bins": {"bin_size": 10},
        "binners": [
            {"fragment_score_binner": {"resource_query": "scores/one"}},
        ],
    }

    with pytest.raises(RunDefinitionError) as excinfo:
        parse_run_definition(config, repo, genome)

    assert "binners[0]" in str(excinfo.value)
    assert "fragment_score_binner" in str(excinfo.value)
    assert "position_score_binner" in str(excinfo.value)


def test_a_resource_matched_by_two_entries_names_both_tracks_by_aggregator(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    # Two aggregators of one resource side by side (D10): every member of
    # the repeated group carries its aggregator, whichever entry came
    # first, while a resource matched once keeps its bare id.
    config = {
        "bins": {"bin_size": 10},
        "binners": [
            {"position_score_binner": {"resource_query": "scores/*"}},
            {"position_score_binner": {
                "resource_query": "scores/one", "aggregator": "min"}},
        ],
    }

    run = parse_run_definition(config, repo, genome)

    assert [(t.name, t.resource_id, t.aggregator) for t in run.tracks] == [
        ("scores/one:max", "scores/one", "max"),
        ("scores/two", "scores/two", "mean"),
        ("scores/one:min", "scores/one", "min"),
    ]


def test_two_entries_producing_one_track_are_a_parse_error_naming_both(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    # Same resource, same aggregator: the suffix cannot tell them apart,
    # and neither could a reader of the ``/tracks`` table.
    config = {
        "bins": {"bin_size": 10},
        "binners": [
            {"position_score_binner": {"resource_query": "scores/*"}},
            {"position_score_binner": {
                "resource_query": "scores/two", "aggregator": "mean",
                "none_value_replacement": 0.0}},
        ],
    }

    with pytest.raises(RunDefinitionError) as excinfo:
        parse_run_definition(config, repo, genome)

    message = str(excinfo.value)
    assert "scores/two:mean" in message
    assert "binners[0]" in message
    assert "binners[1]" in message


def test_search_term_on_a_repository_without_an_index_is_a_parse_error(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    # Only the index can answer a term; the toy GRR has none until it is
    # published.  Refused at parse time, naming the entry and the cause,
    # rather than surfacing the repository's own error mid-run.
    with pytest.raises(RunDefinitionError) as excinfo:
        parse_one_entry(
            {"resource_query": "scores/*", "search_term": "one"},
            repo, genome)

    message = str(excinfo.value)
    assert "binners[0]" in message
    assert "search_term" in message
    assert "index" in message


@pytest.mark.parametrize("entry,fragment", [
    ({"resource_query": "scores/*", "search_term": ["one"]}, "search_term"),
    # A typo must not silently bin with the default.
    ({"resource_query": "scores/*", "aggregtor": "min"}, "aggregtor"),
    ({}, "resource_query"),
    ({"resource_query": "[abc"}, "[abc"),
    ({"resource_query": "scores/one", "aggregator": "mediann"}, "mediann"),
    ({"resource_query": "scores/one", "none_value_replacement": "zero"},
     "none_value_replacement"),
])
def test_a_malformed_entry_is_a_parse_error_naming_what_is_wrong(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
    entry: dict[str, Any], fragment: str,
) -> None:
    with pytest.raises(RunDefinitionError) as excinfo:
        parse_one_entry(entry, repo, genome)

    assert "binners[0]" in str(excinfo.value)
    assert fragment in str(excinfo.value)


def test_a_resource_with_several_scores_is_a_parse_error_listing_them(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    # A track is one score of one resource; which of several the user
    # meant is not the tool's to guess.
    with pytest.raises(RunDefinitionError) as excinfo:
        parse_one_entry({"resource_query": "other/pair"}, repo, genome)

    message = str(excinfo.value)
    assert "binners[0]" in message
    assert "other/pair" in message
    assert "'p'" in message
    assert "'q'" in message
    assert "not yet supported" not in message


@pytest.mark.parametrize("entry,fragment", [
    ({"resource_query": "other/label"}, "'str'"),
    ({"resource_query": "scores/one", "aggregator": "join(,)"}, "join"),
    ({"resource_query": "scores/one", "aggregator": "list"}, "list"),
    ({"resource_query": "scores/one", "aggregator": "concat"}, "concat"),
])
def test_a_non_numeric_score_or_aggregator_is_a_parse_error(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
    entry: dict[str, Any], fragment: str,
) -> None:
    # /values is one float64 matrix (D11): a string-typed score, or an
    # aggregator that builds a string or a list, has no cell to go in.
    with pytest.raises(RunDefinitionError) as excinfo:
        parse_one_entry(entry, repo, genome)

    message = str(excinfo.value)
    assert "binners[0]" in message
    assert fragment in message
    assert "not yet supported" not in message


@pytest.mark.parametrize("bins,fragment", [
    ({"bin_size": 0}, "bin_size"),
    ({"bin_size": "10"}, "bin_size"),
    ({}, "bin_size"),
    ({"bin_size": 10, "regions": []}, "regions"),
    ({"bin_size": 10, "bin_sise": 10}, "bin_sise"),
])
def test_a_malformed_bins_block_is_a_parse_error_naming_the_key(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
    bins: dict[str, Any], fragment: str,
) -> None:
    config = {
        "bins": bins,
        "binners": [
            {"position_score_binner": {"resource_query": "scores/one"}},
        ],
    }

    with pytest.raises(RunDefinitionError) as excinfo:
        parse_run_definition(config, repo, genome)

    assert fragment in str(excinfo.value)


def test_an_unknown_top_level_key_is_a_parse_error(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    config = {
        "bins": {"bin_size": 10},
        "binner": [
            {"position_score_binner": {"resource_query": "scores/one"}},
        ],
    }

    with pytest.raises(RunDefinitionError) as excinfo:
        parse_run_definition(config, repo, genome)

    assert "binner" in str(excinfo.value)
