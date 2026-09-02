# pylint: disable=redefined-outer-name,C0114,C0116
"""What the fragment annotator answers now that the SCORE reduces (#1124).

The annotator no longer collects a region's fragments into a list per
source and hands them to the base to fold.  It asks the logical plane for
the region already reduced -- one ``ScoreAggregationQuery`` per aggregated
attribute, built when the pipeline loads -- and hands the base an
``AggregatedValues``, keyed by attribute NAME, which the base passes
through untouched.

The point of the move is memory: a region's fragments are folded as they
stream rather than materialised, so peak memory per annotatable no longer
grows with the number of fragments over it.  Everything a pipeline
ANSWERS is meant to be unchanged, which is why most of this file reads as
guards rather than as new behaviour -- they pass before the rewrite and
after it, and that is exactly their job.

Two of them guard things that are easy to break while rewriting and that
no other test states:

- an attribute with no aggregator answers the fragment COUNT, not
  ``None`` -- and that is not only the ``count`` attribute, because a
  ``bool`` score has no default aggregator either;
- a ``None`` annotatable and a region no fragment overlaps are different
  answers reached through different entry points: ``None`` short-circuits
  in ``Annotator.annotate`` and never reaches the score, while an empty
  region is a real walk that found nothing.
"""

import gc
import pathlib
import textwrap
import tracemalloc

import pytest
from gain.annotation.annotatable import Region
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.annotation.annotation_pipeline import AnnotationPipeline
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing.builders import (
    a_fragment_score,
    a_grr,
)


