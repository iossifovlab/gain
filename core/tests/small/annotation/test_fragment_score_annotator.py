# pylint: disable=W0621,C0114,C0116

import textwrap

import pytest
from gain.annotation.annotatable import Annotatable, Position, Region
from gain.annotation.annotation_config import AnnotationConfigurationError

# VCFAllele
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing import (
    build_inmemory_test_repository,
    convert_to_tab_separated,
)


@pytest.fixture(scope="module")
def grr() -> GenomicResourceRepo:
    return build_inmemory_test_repository({
        "fragments": {
            "genomic_resource.yaml": textwrap.dedent("""
                type: fragment_score
                table:
                  filename: data.mem
                scores:
                - id: frequency
                  name: frequency
                  type: float
                  desc: some populaton frequency
                - id: collection
                  name: collection
                  type: str
                  desc: SSC or AGRE
                - id: affected_status
                  name: affected_status
                  type: str
                  aggregator: join(,)
                  desc: |
                        shows if the child that has the de novo is
                        affected or unaffected
            """),
            "data.mem": convert_to_tab_separated("""
               chrom  pos_begin  pos_end  frequency  collection affected_status
               1      10         20       0.02       SSC        affected
               1      50         100      0.1        AGRE       affected
               2      1          2        0.00001    AGRE       unaffected
               2      16         20       0.3        SSC        affected
               2      200        203      0.0002     AGRE       unaffected
               15     16         20       0.2        AGRE       affected
            """)},
    })


@pytest.fixture(scope="module")
def larger_grr() -> GenomicResourceRepo:
    return build_inmemory_test_repository({
        "fragments": {
            "genomic_resource.yaml": textwrap.dedent("""
                type: fragment_score
                table:
                  filename: data.mem
                scores:
                - id: frequency
                  name: frequency
                  type: int
                  desc: some populaton frequency
                - id: collection
                  name: collection
                  type: str
                  desc: SSC or AGRE
                - id: affected_status
                  name: "affected_status"
                  type: str
                  desc: |
                        shows if the child that has the de novo is
                        affected or unaffected
                - id: size
                  name: size
                  type: int
                  desc: size
            """),
            "data.mem": convert_to_tab_separated("""
chrom  pos_begin  pos_end  frequency  collection  affected_status  size
chr1   10         20       1          SSC         affected         100
chr1   50         100      1          AGRE        affected         130
chr1   1          2        1          AGRE        unaffected       250
chr1   16         20       3          SSC         affected         360
chr1   32         65       2          AGRE        unaffected       560
chr1   15         60       2          AGRE        affected         670
chr1   12         78       3          SSC        unaffected       550
chr1   16         35       4          AGRE        unaffected       300
chr1   25         67       5          SSc        affected         50
chr1   24         35       2          AGRE        affected         900
            """)},
    })


@pytest.mark.parametrize("annotatable, fragment_count", [
    (Position("1", 15), 1),
    (Region("1", 15, 60), 2),
    (Region("1", 30, 40), 0),
])
def test_basic(
        annotatable: Annotatable,
        fragment_count: int, grr: GenomicResourceRepo) -> None:
    pipeline = load_pipeline_from_yaml(
        textwrap.dedent("""
            - fragment_score: fragments
            """),
        grr)

    atts = pipeline.annotate(annotatable)
    assert atts["count"] == fragment_count


@pytest.mark.parametrize("annotatable, fragment_count", [
    (Position("1", 15), 1),
    (Region("1", 15, 60), 1),
    (Region("1", 30, 40), 0),
])
def test_fragment_filter(
        annotatable: Annotatable, fragment_count: int,
        grr: GenomicResourceRepo) -> None:
    pipeline = load_pipeline_from_yaml(
        textwrap.dedent("""
            - fragment_score:
                resource_id: fragments
                fragment_filter: frequency < 0.05 or collection == "SSC"
            """),
        grr)

    atts = pipeline.annotate(annotatable)
    assert atts["count"] == fragment_count


