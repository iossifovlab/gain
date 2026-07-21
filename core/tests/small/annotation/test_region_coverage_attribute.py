# pylint: disable=redefined-outer-name,C0114,C0116,protected-access
# pylint: disable=c-extension-no-member
"""How much of the queried region actually carried a score (#266).

A ``mean`` over a 500 kb CNV of which 5% is scored and one of which 100%
is scored are reported identically -- the value alone says nothing about
how much data stood behind it.  A position score therefore offers, for
every score it defines, an opt-in ``<score>_coverage`` attribute: the
number of base pairs of the annotated region that carried a value.

The tests below pin the *number*, not the machinery that produces it.
"""

import pathlib
import textwrap

import pyBigWig
import pytest
from gain.annotation.annotatable import (
    Annotatable,
    CNVAllele,
    Region,
    VCFAllele,
)
from gain.annotation.annotation_config import AnnotationConfigurationError
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.annotation.annotation_pipeline import AnnotationPipeline
from gain.annotation.score_annotator import GenomicScoreAnnotatorBase
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing.builders import (
    a_bigwig_score,
    a_grr,
    a_position_score,
)


@pytest.fixture
def fixture_repo(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    """A score with a 10 bp gap: chr1 10-19 and chr1 30-39 are scored."""
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
                chr1   30         39       3.0
            """),
        )
        .with_resource(
            "position_score_na",
            a_position_score()
            .with_score("test100way", "float", column_name="100way",
                        desc="test values")
            .with_na_values("-1")
            .with_data("""
                chrom  pos_begin  pos_end  100way
                chr1   10         19       1.0
                chr1   20         29       -1
                chr1   30         39       3.0
            """),
        )
        .build_repo(tmp_path)
    )


def _pipeline(
    fixture_repo: GenomicResourceRepo, attributes: str,
    resource_id: str = "position_score1",
) -> AnnotationPipeline:
    return load_pipeline_from_yaml(textwrap.dedent(f"""
        - position_score:
            resource_id: {resource_id}
            attributes:
            {attributes}
        """), fixture_repo)


def test_coverage_counts_the_scored_base_pairs_of_the_region(
    fixture_repo: GenomicResourceRepo,
) -> None:
    with _pipeline(fixture_repo, """
            - source: test100way
              name: test100
            - source: test100way_coverage
              name: covered
    """) as pipeline:
        result = pipeline.annotate(Region("chr1", 10, 39))

    assert result["covered"] == 20


def test_a_pipeline_that_does_not_ask_for_coverage_is_unchanged(
    fixture_repo: GenomicResourceRepo,
) -> None:
    with load_pipeline_from_yaml(textwrap.dedent("""
        - position_score:
            resource_id: position_score1
        """), fixture_repo) as pipeline:
        result = pipeline.annotate(Region("chr1", 10, 39))

    assert set(result) == {"test100way"}
    assert result["test100way"] == 2.0


def test_an_uncovered_region_reports_zero_rather_than_no_value(
    fixture_repo: GenomicResourceRepo,
) -> None:
    with _pipeline(fixture_repo, """
            - source: test100way
              name: test100
            - source: test100way_coverage
              name: covered
    """) as pipeline:
        result = pipeline.annotate(Region("chr1", 20, 29))

    assert result["test100"] is None
    assert result["covered"] == 0


def test_coverage_can_be_asked_for_without_the_value_it_measures(
    fixture_repo: GenomicResourceRepo,
) -> None:
    with _pipeline(fixture_repo, """
            - source: test100way_coverage
              name: covered
    """) as pipeline:
        result = pipeline.annotate(Region("chr1", 10, 39))

    assert result == {"covered": 20}


def test_a_record_counts_only_the_bases_it_shares_with_the_region(
    fixture_repo: GenomicResourceRepo,
) -> None:
    """chr1 15-34 overlaps 10-19 by 5 bp and 30-39 by 5 bp."""
    with _pipeline(fixture_repo, """
            - source: test100way_coverage
              name: covered
    """) as pipeline:
        result = pipeline.annotate(Region("chr1", 15, 34))

    assert result["covered"] == 10


@pytest.mark.parametrize("aggregator", [
    "mean", "max", "min", "median", "count", "mode",
    "list", "join(;)", "concatenate", "bool", "value_count",
])
def test_coverage_does_not_depend_on_how_the_value_is_summarised(
    fixture_repo: GenomicResourceRepo, aggregator: str,
) -> None:
    with _pipeline(fixture_repo, f"""
            - source: test100way
              name: test100
              aggregator: {aggregator}
            - source: test100way_coverage
              name: covered
    """) as pipeline:
        result = pipeline.annotate(Region("chr1", 10, 39))

    assert result["covered"] == 20


def test_a_record_carrying_the_na_sentinel_is_not_covered(
    fixture_repo: GenomicResourceRepo,
) -> None:
    """chr1 20-29 has a record, but its value is the NA sentinel."""
    with _pipeline(fixture_repo, """
            - source: test100way_coverage
              name: covered
    """, resource_id="position_score_na") as pipeline:
        result = pipeline.annotate(Region("chr1", 10, 39))

    assert result["covered"] == 20


@pytest.mark.parametrize("position,expected", [
    (15, 1),
    (25, 0),
])
def test_a_substitution_covers_one_base_when_it_is_scored(
    fixture_repo: GenomicResourceRepo, position: int, expected: int,
) -> None:
    with _pipeline(fixture_repo, """
            - source: test100way_coverage
              name: covered
    """) as pipeline:
        result = pipeline.annotate(VCFAllele("chr1", position, "A", "C"))

    assert result["covered"] == expected


def test_a_coverage_attribute_refuses_an_aggregator(
    fixture_repo: GenomicResourceRepo,
) -> None:
    """A coverage is a total; there is nothing for an aggregator to choose."""
    with pytest.raises(
        AnnotationConfigurationError, match="does not support aggregation",
    ):
        _pipeline(fixture_repo, """
            - source: test100way_coverage
              name: covered
              aggregator: max
        """)


def test_each_annotatable_is_measured_on_its_own(
    fixture_repo: GenomicResourceRepo,
) -> None:
    """Consecutive calls must not accumulate into one running total."""
    with _pipeline(fixture_repo, """
            - source: test100way_coverage
              name: covered
    """) as pipeline:
        first = pipeline.annotate(Region("chr1", 10, 39))
        second = pipeline.annotate(Region("chr1", 15, 34))
        third = pipeline.annotate(Region("chr1", 20, 29))

    assert [first["covered"], second["covered"], third["covered"]] \
        == [20, 10, 0]


def test_a_batch_is_measured_one_annotatable_at_a_time(
    fixture_repo: GenomicResourceRepo,
) -> None:
    """The path a file annotation takes: one figure per row, not a total."""
    with _pipeline(fixture_repo, """
            - source: test100way
              name: test100
            - source: test100way_coverage
              name: covered
    """) as pipeline:
        results = pipeline.batch_annotate([
            Region("chr1", 10, 39),
            Region("chr1", 15, 34),
            Region("chr1", 20, 29),
        ])

    assert [result["covered"] for result in results] == [20, 10, 0]


def test_a_coverage_attribute_is_declared_as_an_integer(
    fixture_repo: GenomicResourceRepo,
) -> None:
    """What a tabular or VCF writer reads to type the output column."""
    with _pipeline(fixture_repo, """
            - source: test100way_coverage
              name: covered
    """) as pipeline:
        attribute = pipeline.get_attributes()[0]

    assert attribute.name == "covered"
    assert attribute.get_value_type() == "int"


def test_a_coverage_attribute_has_help_of_its_own(
    fixture_repo: GenomicResourceRepo,
) -> None:
    """The help the web API renders for an attribute must not blow up."""
    with _pipeline(fixture_repo, """
            - source: test100way_coverage
              name: covered
    """) as pipeline:
        annotator = pipeline.annotators[0]
        assert isinstance(annotator, GenomicScoreAnnotatorBase)
        help_text = annotator.build_attribute_help(annotator.attributes[0])

    assert "covered" in help_text
    assert "base pairs" in help_text


def _cutoff_pipeline(
    fixture_repo: GenomicResourceRepo, cutoff: int,
) -> AnnotationPipeline:
    return load_pipeline_from_yaml(textwrap.dedent(f"""
        - position_score:
            resource_id: position_score1
            region_length_cutoff: {cutoff}
            attributes:
            - source: test100way
              name: test100
            - source: test100way_coverage
              name: covered
        """), fixture_repo)


def test_a_region_too_long_to_measure_reports_no_coverage(
    fixture_repo: GenomicResourceRepo,
) -> None:
    """Above the cutoff nothing is looked up, so nothing can be claimed.

    chr1 10-39 is fully scored except for its 20-29 gap, so a confident
    ``0`` here would be plainly false -- and false in exactly the way that
    matters, since the cutoff exists for the large CNVs this attribute was
    added to describe.
    """
    with _cutoff_pipeline(fixture_repo, cutoff=5) as pipeline:
        result = pipeline.annotate(Region("chr1", 10, 39))

    assert result["test100"] is None
    assert result["covered"] is None


def test_the_cutoff_boundary_separates_a_measurement_from_no_measurement(
    fixture_repo: GenomicResourceRepo,
) -> None:
    """One base either side of the cutoff: a figure, then nothing."""
    with _cutoff_pipeline(fixture_repo, cutoff=10) as pipeline:
        measured = pipeline.annotate(Region("chr1", 10, 19))
        unmeasured = pipeline.annotate(Region("chr1", 10, 20))

    assert measured["covered"] == 10
    assert unmeasured["covered"] is None


def test_a_cnv_too_long_to_measure_reports_no_coverage(
    fixture_repo: GenomicResourceRepo,
) -> None:
    with _cutoff_pipeline(fixture_repo, cutoff=5) as pipeline:
        result = pipeline.annotate(CNVAllele(
            "chr1", 10, 39, Annotatable.Type.LARGE_DUPLICATION))

    assert result["covered"] is None


def test_a_missing_annotatable_reports_no_coverage(
    fixture_repo: GenomicResourceRepo,
) -> None:
    """There is no region to measure, so there is no coverage to report."""
    with _pipeline(fixture_repo, """
            - source: test100way_coverage
              name: covered
    """) as pipeline:
        result = pipeline.annotate(None)

    assert result["covered"] is None


def test_a_batch_reports_no_coverage_for_a_missing_annotatable(
    fixture_repo: GenomicResourceRepo,
) -> None:
    with _pipeline(fixture_repo, """
            - source: test100way_coverage
              name: covered
    """) as pipeline:
        results = pipeline.batch_annotate([
            Region("chr1", 10, 39),
            None,
            Region("chr1", 20, 29),
        ])

    assert [result["covered"] for result in results] == [20, None, 0]


def test_a_bigwig_backed_score_agrees_with_a_native_coverage_query(
    tmp_path: pathlib.Path,
) -> None:
    """The same figure ``pyBigWig.stats(type="coverage")`` reports.

    A bigWig computes coverage natively, and #263 may one day push the
    query down to it.  Whichever end computes it, the answer must be the
    same number -- so the attribute is a base-pair count, which a native
    coverage *fraction* over a region of known length reproduces exactly.
    """
    repo = (
        a_grr()
        .with_resource(
            "bigwig_score",
            a_bigwig_score()
            .with_score("test100way")
            .with_data("""
                chr1  9   19  1.0
                chr1  29  39  3.0
            """)
            .with_chrom_lens({"chr1": 1000}),
        )
        .build_repo(tmp_path)
    )

    with _pipeline(repo, """
            - source: test100way_coverage
              name: covered
    """, resource_id="bigwig_score") as pipeline:
        result = pipeline.annotate(Region("chr1", 10, 39))

    bigwig = pyBigWig.open(str(tmp_path / "bigwig_score" / "data.bw"))
    try:
        # bedGraph intervals are 0-based half-open: 1-based chr1:10-39.
        fraction = bigwig.stats("chr1", 9, 39, type="coverage")[0]
    finally:
        bigwig.close()

    assert result["covered"] == 20
    assert round(fraction * (39 - 9)) == result["covered"]
