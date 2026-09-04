# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pytest
from gain.binning.binners import Track
from gain.binning.run_definition import (
    RunDefinitionError,
    parse_run_definition,
)
from gain.genomic_resources.reference_genome import ReferenceGenome
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.utils.regions import BedRegion

from .conftest import CHR1_LENGTH, CHR2_LENGTH


def test_an_exact_id_entry_becomes_one_track_named_by_the_resource(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    config = {
        "bins": {"bin_size": 10},
        "binners": [
            {"position_score_binner": {"resource_query": "scores/one"}},
        ],
    }

    run = parse_run_definition(config, repo, genome)

    assert run.tracks == [
        Track(
            name="scores/one", resource_id="scores/one", score_id="s",
            aggregator="max", none_value_replacement=None),
    ]


def test_a_glob_entry_expands_to_its_matches_sorted_by_resource_id(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    # The glob is a repository search: it must not reach the genome or the
    # score outside the ``scores/`` prefix, and the order is by id, not
    # whatever the repository yields.
    config = {
        "bins": {"bin_size": 10},
        "binners": [
            {"position_score_binner": {"resource_query": "scores/*"}},
        ],
    }

    run = parse_run_definition(config, repo, genome)

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
    config = {
        "bins": {"bin_size": 10},
        "binners": [
            {"position_score_binner": {
                "resource_query": "scores/*",
                "aggregator": "min",
                "none_value_replacement": 0.0,
            }},
        ],
    }

    run = parse_run_definition(config, repo, genome)

    assert [
        (t.name, t.aggregator, t.none_value_replacement) for t in run.tracks
    ] == [
        ("scores/one", "min", 0.0),
        ("scores/two", "min", 0.0),
    ]


def test_omitted_regions_mean_every_chromosome_in_genome_order(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    config = {
        "bins": {"bin_size": 10},
        "binners": [
            {"position_score_binner": {"resource_query": "scores/one"}},
        ],
    }

    run = parse_run_definition(config, repo, genome)

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


def test_a_resource_matched_by_two_entries_is_refused_as_not_yet_supported(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    # Two aggregators of one resource need the ``:<aggregator>`` name
    # suffixing of the validation slice (gain#1201).  Until then the run
    # is refused, never written with two columns of the same name.
    config = {
        "bins": {"bin_size": 10},
        "binners": [
            {"position_score_binner": {"resource_query": "scores/*"}},
            {"position_score_binner": {
                "resource_query": "scores/one", "aggregator": "min"}},
        ],
    }

    with pytest.raises(RunDefinitionError) as excinfo:
        parse_run_definition(config, repo, genome)

    assert "scores/one" in str(excinfo.value)
    assert "not yet supported" in str(excinfo.value)
