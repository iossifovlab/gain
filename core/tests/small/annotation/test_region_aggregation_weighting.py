# pylint: disable=redefined-outer-name,C0114,C0116,protected-access
"""How much each record weighs when a region is aggregated (#260).

A position-score record counts once per base pair of the region it
covers: aggregating a region costs one aggregator call per *record*, not
one per base pair, and the weight comes from the score layer's
already-clipped bounds.

Since #1131 the position score applies that rule ITSELF -- the annotator
asks the logical plane for a region already reduced -- so the position
cases below are pinned against the score's own answers rather than by
watching values arrive at an annotator-owned aggregator.  There is no
longer such an aggregator to watch: what would once have been a silent
change of weighting is now a disagreement between the annotator and the
score, which is the sharper thing to assert anyway.

An allele line and a fragment each count exactly once, however long they are.
Those two still reduce through the base, so they are still pinned by
watching the calls -- and they are what the weighted seam must *not*
change.
"""

import pathlib
import textwrap
from typing import Any

import pytest
from gain.annotation.annotatable import Region
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.annotation.annotation_pipeline import AnnotationPipeline
from gain.genomic_resources.genomic_scores import (
    build_position_score_from_resource,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing.builders import (
    a_fragment_score,
    a_grr,
    a_position_score,
    an_allele_score,
)


@pytest.fixture
def fixture_repo(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    return (
        a_grr()
        .with_resource(
            "position_score1",
            a_position_score()
            .with_score("test100way", "float", column_name="100way",
                        desc="test values")
            .with_data("""
                chrom  pos_begin  pos_end  100way
                chr1   10         19       1.0
                chr1   20         29       2.0
                chr1   30         39       3.0
            """),
        )
        .with_resource(
            "allele_score1",
            an_allele_score()
            .with_score("freq", "float", desc="test values")
            .with_data("""
                chrom  pos_begin  reference  alternative  freq
                chr1   10         A          C            0.1
                chr1   10         A          G            0.2
                chr1   11         C          A            0.3
            """),
        )
        .with_resource(
            "fragments",
            a_fragment_score()
            .with_score("frequency", "float",
                        desc="some population frequency")
            .with_data("""
                chrom  pos_begin  pos_end  frequency
                chr1   10         19       0.1
                chr1   20         200      0.2
            """),
        )
        .build_repo(tmp_path)
    )


def _pipeline(
    fixture_repo: GenomicResourceRepo, aggregator: str,
) -> AnnotationPipeline:
    return load_pipeline_from_yaml(textwrap.dedent(f"""
        - position_score:
            resource_id: position_score1
            attributes:
            - source: test100way
              name: test100
              aggregator: {aggregator}
        """), fixture_repo)


def _record_aggregator_calls(
    pipeline: AnnotationPipeline, calls: list[tuple[Any, int]],
) -> None:
    """Record every (value, weight) pair the annotator adds."""
    aggregator = pipeline.annotators[0].attributes[0].aggregator_instance
    assert aggregator is not None
    original_add = aggregator.add

    def spy(value: Any, count: int = 1) -> None:
        calls.append((value, count))
        original_add(value, count)

    aggregator.add = spy  # type: ignore[method-assign]


def _score_answer(
    fixture_repo: GenomicResourceRepo, pos: int, pos_end: int,
) -> Any:
    """What the score itself reduces that region to, by the same rule."""
    score = build_position_score_from_resource(
        fixture_repo.get_resource("position_score1"))
    with score.open() as opened:
        return opened.get_score_in_region_agg(
            "chr1", pos, pos_end, "test100way", "mean")


def test_each_record_counts_once_per_base_pair_it_covers(
    fixture_repo: GenomicResourceRepo,
) -> None:
    """Two records of equal width average to their midpoint.

    Ten bases of 1.0 and ten of 2.0.  Were a record counted once rather
    than once per base, this region would answer 1.5 as well -- which is
    why the clipped case below, where the two weights differ, is the one
    that can tell the rule apart.
    """
    with _pipeline(fixture_repo, "mean") as pipeline:
        result = pipeline.annotate(Region("chr1", 10, 29))

    assert result["test100"] == 1.5
    assert result["test100"] == _score_answer(fixture_repo, 10, 29)


def test_a_records_weight_is_clipped_to_the_annotatable(
    fixture_repo: GenomicResourceRepo,
) -> None:
    """Only the part of a record inside the annotatable counts.

    Five bases of 1.0 and three of 2.0, so the answer leans to 1.0 -- and
    not to 1.5, which is what counting each record once, or counting them
    at their full ten-base width, would give.
    """
    with _pipeline(fixture_repo, "mean") as pipeline:
        result = pipeline.annotate(Region("chr1", 15, 22))

    assert result["test100"] == pytest.approx((1.0 * 5 + 2.0 * 3) / 8)
    assert result["test100"] != pytest.approx(1.5)
    assert result["test100"] == _score_answer(fixture_repo, 15, 22)


def test_an_allele_line_counts_once_however_wide_the_region(
    fixture_repo: GenomicResourceRepo,
) -> None:
    calls: list[tuple[Any, int]] = []
    pipeline = load_pipeline_from_yaml(textwrap.dedent("""
        - allele_score:
            resource_id: allele_score1
            mode: region
            attributes:
            - source: freq
              name: freq
              aggregator: max
        """), fixture_repo)

    with pipeline:
        _record_aggregator_calls(pipeline, calls)
        result = pipeline.annotate(Region("chr1", 10, 11))

    assert calls == [(0.1, 1), (0.2, 1), (0.3, 1)]
    assert result["freq"] == 0.3


def test_a_fragment_counts_once_however_long_it_is(
    fixture_repo: GenomicResourceRepo,
) -> None:
    calls: list[tuple[Any, int]] = []
    pipeline = load_pipeline_from_yaml(textwrap.dedent("""
        - fragment_score:
            resource_id: fragments
            attributes:
            - source: frequency
              name: frequency
              aggregator: max
        """), fixture_repo)

    with pipeline:
        _record_aggregator_calls(pipeline, calls)
        result = pipeline.annotate(Region("chr1", 10, 200))

    assert calls == [(0.1, 1), (0.2, 1)]
    assert result["frequency"] == 0.2
