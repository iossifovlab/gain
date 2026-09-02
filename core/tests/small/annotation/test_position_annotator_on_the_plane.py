# pylint: disable=redefined-outer-name,C0114,C0116
"""What the position annotator answers now that the SCORE reduces (#1131).

The annotator no longer collects a region's records and hands them to the
base to fold.  It asks the logical plane for the region already reduced --
one ``PositionScoreAggregationQuery`` per attribute, built when the
pipeline loads -- and hands the base an ``AggregatedValues``, keyed by
attribute NAME, which the base passes through untouched.

Three things follow that were not true of the old path, and they are what
this file pins:

- a source exposed twice with two aggregators is two answers, because the
  keys are attribute names and no longer collide;
- a region no record touches answers per aggregator rather than
  short-circuiting to ``None`` for every attribute at once;
- an attribute whose score has no default aggregator and names none is
  refused when the pipeline LOADS, not on the first region.
"""

import pathlib
import textwrap

import pytest
from gain.annotation.annotatable import Region
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing.builders import (
    a_grr,
    a_position_score,
)


@pytest.fixture
def repo(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    """A float score and a ``bool`` score, both only near the start of chr1.

    Everything past position 30 is uncovered, which is what lets one
    annotatable ask about a region no record touches without leaving the
    chromosome the annotator guards on.
    """
    return (
        a_grr()
        .with_resource(
            "scores",
            a_position_score()
            .with_score("s", "float", desc="a float score")
            .with_score("flag", "bool", desc="a bool score")
            .with_data("""
                chrom  pos_begin  pos_end  s    flag
                chr1   10         19       1.0  True
                chr1   20         29       2.0  False
            """),
        )
        .build_repo(tmp_path)
    )


def _pipeline(
    repo: GenomicResourceRepo, attributes: str,
) -> object:
    return load_pipeline_from_yaml(textwrap.dedent(f"""
        - position_score:
            resource_id: scores
            attributes:
{textwrap.indent(textwrap.dedent(attributes), " " * 12)}
        """), repo)


def test_an_untouched_region_answers_per_aggregator(
    repo: GenomicResourceRepo,
) -> None:
    """Each aggregator's own empty answer, not one ``None`` for all of them.

    The old annotator asked whether ANY record had been collected and
    returned an empty result if none had, so every attribute got ``None``
    however it was to be reduced.  The score answers for itself now, and an
    aggregator that has accumulated nothing still has an answer: an empty
    ``list`` is ``[]``, an empty ``value_count`` is ``{}``, and ``bool``
    over nothing is ``False``.  Only the aggregators with no empty answer
    to give -- the numeric ones, and the string ones that join -- still say
    ``None``.

    All four read the same uncovered region of a chromosome the resource
    does have, so what differs between them is the aggregator alone.
    """
    with _pipeline(repo, """
        - source: s
          name: as_mean
          aggregator: mean
        - source: s
          name: as_list
          aggregator: list
        - source: s
          name: as_value_count
          aggregator: value_count
        - source: s
          name: as_bool
          aggregator: bool
    """) as pipeline:
        result = pipeline.annotate(Region("chr1", 100, 110))

    assert result == {
        "as_mean": None,
        "as_list": [],
        "as_value_count": {},
        "as_bool": False,
    }


def test_an_attribute_may_spell_its_aggregator_as_a_mapping(
    repo: GenomicResourceRepo,
) -> None:
    """A pipeline may write an aggregator as a mapping, not just a name.

    ``{aggregator_type: ..., parameters: [...]}`` is the annotation
    pipeline's own spelling -- the resource level is string-only, but an
    attribute may use either.  The query the annotator hands the plane
    carries an aggregator NAME, so the mapping has to be reduced to its
    canonical string on the way in; left as a mapping it would travel a
    slot typed ``str`` all the way to ``Aggregator.build``.

    ``join`` is the aggregator that makes this visible, being the only
    parametrized one: the separator has to survive the trip.  It also
    shows the weighting plainly, each record's value joined once per base
    pair it covers -- the same expansion the old weighted fold produced,
    since both hand the aggregator ``(value, weight)``.
    """
    with _pipeline(repo, """
        - source: s
          name: joined
          aggregator:
            aggregator_type: join
            parameters: ["|"]
    """) as pipeline:
        result = pipeline.annotate(Region("chr1", 10, 29))

    assert result["joined"] == "|".join(["1.0"] * 10 + ["2.0"] * 10)
