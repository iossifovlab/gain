"""Pin the offline claims of the "Fragment scores" page.

The tests run the code that the page describes and assert the results
that the page states. They read no ``.rst`` file. The page is
``docs/source/development/resources/fragment_scores.rst``. The examples of
the page use ``hg38/scores/my_CNVcollection``; the tests use the mini-GRR
fragment score ``mini_fragmentscore`` instead, with one fragment on
``chr1`` from position 4 to 15. Adjacent windows need a fragment that
straddles a boundary, so those tests build one with ``a_fragment_score``.
The tests pin these claims:

- Both builders give a closed ``FragmentScore``, and the generic builder
  gives one too.
- The deprecated type name ``cnv_collection`` still builds a
  ``FragmentScore`` and logs a deprecation notice.
- A fragment comes back as ``(begin, end, values)``. The overlapping,
  covering and starting reads select the fragments by their location.
- The plural reads take ``scores`` and the singular reads take ``score``.
  The location is positional and the rest is keyword-only.
- A fragment that straddles two adjacent windows overlaps both, and starts
  in one of them.
- The overlap fractions restrict the overlapping read.
- The aggregation returns a ``FragmentAggregate`` with a value for each
  query and the number of fragments.
- ``get_scores_in_bins`` gives one row of aggregates for each bin.
"""
import inspect
import logging
import pathlib

