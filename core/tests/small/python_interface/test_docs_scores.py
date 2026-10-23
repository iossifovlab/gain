"""Pin the offline claims of the "Genomic scores" page.

The tests run the code that the page describes and assert the results
that the page states. They read no ``.rst`` file. The page is
``docs/source/development/resources/scores.rst``. The examples of the page
use ``hg38/scores/phastCons100way``; the tests use mini-GRR scores of the
three kinds and of the three table backends instead. The tests pin these
claims:

- The generic builders return a ``PositionScore``, an ``AlleleScore`` or a
  ``FragmentScore``, by the type of the resource.
- A score is initially closed, ``open()`` returns the score, a ``with``
  block closes it, and a read of a closed score raises ``ValueError:
  genomic score <id> is not open``. ``fetch_records`` raises the same
  message at the first ``next()``, not at the call.
- ``score_definitions`` and ``get_score_definition`` give
  ``GenomicScoreDef`` objects, which convert one value or an array.
- ``get_all_chromosomes`` and ``has_chromosome`` report the contigs.
- The chromosome lengths come from a tabix estimate, a bigWig header or the
  extent of an in-memory table. A request for an unavailable source, for a
  closed score or for an absent contig raises ``ValueError``. The lengths
  from a repaired reference genome are pinned by
  ``tests/small/genomic_resources/test_score_chrom_lengths_api.py``.
- ``fetch_records`` yields records, ``supports_region_value_arrays``
  depends on the backend, and ``compile_filter`` gives a ``ScoreFilter``
  that only the score which compiled it accepts.
- ``fetch_region_segments_scores`` yields each record at its own extent.
  The "Overview and examples" page states this too.
- The aggregates keep the values in the order of the queries. An
  ``AlleleAggregate`` holds the allele keys when the call asks for them,
  and a ``FragmentAggregate`` holds the number of fragments.
- The score id can be left out of the histogram methods of a resource with
  one score, and a resource with several scores refuses it.
"""
import types
from typing import Any

import numpy as np
import pytest
from gain import grr
from gain.genomic_resources.aggregators import ScoreAggregationQuery
from gain.genomic_resources.genomic_position_table import ChromLengthSource
from gain.genomic_resources.genomic_scores import (
    AlleleAggregate,
    AlleleScore,
    FragmentAggregate,
    FragmentScore,
    GenomicScore,
    PositionScore,
)
from gain.genomic_resources.histogram import NumberHistogram
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.score_def import GenomicScoreDef
from gain.genomic_resources.score_filter import ScoreFilter, ScoreFilterError

TABIX_SCORE = "mini_positionscore_bedgraph_tabix_0"
TABIX_SCORE_ID = "pos_bedgraph_0"
BIGWIG_SCORE = "mini_positionscore_bw"
IN_MEMORY_SCORE = "mini_positionscore_tsv_0"
TWO_SCORES = "mini_positionscore_tsv_1_twoscores"
ALLELE_SCORE = "mini_allelescore_tabix_0"
FRAGMENT_SCORE = "mini_fragmentscore"


def _open(repo: GenomicResourceRepo, resource_id: str) -> Any:
    return grr.build_score_from_resource_id(resource_id, repo).open()


@pytest.mark.parametrize(("resource_id", "kind"), [
    (TABIX_SCORE, PositionScore),
    (ALLELE_SCORE, AlleleScore),
    (FRAGMENT_SCORE, FragmentScore),
])
def test_generic_builders_return_the_kind_of_the_resource(
    repo: GenomicResourceRepo, resource_id: str, kind: type,
) -> None:
    """Both generic builders pick the class from the resource definition."""
    from_id = grr.build_score_from_resource_id(resource_id, repo)
    from_resource = grr.build_score_from_resource(
        repo.get_resource(resource_id))

    # The exact class, on purpose: isinstance would accept the base.
    # pylint: disable=unidiomatic-typecheck
    assert type(from_id) is kind
    assert type(from_resource) is kind
    assert isinstance(from_id, GenomicScore)


def test_score_is_initially_closed(repo: GenomicResourceRepo) -> None:
    """A newly built score is closed."""
    score = grr.build_score_from_resource_id(TABIX_SCORE, repo)

    assert not score.is_open()