@pytest.mark.parametrize("annotatable, fragment_count", [
    (Position("1", 15), 1),
    (Region("1", 15, 60), 1),
    (Region("1", 30, 40), 0),
])
def test_fragment_filter_in(
        annotatable: Annotatable, fragment_count: int,
        grr: GenomicResourceRepo) -> None:
    pipeline = load_pipeline_from_yaml(
        textwrap.dedent("""
            - fragment_score:
                resource_id: fragments
                fragment_filter: '"SSC" in collection'
            """),
        grr)

    atts = pipeline.annotate(annotatable)
    assert atts["count"] == fragment_count


@pytest.mark.parametrize("annotatable, fragment_count", [
    (Position("1", 15), 1),
    (Region("1", 15, 60), 1),
    (Region("1", 30, 40), 0),
])
def test_fragment_filter_on_newline(
        annotatable: Annotatable, fragment_count: int,
        grr: GenomicResourceRepo) -> None:
    pipeline = load_pipeline_from_yaml(
        textwrap.dedent("""
            - fragment_score:
                resource_id: fragments
                fragment_filter: >
                    frequency < 0.05 or collection == "SSC"
            """),
        grr)

    atts = pipeline.annotate(annotatable)
    assert atts["count"] == fragment_count


@pytest.fixture(scope="module")
def digit_named_grr() -> GenomicResourceRepo:
    """A fragment score named the way real population scores are.

    A leading-digit name is what the fragment grammar could not express;
    ``1000G`` is the shape the GRR actually publishes.
    """
    return build_inmemory_test_repository({
        "fragments": {
            "genomic_resource.yaml": textwrap.dedent("""
                type: fragment_score
                table:
                  filename: data.mem
                scores:
                - id: 1000G
                  name: 1000G
                  type: float
                  desc: a population frequency
            """),
            "data.mem": convert_to_tab_separated("""
               chrom  pos_begin  pos_end  1000G
               1      10         20       0.02
               1      50         100      0.10
            """)},
    })


def test_fragment_filter_takes_digit_names_and_negative_numbers(
    digit_named_grr: GenomicResourceRepo,
) -> None:
    """This annotator reaches the shared compiler, not its own grammar.

    Both halves of the expression were unwritable on the fragment side
    before: its ``word`` was letters-only and its ``number`` unsigned.  The
    grammar itself is pinned where it lives, in ``test_score_filter``; what
    this adds is that a fragment pipeline really routes there.
    """
    pipeline = load_pipeline_from_yaml(
        textwrap.dedent("""
            - fragment_score:
                resource_id: fragments
                fragment_filter: "1000G > 0.05 and 1000G > -1"
            """),
        digit_named_grr)

    atts = pipeline.annotate(Region("1", 1, 200))
    assert atts["count"] == 1


@pytest.mark.parametrize("parameter", [
    "fragment_filter",
    # Deliberately the legacy spelling: what is pinned is that the message
    # names whichever one was written.
    pytest.param("cnv_filter", marks=pytest.mark.legacy_vocabulary),
])
def test_fragment_filter_naming_an_unknown_score_is_refused(
    parameter: str, grr: GenomicResourceRepo,
) -> None:
    """Refused while the pipeline builds, under the spelling the user wrote.

    Both spellings are one parameter, and whichever one the configuration
    says is the one named back -- reporting the other sends the author
    looking for a key that appears nowhere in their file (gain#477).
    """
    with pytest.raises(AnnotationConfigurationError) as excinfo:
        load_pipeline_from_yaml(
            textwrap.dedent(f"""
                - fragment_score:
                    resource_id: fragments
                    {parameter}: 'freq < 0.05'
                """),
            grr)

    message = str(excinfo.value)
    assert f"Error parsing {parameter}" in message
    assert "'frequency'" in message