import pytest
from gain import grr
from gain.genomic_resources.aggregators import ScoreAggregationQuery
from gain.genomic_resources.genomic_scores import (
    FragmentAggregate,
    FragmentScore,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing.builders import a_fragment_score, a_grr

FRAGMENT_SCORE = "mini_fragmentscore"
FRAGMENT = (4, 15, ("Duplication", "Chr1_duplication"))

_STRADDLING = """
    chrom  pos_begin  pos_end  score
    chr1   5          15       0.5
    chr1   12         18       0.7
"""


def _open_mini(repo: GenomicResourceRepo) -> FragmentScore:
    return grr.build_fragment_score_from_resource_id(
        FRAGMENT_SCORE, repo).open()


def _open_straddling(root: pathlib.Path) -> FragmentScore:
    repo = a_grr().with_resource(
        "fragments",
        a_fragment_score().with_score("score", "float")
        .with_data(_STRADDLING),
    ).build_repo(root)
    return grr.build_fragment_score_from_resource_id(
        "fragments", repo).open()


def test_builders_give_a_closed_fragment_score(
    repo: GenomicResourceRepo,
) -> None:
    """The specific and the generic builders give a ``FragmentScore``."""
    from_id = grr.build_fragment_score_from_resource_id(FRAGMENT_SCORE, repo)
    from_resource = grr.build_fragment_score_from_resource(
        repo.get_resource(FRAGMENT_SCORE))
    generic = grr.build_score_from_resource_id(FRAGMENT_SCORE, repo)

    for score in (from_id, from_resource, generic):
        assert isinstance(score, FragmentScore)
        assert not score.is_open()


@pytest.mark.legacy_vocabulary
def test_cnv_collection_is_accepted_with_a_deprecation_notice(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """The older type name still builds a ``FragmentScore``."""
    repo = a_grr().with_resource(
        "legacy", a_fragment_score().with_resource_type("cnv_collection"),
    ).build_repo(tmp_path)

    with caplog.at_level(logging.WARNING):
        score = grr.build_score_from_resource_id("legacy", repo)

    assert isinstance(score, FragmentScore)
    assert "cnv_collection" in caplog.text
    assert "deprecated" in caplog.text


def test_overlapping_read_of_the_page_example(
    repo: GenomicResourceRepo,
) -> None:
    """The ``with`` example prints ``begin, end, values`` per fragment."""
    with _open_mini(repo) as score:
        result = list(score.get_fragment_scores_overlapping_region(
            "chr1", 1, 5))

    assert result == [FRAGMENT]


def test_covering_read_selects_fragments_that_hold_the_position(
    repo: GenomicResourceRepo,
) -> None:
    """Position 4 is in the fragment and position 3 is not."""
    with _open_mini(repo) as score:
        assert list(score.get_fragment_scores_at_position(
            "chr1", 4)) == [FRAGMENT]
        assert not list(score.get_fragment_scores_at_position("chr1", 3))


def test_starting_read_selects_fragments_by_their_start(
    repo: GenomicResourceRepo,
) -> None:
    """A fragment that starts in the region and runs past it is selected."""
    with _open_mini(repo) as score:
        assert list(score.get_fragment_scores_starting_in_region(
            "chr1", 1, 4)) == [FRAGMENT]
        assert not list(score.get_fragment_scores_starting_in_region(
            "chr1", 5, 20))


def test_singular_reads_take_one_score(repo: GenomicResourceRepo) -> None:
    """``score=`` gives the value of one score for each fragment."""
    with _open_mini(repo) as score:
        assert list(score.get_fragment_score_overlapping_region(
            "chr1", 1, 5, score="cnv_name")) == [
                (4, 15, "Chr1_duplication")]
        assert list(score.get_fragment_scores_overlapping_region(
            "chr1", 1, 5, scores=["cnv_name"])) == [
                (4, 15, ("Chr1_duplication",))]


@pytest.mark.parametrize("method", [
    "get_fragment_scores_overlapping_region",
    "get_fragment_scores_at_position",
    "get_fragment_scores_starting_in_region",
    "get_fragment_scores_overlapping_region_agg",
])
def test_location_is_positional_and_the_rest_keyword_only(
    method: str,
) -> None:
    """``score_filter`` and the other options go by keyword."""
    parameters = list(
        inspect.signature(getattr(FragmentScore, method)).parameters.values())

    keyword_only = [p.name for p in parameters if p.kind is p.KEYWORD_ONLY]
    positional = [p.name for p in parameters[1:]
                  if p.kind is p.POSITIONAL_OR_KEYWORD]
    assert positional[0] == "chrom"
    assert "score_filter" in keyword_only


def test_straddling_fragment_overlaps_both_windows_and_starts_in_one(
    tmp_path: pathlib.Path,
) -> None:
    """Adjacent windows 1-10 and 11-20 share the first fragment."""
    with _open_straddling(tmp_path) as score:
        overlap_1 = list(score.get_fragment_scores_overlapping_region(
            "chr1", 1, 10))
        overlap_2 = list(score.get_fragment_scores_overlapping_region(
            "chr1", 11, 20))
        start_1 = list(score.get_fragment_scores_starting_in_region(
            "chr1", 1, 10))
        start_2 = list(score.get_fragment_scores_starting_in_region(
            "chr1", 11, 20))

    assert [(b, e) for b, e, _ in overlap_1] == [(5, 15)]
    assert [(b, e) for b, e, _ in overlap_2] == [(5, 15), (12, 18)]
    assert [(b, e) for b, e, _ in start_1] == [(5, 15)]
    assert [(b, e) for b, e, _ in start_2] == [(12, 18)]


def test_overlap_fractions_restrict_the_overlapping_read(
    tmp_path: pathlib.Path,
) -> None:
    """A fragment must cover enough of the region, or of itself."""
    with _open_straddling(tmp_path) as score:
        region = list(score.get_fragment_scores_overlapping_region(
            "chr1", 11, 20, min_region_overlap_fraction=0.6))
        fragment = list(score.get_fragment_scores_overlapping_region(
            "chr1", 11, 20, min_fragment_overlap_fraction=0.6))

    assert [(b, e) for b, e, _ in region] == [(12, 18)]
    assert [(b, e) for b, e, _ in fragment] == [(12, 18)]


def test_aggregation_counts_the_fragments(
    tmp_path: pathlib.Path,
) -> None:
    """A value for each query, and the number of fragments seen."""
    with _open_straddling(tmp_path) as score:
        plural = score.get_fragment_scores_overlapping_region_agg(
            "chr1", 11, 20, queries=[
                ScoreAggregationQuery("score", "max"),
                ScoreAggregationQuery("score", "min"),
            ])
        singular = score.get_fragment_score_overlapping_region_agg(
            "chr1", 11, 20, score="score", aggregator="max")

    assert isinstance(plural, FragmentAggregate)
    assert plural.count == 2
    assert plural.values == (0.7, 0.5)
    assert singular.count == 2
    assert singular.values == (0.7,)


def test_bins_give_one_row_of_aggregates_per_bin(
    tmp_path: pathlib.Path,
) -> None:
    """Two bins of 10 give two rows, one column per query."""
    with _open_straddling(tmp_path) as score:
        bins = score.get_scores_in_bins(
            "chr1", 1, 20, 10, [ScoreAggregationQuery("score", "max")])

    assert bins.shape == (2, 1)
    assert bins[:, 0].tolist() == pytest.approx([0.5, 0.7])
