# pylint: disable=redefined-outer-name,C0114,C0116
"""``none_value_replacement`` as a position-score attribute parameter (#1135).

The logical plane has always been able to make an uncovered position count:
a ``PositionScoreAggregationQuery`` carries a ``none_value_replacement``
that stands in for every null the per-position expansion holds -- uncovered
and covered-but-NA alike -- before the aggregator sees it.  Until now only
Python could say so; the annotation config could not, and the annotator
passed ``None`` (decision A13 of the annotator-seam design).

This file pins the parameter's arrival on the attribute, at the seam a user
actually configures: a pipeline built from YAML, annotating an annotatable.
"""

import pathlib
import textwrap

import pytest
from gain.annotation.annotatable import Region, VCFAllele
from gain.annotation.annotation_config import AnnotationConfigurationError
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.annotation.annotation_pipeline import AnnotationPipeline
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing import build_inmemory_test_repository
from gain.genomic_resources.testing.builders import (
    a_grr,
    a_position_score,
)


@pytest.fixture
def repo(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    """A float score covering only chr1:10-29.

    Everything from position 30 on is uncovered, so a region can hold a gap
    -- or be nothing but gap -- without leaving the chromosome the
    annotator guards on.
    """
    return (
        a_grr()
        .with_resource(
            "scores",
            a_position_score()
            .with_score("s", "float", desc="a float score")
            .with_data("""
                chrom  pos_begin  pos_end  s
                chr1   10         19       1.0
                chr1   20         29       2.0
            """),
        )
        .build_repo(tmp_path)
    )


def _pipeline_over(
    repo: GenomicResourceRepo, body: str,
) -> AnnotationPipeline:
    """A one-annotator pipeline over ``scores``, with ``body`` beneath it.

    Takes the whole annotator body rather than just an attribute list, so
    a test can also build the annotator with NO attributes and get the
    resource's default annotation.
    """
    return load_pipeline_from_yaml(textwrap.dedent(f"""
        - position_score:
            resource_id: scores
{textwrap.indent(textwrap.dedent(body), " " * 12)}
        """), repo)


def _pipeline(
    repo: GenomicResourceRepo, attributes: str,
) -> AnnotationPipeline:
    return _pipeline_over(
        repo, f"attributes:\n{textwrap.dedent(attributes)}")


def test_a_gap_counts_as_the_replacement(repo: GenomicResourceRepo) -> None:
    """The feature, at its smallest: a partly covered region.

    chr1:10-39 is thirty positions -- ten at 1.0, ten at 2.0, and ten no
    record covers.  Unset, the nulls stay inert and every aggregator skips
    them, so ``mean`` averages the twenty covered positions and answers
    1.5.  Set to 0.0, the ten uncovered positions each contribute a zero
    with their own weight, and the mean over all thirty is 1.0.

    The two answers differ, which is what makes this a test of the
    parameter rather than of the fold: an implementation that accepted the
    key and dropped it would still answer 1.5.
    """
    with _pipeline(repo, """
        - source: s
          name: filled
          aggregator: mean
          none_value_replacement: 0.0
    """) as pipeline:
        filled = pipeline.annotate(Region("chr1", 10, 39))

    with _pipeline(repo, """
        - source: s
          name: bare
          aggregator: mean
    """) as pipeline:
        bare = pipeline.annotate(Region("chr1", 10, 39))

    assert filled == {"filled": 1.0}
    assert bare == {"bare": 1.5}


def test_a_region_no_record_touches_is_all_replacement(
    repo: GenomicResourceRepo,
) -> None:
    """The sharpest case the parameter has: a region that is nothing but gap.

    Reachable only since phase 1 (#1131).  The annotator used to ask whether
    any record had been collected and short-circuit to ``None`` for every
    attribute if none had, so a region past the last record never reached
    the fold at all and no replacement could have applied to it.  The score
    answers for itself now, and such a region arrives as one long null run:
    unset, ``mean`` has accumulated nothing and says ``None``; set, every
    position of the span carries the replacement and the mean IS the
    replacement.

    Distinct from the three exits that still answer without folding -- a
    substitution, a chromosome the resource does not carry, and a region
    over the annotator's length cutoff.  Those are out of scope for #1135;
    this one is not, because it reaches the plane.
    """
    with _pipeline(repo, """
        - source: s
          name: filled
          aggregator: mean
          none_value_replacement: 0.5
    """) as pipeline:
        filled = pipeline.annotate(Region("chr1", 100, 110))

    with _pipeline(repo, """
        - source: s
          name: bare
          aggregator: mean
    """) as pipeline:
        bare = pipeline.annotate(Region("chr1", 100, 110))

    assert filled == {"filled": 0.5}
    assert bare == {"bare": None}


def test_one_source_may_be_read_twice_with_and_without_a_replacement(
    repo: GenomicResourceRepo,
) -> None:
    """The replacement belongs to the QUERY, not to the score.

    Both attributes read ``s`` over the same region, and one fetch serves
    them: the score fetches each distinct score once and lets every query
    fold the column it landed in.  What must NOT be shared is the
    replacement -- were it resolved per score rather than per query, both
    attributes would answer the same way and one of the two configurations
    would be silently ignored.

    The same region as the first test, so the two answers are the ones
    already established separately: 1.0 with the gap counted, 1.5 without.
    Here they must come back from ONE annotation.
    """
    with _pipeline(repo, """
        - source: s
          name: filled
          aggregator: mean
          none_value_replacement: 0.0
        - source: s
          name: bare
          aggregator: mean
    """) as pipeline:
        result = pipeline.annotate(Region("chr1", 10, 39))

    assert result == {"filled": 1.0, "bare": 1.5}


def test_a_pipeline_may_clear_a_resource_declared_replacement(
    repo_with_resource_default: GenomicResourceRepo,
) -> None:
    """Writing ``null`` turns a resource's default off, rather than keeping it.

    The resource declares 0.0, so without this an attribute has no way back
    to the plain behaviour except to stop using the default annotation
    entirely.  An explicit ``null`` overrides the default like any other
    value and leaves the nulls inert, which is the 1.5 the same region
    gives when nothing is configured anywhere.
    """
    with _pipeline_over(repo_with_resource_default, """
            attributes:
            - source: s
              name: s
              aggregator: mean
              none_value_replacement: null
    """) as pipeline:
        result = pipeline.annotate(Region("chr1", 10, 39))

    assert result == {"s": 1.5}


def test_a_replacement_the_score_cannot_mean_is_refused_at_load(
    repo: GenomicResourceRepo,
) -> None:
    """Refused building the pipeline, not on the first region.

    The annotator resolves its queries once as it loads -- the same call
    that refuses an attribute with nothing to reduce it -- and the score's
    resolution judges the replacement's type between the score lookup and
    the aggregator lookup.  So a misconfigured replacement is a complaint
    about the pipeline the user wrote, raised before any annotatable
    arrives, and NOT a second check the annotator makes for itself.

    The tail is the score's own message, wrapped by the factory into an
    ``AnnotationConfigurationError`` naming the annotator, exactly as the
    missing-aggregator refusal is.  Pinning the tail is what would catch
    the annotator growing a validation of its own that shadowed the
    score's.
    """
    with pytest.raises(AnnotationConfigurationError) as excinfo:
        _pipeline(repo, """
            - source: s
              name: s
              aggregator: mean
              none_value_replacement: "not a number"
        """)

    assert str(excinfo.value).endswith(
        "none_value_replacement 'not a number' for score 's' of resource "
        "'scores' does not match its value type 'float'")


def test_a_bool_replacement_is_refused_for_a_numeric_score(
    repo: GenomicResourceRepo,
) -> None:
    """``true`` is not a zero, even though Python would let it be one.

    YAML's ``true`` arrives as a Python ``bool``, which is an ``int`` --
    so a check written as ``isinstance(value, (int, float))`` would accept
    it and fold ``True`` into a float score as 1.0.  The score's table
    refuses it deliberately, exactly as a bool-typed score is not a
    numeric one.

    Pinned through the pipeline because the config is where a ``true``
    can be written by accident: nothing else in an attribute's YAML makes
    the reader think a flag belongs in a numeric slot.
    """
    with pytest.raises(AnnotationConfigurationError) as excinfo:
        _pipeline(repo, """
            - source: s
              name: s
              aggregator: mean
              none_value_replacement: true
        """)

    assert str(excinfo.value).endswith(
        "none_value_replacement True for score 's' of resource 'scores' "
        "does not match its value type 'float'")


def test_an_int_replacement_is_accepted_for_a_float_score(
    repo: GenomicResourceRepo,
) -> None:
    """The other side of that boundary, so the refusal cannot over-reach.

    ``0`` is a perfectly good stand-in for a float score and the score's
    table says so.  Were the refusal keyed on "not exactly a float" rather
    than on the bool/numeric split, this would fail too -- and a user
    writing the obvious ``none_value_replacement: 0`` would be refused for
    no reason.
    """
    with _pipeline(repo, """
        - source: s
          name: filled
          aggregator: mean
          none_value_replacement: 0
    """) as pipeline:
        result = pipeline.annotate(Region("chr1", 10, 39))

    assert result == {"filled": 1.0}


def test_a_substitution_does_not_receive_the_replacement(
    repo: GenomicResourceRepo,
) -> None:
    """The scope fence, decided during triage of #1135.

    ``none_value_replacement`` is a field of an aggregation QUERY, and a
    substitution performs no aggregation: it reads a single position and
    answers with what is there.  A read that folds nothing has nothing to
    substitute into, so an uncovered substitution stays ``None`` even with
    a replacement configured -- the same scope ``aggregator`` already has,
    which likewise means nothing for a single position.

    Pinned rather than assumed, because the opposite reading is a natural
    one ("this attribute is never null") and nothing else would catch the
    parameter quietly spreading to the substitution branch when it is
    swept to name keys in phase 5 (#1134).

    The covered substitution is here so the fence cannot pass by breaking
    the path outright: the same pipeline still answers 1.0 where a record
    does cover the position.
    """
    with _pipeline(repo, """
        - source: s
          name: s
          aggregator: mean
          none_value_replacement: 0.5
    """) as pipeline:
        covered = pipeline.annotate(VCFAllele("chr1", 15, "A", "G"))
        uncovered = pipeline.annotate(VCFAllele("chr1", 100, "A", "G"))

    assert covered == {"s": 1.0}
    assert uncovered == {"s": None}


@pytest.fixture
def repo_with_resource_default() -> GenomicResourceRepo:
    """The same score, but declaring the replacement in the RESOURCE.

    Written as raw resource YAML rather than through the builders because
    ``default_annotation`` is what this fixture exists to carry and the
    position-score builder has no spelling for it.
    """
    return build_inmemory_test_repository({
        "scores": {
            "genomic_resource.yaml":
            """\
            type: position_score
            table:
                filename: data.mem
                zero_based: false
            scores:
            - id: s
              type: float
              desc: "a float score"
              name: s
            default_annotation:
              - source: s
                name: s
                aggregator: mean
                none_value_replacement: 0.0
            """,
            "data.mem": """
                chrom  pos_begin  pos_end  s
                chr1   10         19       1.0
                chr1   20         29       2.0
            """,
        },
    })


def test_a_resource_may_declare_the_replacement_and_a_pipeline_override_it(
    repo_with_resource_default: GenomicResourceRepo,
) -> None:
    """Where the value may be written, and which one wins.

    A resource's ``default_annotation`` carries an attribute's parameters
    into the annotator the same way it carries its aggregator, so a score
    whose author knows an uncovered base means zero can say so once, and
    every pipeline that takes the default gets it.  A pipeline attribute
    that names its own replacement overrides it, as it does for every
    other attribute parameter.

    Over chr1:10-39 -- ten at 1.0, ten at 2.0, ten uncovered -- the
    resource's 0.0 gives a mean of 1.0 and the pipeline's 3.0 gives 2.0.
    Both differ from each other AND from the 1.5 an ignored replacement
    would produce, so neither passes by coincidence.
    """
    with _pipeline_over(repo_with_resource_default, "") as pipeline:
        from_resource = pipeline.annotate(Region("chr1", 10, 39))

    with _pipeline_over(repo_with_resource_default, """
            attributes:
            - source: s
              name: s
              aggregator: mean
              none_value_replacement: 3.0
    """) as pipeline:
        overridden = pipeline.annotate(Region("chr1", 10, 39))

    assert from_resource == {"s": 1.0}
    assert overridden == {"s": 2.0}


def test_the_replacement_is_documented_on_the_attribute(
    repo: GenomicResourceRepo,
) -> None:
    """A configured replacement shows up where the aggregator does.

    An attribute's documentation is what the pipeline's generated help
    renders, and it already names the aggregator that reduces the
    attribute.  A replacement changes the answer just as decisively -- it
    is the difference between a CNV's mean over its covered bases and over
    all of them -- so a reader of the help must be able to see that one is
    in force.

    Only when it IS in force: the unset attribute says nothing, rather
    than documenting a ``None`` that would read as a configured value.
    """
    with _pipeline(repo, """
        - source: s
          name: filled
          aggregator: mean
          none_value_replacement: 0.0
        - source: s
          name: bare
          aggregator: mean
    """) as pipeline:
        filled, bare = pipeline.annotators[0].attributes

    assert "**none_value_replacement**: 0.0" in filled.documentation
    assert "none_value_replacement" not in bare.documentation


def test_the_replacement_is_listed_among_the_attribute_properties(
    repo: GenomicResourceRepo,
) -> None:
    """The OTHER documentation surface: the rendered attribute help.

    An attribute is documented twice by two different routes.  The help
    the web API serves for a single attribute renders a properties list
    built by ``build_score_aggregator_documentation``, which is not the
    string ``attr.documentation`` carries -- so appending to one leaves
    the other saying only which aggregator runs, and a reader of the web
    help would see a ``mean`` whose answer they cannot account for.
    """
    with _pipeline(repo, """
        - source: s
          name: filled
          aggregator: mean
          none_value_replacement: 0.0
    """) as pipeline:
        annotator = pipeline.annotators[0]
        properties = annotator.build_score_aggregator_documentation(
            annotator.attributes[0])

    assert "**none_value_replacement**: 0.0" in properties