def test_open_returns_the_score(repo: GenomicResourceRepo) -> None:
    """``open()`` returns the score, so it chains to a builder."""
    score = grr.build_score_from_resource_id(TABIX_SCORE, repo)

    result = score.open()

    assert result is score
    assert score.is_open()
    score.close()
    assert not score.is_open()


def test_with_block_closes_the_score(repo: GenomicResourceRepo) -> None:
    """A ``with`` block closes the score that ``open()`` opened."""
    with grr.build_position_score_from_resource_id(
            TABIX_SCORE, repo).open() as score:
        assert score.get_scores_at_position("chr1", 3) == (0.2,)

    assert not score.is_open()


@pytest.mark.parametrize(("resource_id", "method", "args"), [
    (TABIX_SCORE, "get_scores_at_position", ("chr1", 3)),
    (ALLELE_SCORE, "get_allele_scores_for_allele", ("chr1", 1, "T", "A")),
    (FRAGMENT_SCORE, "get_fragment_scores_at_position", ("chr1", 4)),
])
def test_read_of_a_closed_score_raises(
    repo: GenomicResourceRepo, resource_id: str, method: str,
    args: tuple,
) -> None:
    """Each kind refuses a read before ``open()``, in the page's words."""
    score = grr.build_score_from_resource_id(resource_id, repo)

    with pytest.raises(
            ValueError,
            match=f"genomic score <{resource_id}> is not open"):
        getattr(score, method)(*args)


def test_fetch_records_of_a_closed_score_raises_the_same_message(
    repo: GenomicResourceRepo,
) -> None:
    """``fetch_records`` is a read too, so it refuses in the same words."""
    score = grr.build_score_from_resource_id(TABIX_SCORE, repo)

    with pytest.raises(
            ValueError,
            match=f"genomic score <{TABIX_SCORE}> is not open"):
        list(score.fetch_records("chr1", 1, 3))


def test_fetch_records_of_a_closed_score_refuses_at_the_first_next(
    repo: GenomicResourceRepo,
) -> None:
    """The call returns a generator, and the first ``next()`` refuses."""
    score = grr.build_score_from_resource_id(TABIX_SCORE, repo)
    records = score.fetch_records("chr1", 1, 3)

    with pytest.raises(
            ValueError,
            match=f"genomic score <{TABIX_SCORE}> is not open"):
        next(records)


def test_score_definitions(repo: GenomicResourceRepo) -> None:
    """The page's loop reads ``score_id`` and ``value_type``."""
    score = grr.build_score_from_resource_id(TWO_SCORES, repo)

    definitions = score.score_definitions

    assert list(definitions) == ["pos_tsv_1", "pos_tsv_1_st"]
    assert [
        (d.score_id, d.value_type) for d in definitions.values()
    ] == [("pos_tsv_1", "float"), ("pos_tsv_1_st", "str")]
    one = score.get_score_definition("pos_tsv_1")
    assert isinstance(one, GenomicScoreDef)
    assert one is definitions["pos_tsv_1"]


def test_score_definition_parses_a_value_and_an_array(
    repo: GenomicResourceRepo,
) -> None:
    """``parse_value`` and ``parse_array`` convert to the declared type."""
    score_def = grr.build_score_from_resource_id(
        TABIX_SCORE, repo).get_score_definition(TABIX_SCORE_ID)
    assert score_def is not None

    assert score_def.parse_value("0.5") == 0.5
    parsed = score_def.parse_array(np.array(["0.5", "1"]))
    assert parsed.tolist() == [0.5, 1.0]


def test_chromosomes_of_the_score(repo: GenomicResourceRepo) -> None:
    """The contigs come in the order of the table."""
    with _open(repo, TABIX_SCORE) as score:
        assert score.get_all_chromosomes() == ["chr1", "chr2"]
        assert score.has_chromosome("chr1")
        assert not score.has_chromosome("chrZ")


@pytest.mark.parametrize(("resource_id", "source"), [
    (TABIX_SCORE, ChromLengthSource.TABIX_ESTIMATE),
    (BIGWIG_SCORE, ChromLengthSource.BIGWIG),
    (IN_MEMORY_SCORE, ChromLengthSource.TABLE_EXTENT),
])
def test_best_available_length_source_is_selected(
    repo: GenomicResourceRepo, resource_id: str,
    source: ChromLengthSource,
) -> None:
    """Each backend answers with its own source, and says which it was."""
    with _open(repo, resource_id) as score:
        assert score.chrom_length_sources == [source]
        assert score.get_chrom_length_source("chr1") is source
        assert score.get_chrom_length("chr1", source=source.value) == \
            score.get_chrom_length("chr1")