@pytest.mark.parametrize(
    "annotatable, fragment_count, status, status2, collection", [
        (Position("1", 15), 1, "affected", "affected", "SSC"),
        (Region("1", 15, 60), 2,
         "affected,affected", "affected", "SSC,AGRE"),
        (Region("1", 30, 40), 0, None, None, None),
    ])
def test_fragment_filter_and_attribute(
    annotatable: Annotatable, fragment_count: int,
    status: str, status2: str, collection: str,
    grr: GenomicResourceRepo,
) -> None:
    pipeline = load_pipeline_from_yaml(
        textwrap.dedent("""
            - fragment_score:
                resource_id: fragments
                fragment_filter: frequency < 0.05 or collection == "AGRE"
                attributes:
                - count
                - name: status
                  source: "affected_status"
                - name: status2
                  source: "affected_status"
                  aggregator: mode
                - source: "collection"
            """),
        grr)

    atts = pipeline.annotate(annotatable)
    assert "status" in atts
    assert "status2" in atts
    assert "collection" in atts

    assert atts["count"] == fragment_count
    assert atts["status"] == status
    assert atts["status2"] == status2
    assert atts["collection"] == collection

    status_info = pipeline.get_attribute_info("status")
    status2_info = pipeline.get_attribute_info("status2")
    collection_info = pipeline.get_attribute_info("collection")
    assert status_info is not None
    assert status2_info is not None
    assert collection_info is not None

    assert "aggregator: join(,)" in status_info.documentation
    assert "aggregator: mode" in status2_info.documentation
    assert "aggregator: join(,)" in collection_info.documentation


def test_fragment_aggregators(
    larger_grr: GenomicResourceRepo,
) -> None:
    pipeline = load_pipeline_from_yaml(
        textwrap.dedent("""
            - fragment_score:
                resource_id: fragments
                attributes:
                - count
                - name: size_max
                  source: "size"
                  aggregator: max
                - name: affected_status_count
                  source: "affected_status"
                  aggregator: count
                - name: size_median
                  source: "size"
                  aggregator: median
                - name: frequency_median
                  source: "frequency"
                  aggregator: median
                - name: frequency_list
                  source: "frequency"
                  aggregator: list
                - name: size_mode
                  source: "size"
                  aggregator: mode
                - name: collection_join
                  source: "collection"
                  aggregator: join(;)
            """),
        larger_grr)
    annotatable = Region("chr1", 1, 100)
    atts = pipeline.annotate(annotatable)

    assert "count" in atts
    assert "size_max" in atts
    assert "affected_status_count" in atts
    assert "size_median" in atts
    assert "frequency_median" in atts
    assert "frequency_list" in atts
    assert "size_mode" in atts
    assert "collection_join" in atts

    assert atts["count"] == 10
    assert atts["size_max"] == 900
    assert atts["affected_status_count"] == 10
    assert atts["size_median"] == 330.0
    assert atts["frequency_median"] == 2.0
    assert atts["frequency_list"] == [1, 1, 3, 2, 3, 4, 2, 5, 2, 1]
    assert atts["size_mode"] == 50
    assert atts["collection_join"] == \
        "AGRE;SSC;SSC;AGRE;SSC;AGRE;AGRE;SSc;AGRE;AGRE"


# The two overlap fractions (gain#1125).
#
# Every case below annotates `Region("1", 15, 60)` against the `grr`
# fixture, whose chromosome 1 carries exactly two fragments.  The
# arithmetic that region sets up is the whole point of using it:
#
#     region [15, 60]         length 46
#     fragment [10, 20]       overlap [15, 20] =  6  ->  6/46 = 0.130 of the
#                                                        region,
#                                                        6/11 = 0.545 of the
#                                                        fragment
#     fragment [50, 100]      overlap [50, 60] = 11  -> 11/46 = 0.239 of the
#                                                        region,
#                                                       11/51 = 0.216 of the
#                                                        fragment
#
# So a fragment-denominated 0.5 keeps the FIRST fragment and a
# region-denominated 0.2 keeps the SECOND.  The two thresholds select
# DIFFERENT fragments here, deliberately: an implementation that passed
# them to the score the other way round would still answer `count == 1`,
# and only the attribute value tells the two apart.  That is why these
# assert `frequency` and not just the count -- getting the fractions
# backwards is a silent wrong answer rather than an error.


