# pylint: disable=redefined-outer-name,C0114,C0116
"""What the position annotator answers now that the SCORE reduces (#1131).

The annotator no longer collects a region's records and hands them to the
base to fold.  It asks the logical plane for the region already reduced --
one ``PositionScoreAggregationQuery`` per attribute, built when the
pipeline loads -- and hands the base an ``AnnotatedValues``, keyed by
attribute NAME, which the base passes through untouched.

Five things follow that were not true of the old path, and they are what
this file pins:

- a source exposed twice with two aggregators is two answers, because the
  keys are attribute names and no longer collide;
- a region no record touches answers per aggregator rather than
  short-circuiting to ``None`` for every attribute at once;
- an attribute whose score has no default aggregator and names none is
  refused when the pipeline LOADS, not on the first region;
- a region starting below position 1 is refused rather than clipped and
  answered;
- where records overlap, a covered position counts once rather than once
  per record covering it.

The last two are consequences of reducing over the plane rather than
choices made here, but they are user-visible, so they are pinned where
someone changing this path will see them.
"""

import pathlib
import textwrap

import pytest
from gain.annotation.annotatable import Region, VCFAllele
from gain.annotation.annotation_config import AnnotationConfigurationError
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
    attribute may use either -- and the move to the plane must not lose
    it.  ``join`` is the aggregator that makes this visible, being the
    only parametrized one: the separator has to survive the trip.

    This is an end-to-end guard, and deliberately not the pin on
    ``aggregator_name``: ``Aggregator.build`` accepts a mapping as
    happily as a name, so passing the mapping straight through would
    still ANSWER correctly here.  What it would break is the query's
    ``str`` slot and the name ``resolve_aggregation_queries`` promises,
    which is a contract rather than a behaviour and is pinned as a unit
    in ``test_aggregators``.

    It also shows the weighting plainly, each record's value joined once
    per base pair it covers -- the same expansion the old weighted fold
    produced, since both hand the aggregator ``(value, weight)``.
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


def test_one_source_twice_with_two_aggregators_is_two_answers(
    repo: GenomicResourceRepo,
) -> None:
    """The fault the attribute-name key exists to fix.

    Both attributes read ``s``; only the aggregator differs.  The score
    fetches the column once and folds it twice, and the answers come back
    positionally, one per query -- so keying the result by SOURCE would
    have the second write over the first and both attributes report one
    of the two reductions.  Values chosen so max and min differ, because
    two answers that happened to coincide would pass either way.
    """
    with _pipeline(repo, """
        - source: s
          name: highest
          aggregator: max
        - source: s
          name: lowest
          aggregator: min
    """) as pipeline:
        result = pipeline.annotate(Region("chr1", 10, 29))

    assert result == {"highest": 2.0, "lowest": 1.0}


def test_a_substitution_answers_a_source_named_twice_under_both_names(
    tmp_path: pathlib.Path,
) -> None:
    """The point read answers once per ATTRIBUTE, under the attribute's
    own name -- a source named twice is asked twice and answered twice.

    Three attributes over two sources, with the doubled one first and
    last, so the pairing is visible: a read that collapsed the sources
    to two would leave the third attribute with nothing to pair with,
    and a pairing that reordered them would put ``t``'s value under a
    name on ``s``.  The two names on ``s`` both have to come back with
    what ``s`` holds at the position (gain#1134, gain#1111).

    Its own resource, with two float scores: the shared fixture's
    ``s == 1.0`` and ``flag == True`` compare equal in Python, so a swap
    between those two sources would answer identically and hide.
    """
    repo = (
        a_grr()
        .with_resource(
            "scores",
            a_position_score()
            .with_score("s", "float")
            .with_score("t", "float")
            .with_data("""
                chrom  pos_begin  s    t
                chr1   12         1.0  10.0
            """),
        )
        .build_repo(tmp_path)
    )
    with _pipeline(repo, """
        - source: s
          name: first
        - source: t
          name: tee
        - source: s
          name: second
    """) as pipeline:
        result = pipeline.annotate(VCFAllele("chr1", 12, "A", "C"))

    assert result == {"first": 1.0, "tee": 10.0, "second": 1.0}


def test_the_bool_fixtures_false_record_answers_false(
    repo: GenomicResourceRepo,
) -> None:
    """The fixture has carried a ``False`` row all along; now it is asserted.

    Four files in the suite write ``False`` into a bool column and not one
    of them asked what came back, which is exactly why gain#1192 -- a
    ``bool`` score parsing its cell with Python's ``bool``, so that every
    non-empty text answered ``True`` -- survived a green suite.  The datum
    was always here; only the assertion was missing.

    ``list`` rather than ``bool`` as the aggregator: ``BoolAggregator``
    answers whether ANY non-null value was accumulated, so it answers
    ``True`` for a region holding nothing but ``False`` and cannot see the
    regression this test exists for.
    """
    with _pipeline(repo, """
        - source: flag
          name: flags
          aggregator: list
    """) as pipeline:
        covered_true = pipeline.annotate(Region("chr1", 10, 19))
        covered_false = pipeline.annotate(Region("chr1", 20, 29))

    assert covered_true == {"flags": [True] * 10}
    assert covered_false == {"flags": [False] * 10}