def test_tabix_estimate_is_an_upper_bound(
    repo: GenomicResourceRepo,
) -> None:
    """The estimate exceeds the 10 positions of the mini genome."""
    with _open(repo, TABIX_SCORE) as score:
        lengths = score.get_all_chrom_lengths(source="tabix_estimate")

    assert lengths.keys() == {"chr1", "chr2"}
    assert all(length >= 10 for length in lengths.values())


def test_bigwig_header_gives_the_chromosome_length(
    repo: GenomicResourceRepo,
) -> None:
    """The bigWig header describes the whole chromosome."""
    with _open(repo, BIGWIG_SCORE) as score:
        assert score.get_all_chrom_lengths() == {"chr1": 10, "chr2": 10}


def test_unavailable_reference_genome_source_raises(
    repo: GenomicResourceRepo,
) -> None:
    """A score without stored genome lengths refuses that source."""
    with _open(repo, TABIX_SCORE) as score, \
            pytest.raises(ValueError, match="repair the resource"):
        score.get_all_chrom_lengths(source="reference_genome")


def test_length_of_an_absent_contig_raises(
    repo: GenomicResourceRepo,
) -> None:
    """A contig that the score does not hold has no length."""
    with _open(repo, TABIX_SCORE) as score, pytest.raises(ValueError):
        score.get_chrom_length("chrZ")


def test_source_without_a_length_for_the_contig_raises(
    repo: GenomicResourceRepo,
) -> None:
    """A bigWig score has no tabix estimate."""
    with _open(repo, BIGWIG_SCORE) as score, \
            pytest.raises(ValueError, match="no length for chr1"):
        score.get_chrom_length("chr1", source="tabix_estimate")


def test_length_of_a_closed_score_raises(repo: GenomicResourceRepo) -> None:
    """A closed score answers no chromosome length."""
    score = grr.build_score_from_resource_id(TABIX_SCORE, repo)

    with pytest.raises(ValueError, match="is not open"):
        score.get_chrom_length("chr1")


def test_fetch_records_yields_the_records_of_a_region(
    repo: GenomicResourceRepo,
) -> None:
    """``fetch_records`` yields one record for each row in the region."""
    with _open(repo, TABIX_SCORE) as score:
        records = score.fetch_records("chr1", 1, 4)

        assert isinstance(records, types.GeneratorType)
        assert [(r[0], r[1], r[2]) for r in records] == [
            ("chr1", 1, 1), ("chr1", 2, 2), ("chr1", 3, 3), ("chr1", 4, 4)]


@pytest.mark.parametrize(("resource_id", "supported"), [
    (TABIX_SCORE, True),
    (IN_MEMORY_SCORE, False),
])
def test_region_value_arrays_support_depends_on_the_backend(
    repo: GenomicResourceRepo, resource_id: str, supported: bool,
) -> None:
    """A tabix table supports the arrays, an in-memory table does not."""
    with _open(repo, resource_id) as score:
        score_ids = list(score.score_definitions)
        assert score.supports_region_value_arrays(score_ids) is supported


def test_region_value_arrays_read_whole_columns(
    repo: GenomicResourceRepo,
) -> None:
    """The arrays hold the values of many rows in one object."""
    with _open(repo, TABIX_SCORE) as score:
        batches = list(score.fetch_region_value_arrays(
            "chr1", 1, 4, [TABIX_SCORE_ID]))

    values = np.concatenate([batch[2][TABIX_SCORE_ID] for batch in batches])
    assert values.tolist() == pytest.approx([0.0, 0.1, 0.2, 0.3])


def test_compiled_filter_selects_records_as_they_are_read(
    repo: GenomicResourceRepo,
) -> None:
    """The page's ``score > threshold`` filter keeps the higher values."""
    with _open(repo, TABIX_SCORE) as score:
        keep = score.compile_filter(f"{TABIX_SCORE_ID} > 0.25")
        records = list(score.fetch_records(
            "chr1", 1, 6, score_filter=keep))

    assert isinstance(keep, ScoreFilter)
    assert [r[1] for r in records] == [4, 5, 6]