@pytest.fixture
def repo(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    """Three fragments over ``chr1``, and a ``bool`` score beside the float.

    Read against ``[100, 199]`` all three overlap, so the float reduces to
    ``3.0`` by ``max`` and ``1.0`` by ``min``.  Everything past 200 is
    uncovered, which is what lets one annotatable ask about a region no
    fragment touches without leaving the chromosome the resource has.

    ``flag`` is a ``bool``, whose default aggregator is deliberately
    ``None`` -- the one score type a fragment resource declares no
    reduction for.
    """
    return (
        a_grr()
        .with_resource(
            "fragments",
            a_fragment_score()
            .with_score("v", "float", desc="a float score")
            .with_score("flag", "bool", desc="a bool score")
            .with_data("""
                chrom  pos_begin  pos_end  v    flag
                chr1   50         149      3.0  True
                chr1   100        199      1.0  False
                chr1   150        159      2.0  True
            """),
        )
        .build_repo(tmp_path)
    )


def _pipeline(
    repo: GenomicResourceRepo, attributes: str,
) -> AnnotationPipeline:
    return load_pipeline_from_yaml(textwrap.dedent(f"""
        - fragment_score:
            resource_id: fragments
            attributes:
{textwrap.indent(textwrap.dedent(attributes), " " * 12)}
        """), repo)


def test_one_source_asked_twice_answers_twice(
    repo: GenomicResourceRepo,
) -> None:
    """Two aggregators over one source are two answers, keyed by NAME.

    The reason the annotator hands back attribute names rather than
    sources: a source-keyed mapping has one slot for what is now two
    reductions of one score.
    """
    with _pipeline(repo, """
        - source: v
          name: as_min
          aggregator: min
        - source: v
          name: as_max
          aggregator: max
    """) as pipeline:
        result = pipeline.annotate(Region("chr1", 100, 199))

    assert result == {"as_min": 1.0, "as_max": 3.0}


def test_an_attribute_with_no_aggregator_answers_the_fragment_count(
    repo: GenomicResourceRepo,
) -> None:
    """Not only ``count`` -- a ``bool`` score lands here too.

    ``FragmentScore.DEFAULT_AGGREGATORS["bool"]`` is ``None`` and the
    builder declares no aggregator, so ``flag`` has none either.  The
    annotator hands the fragment count to EVERY attribute without an
    aggregator, which makes ``flag`` answer ``3`` rather than ``None`` or
    a value of its own.

    That is long-standing behaviour and this pins it, because it is
    invisible from the config and a rewrite that reasoned only about the
    ``count`` attribute would quietly change it.
    """
    with _pipeline(repo, """
        - source: count
          name: count
        - source: flag
          name: flag
    """) as pipeline:
        result = pipeline.annotate(Region("chr1", 100, 199))

    assert result == {"count": 3, "flag": 3}


def test_a_none_annotatable_and_an_empty_region_are_different_answers(
    repo: GenomicResourceRepo,
) -> None:
    """``None`` for no annotatable; ``0`` for a region nothing overlaps.

    Reached through different entry points, which is why both are asserted
    here rather than one standing in for the other.  ``None`` never gets
    as far as the score: ``Annotator.annotate`` short-circuits to
    ``_empty_result``, whose values are ``None`` for every attribute.  An
    empty region is a real walk that saw no fragments, so its count comes
    off ``FragmentAggregate.count`` and is ``0``.
    """
    with _pipeline(repo, """
        - source: count
          name: count
        - source: v
          name: as_max
          aggregator: max
    """) as pipeline:
        for_nothing = pipeline.annotate(None)
        for_empty_region = pipeline.annotate(Region("chr1", 500, 600))

    assert for_nothing == {"count": None, "as_max": None}
    assert for_empty_region == {"count": 0, "as_max": None}


def _repo_of_overlapping_fragments(
    tmp_path: pathlib.Path, count: int,
) -> GenomicResourceRepo:
    """A resource whose ``count`` fragments all overlap ``[1, 10 ** 6]``.

    Begins ascend, which the fragment kind requires; spans are short and
    the queried region is wide, so every fragment is admitted and the walk
    length IS ``count``.
    """
    rows = "\n".join(
        f"chr1 {i * 4 + 1} {i * 4 + 50} {float(i % 7)}"
        for i in range(count))
    return (
        a_grr()
        .with_resource(
            "fragments",
            a_fragment_score()
            .with_score("v", "float")
            .with_data(f"chrom pos_begin pos_end v\n{rows}"),
        )
        .build_repo(tmp_path)
    )


def _peak_bytes_annotating(
    repo: GenomicResourceRepo, region: Region,
) -> int:
    """Peak bytes allocated by ONE annotate call over ``region``.

    The pipeline is loaded, opened and annotated once before measuring, so
    what is measured is a steady-state annotate rather than the one-off
    allocation of the pipeline, the score and the table behind it.
    """
    with _pipeline(repo, """
        - source: v
          name: as_max
          aggregator: max
    """) as pipeline:
        pipeline.annotate(region)

        gc.collect()
        tracemalloc.start()
        try:
            pipeline.annotate(region)
            return tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()


def test_peak_memory_does_not_grow_with_the_number_of_fragments(
    tmp_path: pathlib.Path,
) -> None:
    """The whole point of the folding read, measured.

    The old annotator appended every fragment's value to a list per
    source, so a region with ten times the fragments cost about ten times
    the peak.  Folding as the stream arrives keeps one accumulator per
    query whatever the walk length, so the two peaks land close together.

    ``max`` is deliberate: an aggregator that KEEPS what it is given --
    ``list``, ``value_count`` -- would still grow, and that is a property
    of the aggregator rather than of this read.

    The bound is loose on purpose.  This is a measurement, so it is stated
    as "nothing like linear" rather than as a tight constant: linear
    growth is ~10x here, and the assertion fails anything above 3x.

    Measured 2026-09-02: 8904 -> 66664 bytes before the folding read, and
    flat to within 1% after it.  The 3x bound is therefore an enormous
    margin against noise on a loaded machine, and still nowhere near the
    7.5x the old path actually cost.
    """
    region = Region("chr1", 1, 10 ** 6)
    small = _peak_bytes_annotating(
        _repo_of_overlapping_fragments(tmp_path / "small", 200), region)
    large = _peak_bytes_annotating(
        _repo_of_overlapping_fragments(tmp_path / "large", 2000), region)

    assert large < 3 * small, (
        f"peak grew from {small} to {large} bytes for 10x the fragments")