def test_a_bool_attribute_naming_no_aggregator_is_refused_at_load(
    repo: GenomicResourceRepo,
) -> None:
    """Refused building the pipeline, not on the first region.

    ``bool`` is the one value type whose class default aggregator is
    deliberately ``None``, so it is the only way an attribute can reach
    the annotator with nothing to reduce it.  The old path answered such
    an attribute with the region's per-base expansion -- one value per
    base pair of a CNV, output nobody asked for.

    Nothing is annotated here: reaching the refusal without an annotatable
    is the whole point, and a pipeline that only failed once a region
    arrived would have shipped the misconfiguration.

    It arrives as an ``AnnotationConfigurationError`` naming the annotator,
    because the factory wraps what building one raises -- which is what
    makes this a configuration complaint about a pipeline the user wrote
    rather than a stray ``ValueError`` from the score layer.  The plane's
    own remedy survives the wrapping, and that is what the tail pins.
    """
    with pytest.raises(AnnotationConfigurationError) as excinfo:
        _pipeline(repo, """
            - source: flag
              name: flag
        """)

    assert str(excinfo.value).endswith(
        "score 'flag' of resource 'scores' has no default aggregator "
        "for value type 'bool'; name one on the query")


def test_a_bool_attribute_that_names_one_loads_and_answers(
    repo: GenomicResourceRepo,
) -> None:
    """The other side of that boundary: naming one is all it takes.

    Guards the refusal against over-reach -- were it refusing on the value
    type rather than on the missing default, this would fail too.
    """
    with _pipeline(repo, """
        - source: flag
          name: flag
          aggregator: bool
    """) as pipeline:
        result = pipeline.annotate(Region("chr1", 10, 19))

    assert result == {"flag": True}


def test_a_region_starting_below_one_is_refused_rather_than_answered(
    repo: GenomicResourceRepo,
) -> None:
    """The plane's span guard is allowed through (#1131).

    Positions are 1-based, so a region starting at 0 is malformed input.
    The read the annotator left did NOT decline it -- it clipped to the
    covered part and answered normally, so this fixture's region 0-10
    used to annotate as ``1.0``.  The read it moved onto refuses it
    instead, which is a real answer becoming an error and not merely a
    ``None`` becoming one.

    Worth pinning because it is reachable, not theoretical: ``Region``
    is happy to be constructed this way, and ``annotate_columns`` builds
    regions with a bare ``int()`` of the start column, so a 0-based or
    BED-derived input produces one.

    A REVERSED region is not this change's doing and is deliberately not
    pinned here: ``len()`` of it raises in the cutoff check, before the
    annotator gets as far as the score, on this revision and the one
    before it alike.
    """
    with _pipeline(repo, """
        - source: s
          name: s
          aggregator: mean
    """) as pipeline, pytest.raises(ValueError) as excinfo:
        pipeline.annotate(Region("chr1", 0, 10))

    assert str(excinfo.value) == (
        "genomic score <scores> asked for a region with start 0; "
        "positions are 1-based")


def test_overlapping_records_count_each_position_once(
    tmp_path: pathlib.Path,
) -> None:
    """A position covered twice counts once, to the first record (#1131).

    A position score promises one value per position, so records that
    overlap are malformed -- the statistics scan refuses such a resource
    and a point lookup already answered from the first record covering
    the position.  Region annotation used to be the exception: it folded
    every record at its full clipped width, so the five positions these
    share counted twice.

    Reducing over POSITIONS rather than over records settles that the
    same way the rest of the kind already had.  The two answers differ,
    which is why this is pinned rather than assumed: counting 10-19 and
    15-24 twice over their overlap gives 2.0, once gives 5/3.
    """
    repo = (
        a_grr()
        .with_resource(
            "scores",
            a_position_score()
            .with_score("s", "float", desc="a float score")
            .with_data("""
                chrom  pos_begin  pos_end  s
                chr1   10         19       1.0
                chr1   15         24       3.0
            """),
        )
        .build_repo(tmp_path)
    )

    with _pipeline(repo, """
        - source: s
          name: s
          aggregator: mean
    """) as pipeline:
        result = pipeline.annotate(Region("chr1", 10, 24))

    # 1.0 for 10-19 and 3.0 for 20-24: fifteen positions, each once.
    assert result["s"] == pytest.approx((1.0 * 10 + 3.0 * 5) / 15)
    assert result["s"] != pytest.approx(2.0)