def test_filter_of_one_score_object_is_refused_by_another(
    repo: GenomicResourceRepo,
) -> None:
    """A filter belongs to the score object that compiled it."""
    with _open(repo, TABIX_SCORE) as first, \
            _open(repo, TABIX_SCORE) as second:
        keep = first.compile_filter(f"{TABIX_SCORE_ID} > 0.25")

        with pytest.raises(ScoreFilterError):
            list(second.fetch_records("chr1", 1, 6, score_filter=keep))


def test_segments_come_back_at_the_extent_of_their_record(
    repo: GenomicResourceRepo,
) -> None:
    """A record that straddles the region ends comes back whole."""
    with _open(repo, BIGWIG_SCORE) as score:
        segments = list(score.fetch_region_segments_scores("chr1", 3, 7))

    assert segments == [(1, 5, [0.0]), (6, 10, [pytest.approx(0.1)])]


def test_allele_aggregate_keeps_the_query_order(
    repo: GenomicResourceRepo,
) -> None:
    """One score with two aggregators gives two values, in query order."""
    with _open(repo, ALLELE_SCORE) as score:
        aggregate = score.get_allele_scores_in_region_agg(
            "chr1", 1, 10, queries=[
                ScoreAggregationQuery("allele_tsv_0_num", "max"),
                ScoreAggregationQuery("allele_tsv_0_num", "min"),
            ])

    assert isinstance(aggregate, AlleleAggregate)
    assert aggregate.values == (1.2, 1.0)
    assert aggregate.allele_keys is None


def test_allele_aggregate_holds_the_keys_when_asked(
    repo: GenomicResourceRepo,
) -> None:
    """``allele_keys=[]`` asks the aggregate for the matched allele keys."""
    with _open(repo, ALLELE_SCORE) as score:
        aggregate = score.get_allele_scores_in_region_agg(
            "chr1", 1, 10, queries=[
                ScoreAggregationQuery("allele_tsv_0_num", "max")],
            allele_keys=[])

    assert aggregate is not None
    assert aggregate.allele_keys == (
        "chr1:1:T:A", "chr1:1:T:C", "chr1:1:T:G")


def test_fragment_aggregate_holds_the_fragment_count(
    repo: GenomicResourceRepo,
) -> None:
    """A ``FragmentAggregate`` counts the fragments it reduced."""
    with _open(repo, FRAGMENT_SCORE) as score:
        aggregate = score.get_fragment_scores_overlapping_region_agg(
            "chr1", 1, 20, queries=[
                ScoreAggregationQuery("cnv_name", "list"),
                ScoreAggregationQuery("cnv_name", "count"),
            ])

    assert isinstance(aggregate, FragmentAggregate)
    assert aggregate.count == 1
    assert list(aggregate.values) == [["Chr1_duplication"], 1]


def test_single_score_id_can_be_left_out(repo: GenomicResourceRepo) -> None:
    """A resource with one score answers the histogram methods without it."""
    score = grr.build_score_from_resource_id(TABIX_SCORE, repo)

    assert isinstance(score.get_score_histogram(), NumberHistogram)
    assert score.get_score_histogram().to_dict() == \
        score.get_score_histogram(TABIX_SCORE_ID).to_dict()
    assert score.get_histogram_config() == \
        score.get_histogram_config(TABIX_SCORE_ID)
    assert score.get_score_range() == score.get_score_range(TABIX_SCORE_ID)
    assert score.get_histogram_image_url() == \
        score.get_histogram_image_url(TABIX_SCORE_ID)
    assert score.get_histogram_image_public_url() == \
        score.get_histogram_image_public_url(TABIX_SCORE_ID)


@pytest.mark.parametrize("method", [
    "get_score_histogram",
    "get_histogram_config",
    "get_score_range",
    "get_histogram_image_url",
    "get_histogram_image_public_url",
])
def test_several_scores_need_the_score_id(
    repo: GenomicResourceRepo, method: str,
) -> None:
    """A resource with two scores refuses a call without a score id."""
    score = grr.build_score_from_resource_id(TWO_SCORES, repo)

    with pytest.raises(ValueError, match="pos_tsv_1_st"):
        getattr(score, method)()