def test_fragment_overlap_fraction_keeps_the_fragment_mostly_inside(
    grr: GenomicResourceRepo,
) -> None:
    pipeline = load_pipeline_from_yaml(
        textwrap.dedent("""
            - fragment_score:
                resource_id: fragments
                min_fragment_overlap_fraction: 0.5
                attributes:
                - count
                - source: frequency
                  aggregator: join(;)
            """),
        grr)

    atts = pipeline.annotate(Region("1", 15, 60))

    assert atts["count"] == 1
    assert atts["frequency"] == "0.02"


def test_region_overlap_fraction_keeps_the_fragment_covering_the_region(
    grr: GenomicResourceRepo,
) -> None:
    """The other denominator selects the OTHER fragment.

    Paired with the test above: same region, same resource, a threshold
    that admits only what the fragment-denominated one rejects.  Swapping
    the two parameters anywhere between here and the score turns this red.
    """
    pipeline = load_pipeline_from_yaml(
        textwrap.dedent("""
            - fragment_score:
                resource_id: fragments
                min_region_overlap_fraction: 0.2
                attributes:
                - count
                - source: frequency
                  aggregator: join(;)
            """),
        grr)

    atts = pipeline.annotate(Region("1", 15, 60))

    assert atts["count"] == 1
    assert atts["frequency"] == "0.1"


def test_both_fractions_unset_admits_every_overlapping_fragment(
    grr: GenomicResourceRepo,
) -> None:
    """Unset is not 0.0 -- it is no threshold at all.

    The baseline the two tests above are measured against: the same
    region, no fractions configured, both fragments answered.
    """
    pipeline = load_pipeline_from_yaml(
        textwrap.dedent("""
            - fragment_score:
                resource_id: fragments
                attributes:
                - count
                - source: frequency
                  aggregator: join(;)
            """),
        grr)

    atts = pipeline.annotate(Region("1", 15, 60))

    assert atts["count"] == 2
    assert atts["frequency"] == "0.02;0.1"


@pytest.mark.parametrize("parameter", [
    "min_region_overlap_fraction",
    "min_fragment_overlap_fraction",
])
def test_a_fraction_outside_the_unit_interval_is_refused_by_name(
    parameter: str, grr: GenomicResourceRepo,
) -> None:
    """Refused as the PIPELINE LOADS, naming the key the user wrote.

    The score guards the same range, but it raises `ValueError` on the
    first annotated variant -- too late, and addressed to a caller rather
    than to whoever wrote the YAML.  gain#477: an error naming a key
    absent from their config sends them looking in the wrong place.
    """
    with pytest.raises(AnnotationConfigurationError, match=parameter):
        load_pipeline_from_yaml(
            textwrap.dedent(f"""
                - fragment_score:
                    resource_id: fragments
                    {parameter}: 1.5
                """),
            grr)


@pytest.mark.parametrize("configured", ["half", "true"])
def test_a_non_numeric_fraction_is_refused_by_name(
    configured: str, grr: GenomicResourceRepo,
) -> None:
    """A fraction has to be a number, and `true` is not one of them.

    YAML reads `true` as a bool, and `bool` is a subclass of `int`, so a
    range check alone would admit it as 1.0 -- full containment, a
    threshold nobody asked for, applied silently.  It is refused instead.
    """
    with pytest.raises(
            AnnotationConfigurationError,
            match="min_region_overlap_fraction"):
        load_pipeline_from_yaml(
            textwrap.dedent(f"""
                - fragment_score:
                    resource_id: fragments
                    min_region_overlap_fraction: {configured}
                """),
            grr)
